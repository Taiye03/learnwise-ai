"""
tests/fakes.py — in-memory stand-ins for the two runtime boundaries (LLM, Store).

They implement the same methods as ClaudeClient and OracleStore, so the
real TurnEngine runs unmodified. This is the payoff of keeping every network
and database call behind those two interfaces.
"""
from __future__ import annotations

import itertools
from collections import defaultdict

from tutor.runtime.config import ModelTier, Settings
from tutor.runtime.llm import LLMResult, ToolCall


def make_settings(**overrides) -> Settings:
    tiers = {
        "small": ModelTier("small", "claude-haiku-5-5", 1, 600),
        "medium": ModelTier("medium", "claude-sonnet-5-5", 3, 600),
        "large": ModelTier("large", "claude-opus-5-5", 5, 600),
    }
    base = dict(anthropic_api_key="x", oracle_user="u", oracle_password="p", oracle_dsn="d",
                pseudonym_pepper=b"test-pepper", api_bearer_token="t", tiers=tiers)
    base.update(overrides)
    return Settings(**base)


class FakeLLM:
    """
    Scripted model. Forced-tool calls (planner/reviewer) pop from their queues;
    free reply calls pop from `replies`, where an item is either text or a
    ToolCall (to simulate the tutor asking for a tool).
    """

    def __init__(self, plans=None, reviews=None, replies=None):
        self.plans = list(plans or [])
        self.reviews = list(reviews or [])
        self.replies = list(replies or [])
        self.calls: list[dict] = []
        self._ids = itertools.count()

    def complete(self, *, model, system, messages, max_tokens, tools=None, tool_choice=None):
        self.calls.append({"model": model, "system": system, "messages": messages,
                           "tools": tools, "tool_choice": tool_choice})
        forced = (tool_choice or {}).get("name")
        if forced == "submit_plan":
            return self._tool_result(model, "submit_plan", self.plans.pop(0))
        if forced == "submit_review":
            return self._tool_result(model, "submit_review", self.reviews.pop(0))
        item = self.replies.pop(0)
        if isinstance(item, ToolCall):
            return LLMResult(model, "", [item], "tool_use", 100, 20,
                             raw_content=[{"type": "tool_use", "id": item.id, "name": item.name, "input": item.input}])
        return LLMResult(model, item, [], "end_turn", 100, 30, cache_read_tokens=500,
                         raw_content=[{"type": "text", "text": item}])

    def _tool_result(self, model, name, data):
        call = ToolCall(f"tc{next(self._ids)}", name, data)
        return LLMResult(model, "", [call], "tool_use", 80, 40)

    def purposes(self) -> list[str]:
        out = []
        for c in self.calls:
            forced = (c["tool_choice"] or {}).get("name")
            out.append({"submit_plan": "plan", "submit_review": "review"}.get(forced, "reply"))
        return out


class FakeStore:
    def __init__(self):
        self.states: dict[str, dict] = {}
        self.turns: list[dict] = []
        self.memory: dict[str, list[str]] = defaultdict(list)
        self.curriculum: dict[str, list[str]] = defaultdict(list)
        self.usage: dict[str, int] = defaultdict(int)
        self.flags: list[dict] = []
        self.audits: list[dict] = []

    def enroll(self, pseudonym, class_id="class-demo", age_band="8-10", **state):
        self.states[pseudonym] = {"class_id": class_id, "age_band": age_band, **state}

    def load_state(self, pseudonym):
        s = self.states.get(pseudonym)
        return None if s is None else {**s}

    def save_state(self, pseudonym, state):
        self.states[pseudonym] = {**state}

    def recent_turns(self, session_id, limit):
        rows = [(t["role"], t["text"]) for t in self.turns if t["session_id"] == session_id]
        return rows[-limit:]

    def append_turn(self, session_id, pseudonym, role, text, model, tokens):
        self.turns.append({"session_id": session_id, "pseudonym": pseudonym, "role": role,
                           "text": text, "model": model, "tokens": tokens})

    def search_memory(self, pseudonym, query, k):
        return self.memory[pseudonym][:k]

    def add_memory(self, pseudonym, kind, note):
        self.memory[pseudonym].append(note)

    def search_curriculum(self, class_id, query, k):
        return self.curriculum[class_id][:k]

    def tokens_today(self, pseudonym):
        return self.usage[pseudonym]

    def add_usage(self, pseudonym, tokens):
        self.usage[pseudonym] += tokens

    def flag_for_adult(self, pseudonym, session_id, severity, reason):
        self.flags.append({"pseudonym": pseudonym, "severity": severity, "reason": reason})

    def audit(self, event, pseudonym, detail):
        self.audits.append({"event": event, "pseudonym": pseudonym, "detail": detail})


PLAN_NORMAL = {"move": "hint", "topic": "adding fractions", "needs_memory": True, "memory_query": "fractions",
               "needs_curriculum": True, "curriculum_query": "fractions", "allow_tools": False,
               "complexity": 3, "risk": "none", "rationale": "child is close; nudge on common denominators"}
REVIEW_OK = {"approved": True, "issues": [], "needs_adult": False, "mastery_skill": "adding fractions",
             "mastery_correct": False, "memory_note": "Responds well to pizza-slice examples for fractions."}
REVIEW_BAD = {"approved": False, "issues": ["gives away the answer"], "needs_adult": False,
              "mastery_skill": "", "memory_note": ""}
