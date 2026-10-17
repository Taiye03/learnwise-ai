"""
runtime/safety.py — the two synchronous safety gates around every reply.

  Gate 1  screen_input()  deterministic, runs BEFORE any model call.
          A floor, not a ceiling: catches unambiguous danger phrases and
          short-circuits to a fixed, pre-reviewed reply + an adult flag.
          The planner and reviewer catch the nuanced cases.

  Gate 2  review_reply()  one small-model call AFTER the reply is drafted.
          Fails CLOSED: if the review rejects twice or errors, the child
          gets a neutral fallback line, never the unreviewed draft.

The review call also extracts mastery evidence and a memory note, so the
same round trip does safety + learning bookkeeping (one call, two jobs).

Ref: Anthropic, Guidelines for Organizations Serving Minors
     https://support.claude.com/en/articles/9307344
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .llm import LLM, LLMResult, structured_call

# --- Gate 1: deterministic input screen ------------------------------------

_CRISIS: dict[str, list[re.Pattern[str]]] = {
    "harm_self": [
        re.compile(r"\b(kill|hurt|cut)\s+my\s*self\b", re.I),
        re.compile(r"\bwant\s+to\s+die\b", re.I),
        re.compile(r"\bsuicid", re.I),
    ],
    "harm_by_other": [
        re.compile(r"\b(someone|somebody|he|she|they|my\s+\w+)\s+(?:is\s+)?(hurting|hurts|hits|hit|beats)\s+me\b", re.I),
        re.compile(r"\bscared\s+to\s+go\s+home\b", re.I),
    ],
    "online_danger": [
        re.compile(r"\bstranger\b.*\b(meet|address|pictures?|photos?)\b", re.I),
        re.compile(r"\b(told|asked)\s+me\s+to\s+keep\s+(it|this)\s+(a\s+)?secret\b", re.I),
    ],
}

# Fixed replies: written once, reviewed by your child-development advisors,
# never generated. Deterministic text is the right tool for the worst moments.
CRISIS_REPLIES = {
    "harm_self": ("Thank you for telling me. That sounds really hard, and you deserve help right now. "
                  "Please tell a grown-up you trust today, like a parent, your teacher, or the school counselor. "
                  "You can also call or text 988 any time to talk to someone kind. I've let your teacher know."),
    "harm_by_other": ("Thank you for telling me. Nobody is allowed to hurt you. Please tell a grown-up you trust "
                      "today, like your teacher or the school counselor. If you are in danger right now, call 911. "
                      "I've let your teacher know."),
    "online_danger": ("Thank you for telling me. Grown-ups should never ask kids to keep secrets or meet up from "
                      "online. Please tell a parent or your teacher today. You did the right thing by saying "
                      "something. I've let your teacher know."),
}

FALLBACK_REPLY = "Hmm, let me think about that a different way. Which part would you like to work on first?"


def screen_input(text: str) -> str | None:
    """Return a crisis category if any floor pattern matches, else None."""
    for category, patterns in _CRISIS.items():
        if any(p.search(text) for p in patterns):
            return category
    return None


# --- Gate 2: post-draft review (one small-model call) -----------------------

REVIEW_TOOL = {
    "name": "submit_review",
    "description": "Submit your review of the tutor's draft reply and what the child's message shows.",
    "input_schema": {
        "type": "object",
        "properties": {
            "approved": {"type": "boolean", "description": "True only if the draft is safe, age-appropriate and pedagogically sound."},
            "issues": {"type": "array", "items": {"type": "string"}, "description": "Short reasons if not approved."},
            "needs_adult": {"type": "boolean", "description": "Child's message suggests a trusted adult should follow up."},
            "adult_reason": {"type": "string"},
            "mastery_skill": {"type": "string", "description": "Skill the child's message gave evidence on, or empty."},
            "mastery_correct": {"type": "boolean", "description": "Whether that evidence showed correct understanding."},
            "memory_note": {"type": "string", "description": "One de-identified, learning-relevant sentence worth remembering, or empty."},
        },
        "required": ["approved", "issues", "needs_adult", "mastery_skill", "memory_note"],
    },
}


@dataclass
class Review:
    approved: bool
    issues: list[str] = field(default_factory=list)
    needs_adult: bool = False
    adult_reason: str = ""
    mastery_skill: str = ""
    mastery_correct: bool | None = None
    memory_note: str = ""


def review_reply(llm: LLM, model: str, reviewer_prompt: str, age_band: str,
                 child_message: str, draft: str, move: str) -> tuple[Review, LLMResult]:
    """Ask the small model to approve the draft and extract learning evidence, in one call."""
    user_text = (f"<age_band>{age_band}</age_band>\n<planned_move>{move}</planned_move>\n"
                 f"<child_message>\n{child_message}\n</child_message>\n<draft_reply>\n{draft}\n</draft_reply>")
    system = [{"type": "text", "text": reviewer_prompt, "cache_control": {"type": "ephemeral"}}]
    data, result = structured_call(llm, model=model, system=system, user_text=user_text,
                                   tool_schema=REVIEW_TOOL, max_tokens=400)
    return Review(
        approved=bool(data.get("approved", False)),   # missing field => not approved (fail closed)
        issues=list(data.get("issues", [])),
        needs_adult=bool(data.get("needs_adult", False)),
        adult_reason=str(data.get("adult_reason", "")),
        mastery_skill=str(data.get("mastery_skill", "")).strip().lower(),
        mastery_correct=data.get("mastery_correct"),
        memory_note=str(data.get("memory_note", "")).strip(),
    ), result
