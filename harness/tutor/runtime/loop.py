"""
runtime/loop.py — the turn engine. One child message in, one reviewed reply out.

Model calls per turn, by path:
  crisis screen hit      0   fixed reply + adult flag
  acknowledgement        2   reply + review            (planner skipped)
  normal                 3   plan + reply + review
  + unforeseen tool      +1  one tool round, then tool_choice=none forces text
  + review rejection     +2  one revised reply + its review, then fail closed

Every bound above is enforced in code, not left to the model.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from ..context import memory
from ..context.assembler import Context, build_context
from ..context.cues import read_cues
from ..context.planner import make_plan
from ..context.prompts import Prompts
from ..context.router import choose_tier
from .config import Settings
from .db import Store
from .llm import LLM, LLMResult
from .privacy import pseudonymize, scrub
from .safety import CRISIS_REPLIES, FALLBACK_REPLY, review_reply, screen_input
from .tools import TOOL_DEFS, ToolExecutor, TurnIds

log = logging.getLogger("tutor.loop")


class UnknownLearner(Exception):
    """Closed roster: only teacher-enrolled learners have a learner_state row."""


@dataclass
class TurnRequest:
    learner_id: str                          # opaque roster id — never a name
    session_id: str
    message: str
    seconds_since_last_reply: float | None = None


@dataclass
class TurnResponse:
    reply: str
    meta: dict = field(default_factory=dict)   # path, plan, tier, call count — no child text


@dataclass
class _Calls:
    """Per-turn ledger of model calls, for the budget and the response meta."""
    items: list[tuple[str, LLMResult]] = field(default_factory=list)

    def add(self, purpose: str, result: LLMResult | None) -> None:
        if result is not None:
            self.items.append((purpose, result))

    @property
    def budget_tokens(self) -> int:
        return sum(r.budget_tokens for _, r in self.items)

    def summary(self) -> dict:
        return {"model_calls": len(self.items),
                "calls": [{"purpose": p, "model": r.model, "in": r.input_tokens, "out": r.output_tokens,
                           "cache_read": r.cache_read_tokens, "cache_write": r.cache_write_tokens}
                          for p, r in self.items]}


class TurnEngine:
    def __init__(self, settings: Settings, llm: LLM, store: Store, prompts: Prompts):
        self.s, self.llm, self.store, self.prompts = settings, llm, store, prompts
        self.tools = ToolExecutor(store)

    # ------------------------------------------------------------------ main
    def handle(self, req: TurnRequest) -> TurnResponse:
        now = time.time()
        pid = pseudonymize(req.learner_id, self.s.pseudonym_pepper)
        state = self.store.load_state(pid)
        if state is None:
            raise UnknownLearner()
        ids = TurnIds(pid, req.session_id, state["class_id"])
        calls = _Calls()

        # 1. GATE 1 — deterministic floor screen, before any model sees the text.
        crisis = screen_input(req.message)
        if crisis:
            self.store.flag_for_adult(pid, req.session_id, "high", f"input screen: {crisis}")
            self.store.audit("crisis_screen", pid, {"category": crisis})
            reply = CRISIS_REPLIES[crisis]
            self._persist_turn(ids, req.message, reply, None, 0)
            return TurnResponse(reply, {"path": "crisis", "model_calls": 0})

        # 2. CUES — deterministic read of the child's signals.
        cues = read_cues(req.message, req.seconds_since_last_reply, state)
        history = self.store.recent_turns(req.session_id, 12)

        # 3. PLAN — one cheap call (or none on the fast path).
        small = self.s.tiers[self.s.planner_tier].model_id
        plan, plan_res = make_plan(self.llm, small, self.prompts.planner, state, cues, history, req.message)
        calls.add("plan", plan_res)
        if plan.risk == "high":
            self.store.flag_for_adult(pid, req.session_id, "medium", f"planner: {plan.risk_reason}")

        # 4. ROUTE — pick the Claude tier for the reply.
        route = choose_tier(plan, cues, state, self.store.tokens_today(pid), self.s, now)

        # 5. PREFETCH — retrieval the plan asked for, done by the harness, not by model tool calls.
        memories = memory.recall(self.store, pid, plan.memory_query) if plan.needs_memory else []
        curriculum = (self.store.search_curriculum(ids.class_id, plan.curriculum_query, 3)
                      if plan.needs_curriculum else [])

        # 6. ASSEMBLE — cache-friendly, budgeted context.
        ctx = build_context(system_prompt=self.prompts.tutor_system, age_band=state["age_band"], state=state,
                            cues=cues, plan=plan, memories=memories, curriculum=curriculum, history=history,
                            message=req.message, budget_tokens=self.s.context_budget_tokens)

        # 7. GENERATE — at most one tool round, and only if the plan allowed tools.
        draft = self._generate(route.model_id, ctx, plan.allow_tools, ids, calls)

        # 8. GATE 2 — review; one bounded retry; fail closed.
        reply, review = self._review(state["age_band"], req.message, draft, plan.move, ctx, route.model_id, ids, calls)

        # 9. LEARN + PERSIST
        if review and review.needs_adult:
            self.store.flag_for_adult(pid, req.session_id, "medium", f"reviewer: {review.adult_reason}")
        if review:
            memory.update_mastery(state, review.mastery_skill, review.mastery_correct)
            memory.keep(self.store, pid, review.memory_note)
        state["affect"] = cues.affect
        state["router"] = route.router_state
        if plan.topic:
            state["current_topic"] = plan.topic
        self.store.save_state(pid, state)
        self.store.add_usage(pid, calls.budget_tokens)
        self._persist_turn(ids, req.message, reply, route.model_id, calls.budget_tokens)
        self.store.audit("turn", pid, {"move": plan.move, "plan_source": plan.source, "tier": route.tier,
                                       "route_reason": route.reason, "dropped_turns": ctx.dropped_turns,
                                       **calls.summary()})

        return TurnResponse(reply, {"path": "normal", "move": plan.move, "plan_source": plan.source,
                                    "tier": route.tier, "route_reason": route.reason, **calls.summary()})

    # --------------------------------------------------------------- helpers
    def _generate(self, model: str, ctx: Context, allow_tools: bool, ids: TurnIds, calls: _Calls) -> str:
        """Reply call. Tools are always DEFINED (cache-stable) but only ENABLED when the plan allows."""
        messages = list(ctx.messages)
        tool_choice = {"type": "auto"} if allow_tools else {"type": "none"}
        max_tokens = next(t.max_output_tokens for t in self.s.tiers.values() if t.model_id == model)
        for round_no in range(self.s.max_tool_rounds + 1):
            res = self.llm.complete(model=model, system=ctx.system, messages=messages, max_tokens=max_tokens,
                                    tools=TOOL_DEFS, tool_choice=tool_choice)
            calls.add("reply" if round_no == 0 else "reply_after_tool", res)
            if not res.tool_calls or round_no == self.s.max_tool_rounds:
                return res.text
            results = [{"type": "tool_result", "tool_use_id": c.id, "content": self.tools.run(c, ids)}
                       for c in res.tool_calls]
            messages += [{"role": "assistant", "content": res.raw_content}, {"role": "user", "content": results}]
            tool_choice = {"type": "none"}   # hard cap: after one tool round the model must answer in text
        return ""

    def _review(self, age_band, child_msg, draft, move, ctx, model, ids, calls):
        """Return (reply_to_send, review_or_None). Two strikes or any error -> FALLBACK_REPLY."""
        reviewer = self.s.tiers[self.s.reviewer_tier].model_id
        try:
            for attempt in range(2):
                if not draft:
                    break
                review, res = review_reply(self.llm, reviewer, self.prompts.reviewer, age_band,
                                           child_msg, draft, move)
                calls.add("review", res)
                if review.approved:
                    return draft, review
                self.store.audit("review_reject", ids.pseudonym, {"attempt": attempt, "issues": review.issues[:5]})
                if attempt == 0:
                    draft = self._generate(model, ctx.with_revision_note(review.issues), False, ids, calls)
        except Exception as exc:   # reviewer outage must never let an unreviewed draft through
            log.warning("review failed: %s", type(exc).__name__)
            self.store.audit("review_error", ids.pseudonym, {"error": type(exc).__name__})
        return FALLBACK_REPLY, None

    def _persist_turn(self, ids: TurnIds, child_msg: str, reply: str, model: str | None, tokens: int) -> None:
        """Both sides of the turn, scrubbed. Raw child text is never written."""
        self.store.append_turn(ids.session_id, ids.pseudonym, "child", scrub(child_msg), None, 0)
        self.store.append_turn(ids.session_id, ids.pseudonym, "tutor", scrub(reply), model, tokens)
