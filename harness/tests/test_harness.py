"""
tests/test_harness.py — run with:  python -m unittest -v
Each test names the property it proves. No network, no database.
"""
import unittest

from tests.fakes import (PLAN_NORMAL, REVIEW_BAD, REVIEW_OK, FakeLLM, FakeStore, make_settings)
from tutor.context.assembler import build_context, est_tokens
from tutor.context.cues import read_cues
from tutor.context.planner import Plan
from tutor.context.prompts import load_prompts
from tutor.context.router import choose_tier
from tutor.runtime.llm import ToolCall
from tutor.runtime.loop import TurnEngine, TurnRequest, UnknownLearner
from tutor.runtime.privacy import pseudonymize, scrub
from tutor.runtime.safety import CRISIS_REPLIES, FALLBACK_REPLY

SETTINGS = make_settings()
PID = pseudonymize("roster-42", SETTINGS.pseudonym_pepper)


def engine(llm, store=None):
    store = store or FakeStore()
    if PID not in store.states:
        store.enroll(PID)
    return TurnEngine(SETTINGS, llm, store, load_prompts()), store


class TurnLoopTests(unittest.TestCase):
    def test_normal_turn_is_exactly_three_calls_plan_reply_review(self):
        llm = FakeLLM(plans=[PLAN_NORMAL], reviews=[REVIEW_OK],
                      replies=["What do both fractions need to share before you add them?"])
        eng, store = engine(llm)
        store.curriculum["class-demo"] = ["Week 3: add fractions with like denominators"]
        r = eng.handle(TurnRequest("roster-42", "s1", "is 1/2 + 1/3 = 2/5?"))
        self.assertEqual(llm.purposes(), ["plan", "reply", "review"])
        self.assertEqual(r.meta["model_calls"], 3)
        self.assertIn("share", r.reply)
        # The reply call had tools defined (cache-stable) but disabled by the plan.
        self.assertEqual(llm.calls[1]["tool_choice"], {"type": "none"})
        # Prefetched lesson plan landed in the brief, with no tool call.
        self.assertIn("Week 3", llm.calls[1]["messages"][-1]["content"])

    def test_acknowledgement_skips_the_planner(self):
        llm = FakeLLM(reviews=[REVIEW_OK], replies=["Great. Can you tell me the first step?"])
        eng, _ = engine(llm)
        r = eng.handle(TurnRequest("roster-42", "s1", "ok thanks"))
        self.assertEqual(llm.purposes(), ["reply", "review"])
        self.assertEqual(r.meta["plan_source"], "fast_path")

    def test_crisis_phrase_short_circuits_with_zero_model_calls_and_a_flag(self):
        llm = FakeLLM()
        eng, store = engine(llm)
        r = eng.handle(TurnRequest("roster-42", "s1", "a stranger online wants my address and photos"))
        self.assertEqual(llm.calls, [])
        self.assertEqual(r.reply, CRISIS_REPLIES["online_danger"])
        self.assertEqual(store.flags[0]["severity"], "high")

    def test_review_rejects_twice_so_child_gets_fallback_never_the_draft(self):
        llm = FakeLLM(plans=[PLAN_NORMAL], reviews=[REVIEW_BAD, REVIEW_BAD],
                      replies=["The answer is 5/6.", "It's 5/6, just write that."])
        eng, store = engine(llm)
        r = eng.handle(TurnRequest("roster-42", "s1", "what's 1/2 + 1/3"))
        self.assertEqual(r.reply, FALLBACK_REPLY)
        self.assertEqual(llm.purposes(), ["plan", "reply", "review", "reply", "review"])
        self.assertEqual(sum(a["event"] == "review_reject" for a in store.audits), 2)

    def test_review_outage_fails_closed(self):
        llm = FakeLLM(plans=[PLAN_NORMAL], reviews=[], replies=["Unreviewed draft"])   # review pop -> IndexError
        eng, store = engine(llm)
        r = eng.handle(TurnRequest("roster-42", "s1", "help with fractions"))
        self.assertEqual(r.reply, FALLBACK_REPLY)
        self.assertTrue(any(a["event"] == "review_error" for a in store.audits))

    def test_tool_round_is_capped_at_one(self):
        plan = {**PLAN_NORMAL, "allow_tools": True}
        tool = ToolCall("t1", "flag_for_adult", {"severity": "medium", "reason": "child says they feel unsafe"})
        llm = FakeLLM(plans=[plan], reviews=[REVIEW_OK], replies=[tool, "A grown-up you trust can help. Want to keep going?"])
        eng, store = engine(llm)
        eng.handle(TurnRequest("roster-42", "s1", "i dont feel safe sometimes"))
        self.assertEqual(llm.purposes(), ["plan", "reply", "reply", "review"])
        self.assertEqual(llm.calls[2]["tool_choice"], {"type": "none"})   # second call cannot use tools
        self.assertEqual(store.flags[0]["severity"], "medium")

    def test_raw_text_and_roster_id_are_never_persisted(self):
        llm = FakeLLM(plans=[PLAN_NORMAL], reviews=[REVIEW_OK], replies=["Let's keep that private. Back to fractions!"])
        eng, store = engine(llm)
        eng.handle(TurnRequest("roster-42", "s1", "my email is kid@example.com call 615-555-0100"))
        child = store.turns[0]
        self.assertNotIn("kid@example.com", child["text"])
        self.assertNotIn("615-555-0100", child["text"])
        self.assertNotEqual(child["pseudonym"], "roster-42")

    def test_learning_state_updates_after_a_turn(self):
        llm = FakeLLM(plans=[PLAN_NORMAL], reviews=[REVIEW_OK], replies=["Try finding a common denominator."])
        eng, store = engine(llm)
        eng.handle(TurnRequest("roster-42", "s1", "1/2 + 1/3 = 2/5"))
        s = store.states[PID]
        self.assertLess(s["mastery"]["adding fractions"], 0.5)        # wrong answer lowers the estimate
        self.assertEqual(s["current_topic"], "adding fractions")
        self.assertEqual(len(store.memory[PID]), 1)
        self.assertGreater(store.usage[PID], 0)

    def test_unenrolled_learner_is_rejected(self):
        eng = TurnEngine(SETTINGS, FakeLLM(), FakeStore(), load_prompts())
        with self.assertRaises(UnknownLearner):
            eng.handle(TurnRequest("not-on-roster", "s1", "hi"))


class RouterTests(unittest.TestCase):
    def _cues(self, msg="ok so what next", state=None):
        return read_cues(msg, None, state or {})

    def test_high_risk_always_routes_to_large(self):
        plan = Plan(move="encourage", topic="", complexity=1, risk="high")
        c = choose_tier(plan, self._cues(), {}, 0, SETTINGS, now=1000)
        self.assertEqual(c.tier, "large")

    def test_budget_guard_routes_to_small(self):
        plan = Plan(move="worked_example", topic="", complexity=5)
        c = choose_tier(plan, self._cues(), {}, SETTINGS.daily_token_budget, SETTINGS, now=1000)
        self.assertEqual(c.tier, "small")

    def test_downgrade_waits_while_cache_is_warm_then_happens(self):
        plan = Plan(move="check_understanding", topic="", complexity=1)
        state = {"router": {"tier": "medium", "last_turn_at": 990, "low_streak": 0}}
        first = choose_tier(plan, self._cues(), state, 0, SETTINGS, now=1000)
        self.assertEqual(first.tier, "medium")
        state["router"] = {**first.router_state, "low_streak": 2}
        third = choose_tier(plan, self._cues(), state, 0, SETTINGS, now=1010)
        self.assertEqual(third.tier, "small")

    def test_cold_cache_downgrades_immediately(self):
        plan = Plan(move="check_understanding", topic="", complexity=1)
        state = {"router": {"tier": "large", "last_turn_at": 0}}
        c = choose_tier(plan, self._cues(), state, 0, SETTINGS, now=10_000)
        self.assertEqual(c.tier, "small")

    def test_upgrades_are_never_delayed(self):
        plan = Plan(move="worked_example", topic="", complexity=4)
        state = {"router": {"tier": "small", "last_turn_at": 990}}
        c = choose_tier(plan, self._cues(), state, 0, SETTINGS, now=1000)
        self.assertEqual(c.tier, "large")


class ContextTests(unittest.TestCase):
    def _build(self, history, budget=12_000):
        plan = Plan(move="hint", topic="fractions", rationale="r")
        return build_context(system_prompt=load_prompts().tutor_system, age_band="6-7", state={},
                             cues=read_cues("hi", None, {}), plan=plan, memories=[], curriculum=[],
                             history=history, message="hi", budget_tokens=budget)

    def test_cache_breakpoints_on_system_and_last_history_message(self):
        ctx = self._build([("child", "a"), ("tutor", "b")])
        self.assertIn("cache_control", ctx.system[0])
        self.assertIn("cache_control", ctx.messages[-2]["content"][-1])
        self.assertIsInstance(ctx.messages[-1]["content"], str)   # volatile brief is NOT cached

    def test_oldest_history_dropped_first_to_fit_budget(self):
        history = [("child" if i % 2 == 0 else "tutor", f"turn {i} " + "x" * 2000) for i in range(20)]
        ctx = self._build(history, budget=3000)
        self.assertGreater(ctx.dropped_turns, 0)
        self.assertLessEqual(ctx.est_tokens, 3000)
        self.assertIn("turn 19", str(ctx.messages[-2]["content"]))

    def test_messages_alternate_and_start_with_user(self):
        ctx = self._build([("tutor", "orphan"), ("child", "a"), ("child", "b"), ("tutor", "c")])
        roles = [m["role"] for m in ctx.messages]
        self.assertEqual(roles[0], "user")
        self.assertTrue(all(a != b for a, b in zip(roles, roles[1:])))

    def test_prompt_comments_are_stripped(self):
        self.assertNotIn("<!--", load_prompts().tutor_system)


class CueAndPrivacyTests(unittest.TestCase):
    def test_frustration_and_answer_seeking(self):
        c = read_cues("I HATE THIS just tell me the answer!!", None, {})
        self.assertGreaterEqual(c.frustration, 0.8)
        self.assertTrue(c.answer_seeking)

    def test_confusion_streak_accumulates(self):
        s = {}
        for _ in range(2):
            s["affect"] = read_cues("i don't get it", None, s).affect
        self.assertEqual(s["affect"]["confusion_streak"], 2)

    def test_pseudonym_is_stable_and_pepper_dependent(self):
        self.assertEqual(pseudonymize("a", b"p"), pseudonymize("a", b"p"))
        self.assertNotEqual(pseudonymize("a", b"p"), pseudonymize("a", b"q"))

    def test_scrub_address(self):
        self.assertEqual(scrub("i live at 42 maple street"), "i live at [ADDRESS]")


if __name__ == "__main__":
    unittest.main()
