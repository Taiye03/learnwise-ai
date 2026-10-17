"""
context/planner.py — decide the whole turn up front, in one cheap call.

The plan answers, before the expensive reply call happens:
  * which pedagogical MOVE to make (hint, Socratic question, worked example...)
  * whether to fetch memories / lesson-plan excerpts, and with what query
  * whether the reply model may use tools at all
  * how hard the turn is (feeds the model router)
  * whether anything is risky (feeds escalation and routing)

Planning first is what removes superfluous tool calls: the reply model gets
everything it needs injected, so it doesn't have to go looking. A fast path
skips even the planner for trivial acknowledgements ("ok", "thanks").

Ref: "Building effective agents" — prefer predictable workflows; route
inputs before handling them. https://www.anthropic.com/engineering/building-effective-agents
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from ..runtime.llm import LLM, LLMResult, structured_call
from .cues import Cues

MOVES = ["socratic_question", "hint", "worked_example", "check_understanding", "encourage",
         "reframe_simpler", "suggest_break", "redirect_to_task", "privacy_reminder"]

PLAN_TOOL = {
    "name": "submit_plan",
    "description": "Submit the plan for the tutor's next reply.",
    "input_schema": {
        "type": "object",
        "properties": {
            "move": {"type": "string", "enum": MOVES},
            "topic": {"type": "string", "description": "Short topic label, e.g. 'adding fractions'."},
            "needs_memory": {"type": "boolean"},
            "memory_query": {"type": "string"},
            "needs_curriculum": {"type": "boolean"},
            "curriculum_query": {"type": "string"},
            "allow_tools": {"type": "boolean", "description": "Normally false."},
            "complexity": {"type": "integer", "minimum": 1, "maximum": 5},
            "risk": {"type": "string", "enum": ["none", "low", "high"]},
            "risk_reason": {"type": "string"},
            "rationale": {"type": "string", "description": "<= 25 words, for the audit log and the tutor."},
        },
        "required": ["move", "topic", "needs_memory", "needs_curriculum", "allow_tools", "complexity", "risk", "rationale"],
    },
}


@dataclass
class Plan:
    move: str
    topic: str
    needs_memory: bool = False
    memory_query: str = ""
    needs_curriculum: bool = False
    curriculum_query: str = ""
    allow_tools: bool = False
    complexity: int = 2
    risk: str = "none"
    risk_reason: str = ""
    rationale: str = ""
    source: str = "model"   # "model" | "fast_path" — recorded in turn meta

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        """Validate model output defensively: unknown values fall back to safe defaults."""
        return cls(
            move=d.get("move") if d.get("move") in MOVES else "socratic_question",
            topic=str(d.get("topic", ""))[:80],
            needs_memory=bool(d.get("needs_memory")),
            memory_query=str(d.get("memory_query", ""))[:200],
            needs_curriculum=bool(d.get("needs_curriculum")),
            curriculum_query=str(d.get("curriculum_query", ""))[:200],
            allow_tools=bool(d.get("allow_tools")),
            complexity=min(5, max(1, int(d.get("complexity", 2)))),
            risk=d.get("risk") if d.get("risk") in ("none", "low", "high") else "low",
            risk_reason=str(d.get("risk_reason", ""))[:300],
            rationale=str(d.get("rationale", ""))[:300],
        )


def fast_path(cues: Cues, state: dict) -> Plan | None:
    """Skip the planner call entirely when the next move is obvious."""
    if cues.trivial_ack and cues.frustration < 0.3 and not cues.shared_pii:
        return Plan(move="check_understanding", topic=state.get("current_topic", ""),
                    complexity=1, rationale="acknowledgement; continue the current step", source="fast_path")
    return None


def make_plan(llm: LLM, model: str, planner_prompt: str, state: dict, cues: Cues,
              history: list[tuple[str, str]], message: str) -> tuple[Plan, LLMResult | None]:
    """Return (plan, result-or-None). None means the fast path was used: zero model calls."""
    quick = fast_path(cues, state)
    if quick:
        return quick, None

    # Compact, structured input: the planner needs signals, not a transcript.
    summary = {
        "age_band": state.get("age_band"),
        "current_topic": state.get("current_topic", ""),
        "mastery": dict(sorted(state.get("mastery", {}).items(), key=lambda kv: kv[1])[:5]),  # weakest 5
        "affect_trend": cues.affect,
        "this_turn": {"frustration": cues.frustration, "confusion": cues.confusion,
                      "disengagement": cues.disengagement, "answer_seeking": cues.answer_seeking,
                      "shared_personal_info": cues.shared_pii, "seconds_to_answer": cues.latency_s},
        "recent_turns": [f"{role}: {text[:160]}" for role, text in history[-4:]],
    }
    user_text = (f"<signals>\n{json.dumps(summary, indent=1)}\n</signals>\n"
                 f"<child_message>\n{message}\n</child_message>")
    system = [{"type": "text", "text": planner_prompt, "cache_control": {"type": "ephemeral"}}]
    data, result = structured_call(llm, model=model, system=system, user_text=user_text,
                                   tool_schema=PLAN_TOOL, max_tokens=300)
    return Plan.from_dict(data), result
