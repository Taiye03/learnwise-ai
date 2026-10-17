"""
context/cues.py — read the child's signals from the message itself. No model call.

What a text tutor can actually observe: word choice, length, punctuation,
capitals, and how long the child took to answer. That's crude next to the
camera/posture signals in affective-computing research, but it is cheap,
deterministic and explainable to a teacher — which matters more here.

Each turn produces raw scores in [0, 1]; we then keep an exponential moving
average (EMA) per signal in the learner state, so ONE outburst nudges the
picture but a run of them changes it.

Grounding: confusion is often productive when it gets resolved, and harmful
when it persists into frustration/boredom (D'Mello & Graesser, 2012,
https://doi.org/10.1016/j.learninstruc.2011.10.001). So we track confusion
separately from frustration and let the planner decide when to step in.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..runtime.privacy import find_pii

ALPHA = 0.4   # EMA weight on the newest turn: responsive, but one turn can't dominate

_FRUSTRATION = re.compile(r"\b(i hate|stupid|dumb|i can'?t|i give up|too hard|ugh+|this sucks)\b", re.I)
_CONFUSION = re.compile(r"\b(i don'?t (get|understand|know)|idk|huh|confus\w*|what do you mean|wait what)\b", re.I)
_ANSWER_SEEKING = re.compile(r"\b(just (tell|give) me|what'?s the answer|do it for me|write it for me)\b", re.I)
# 1-3 acknowledgement words in a row: "ok", "ok thanks", "yeah got it!"
_ACK = re.compile(r"^\s*(?:(?:ok(?:ay)?|k|yes|yeah|yep|no|nope|thanks?|thank you|cool|got it)[.!, ]*){1,3}$", re.I)


@dataclass
class Cues:
    frustration: float          # this turn, 0..1
    confusion: float
    disengagement: float
    answer_seeking: bool        # wants the answer handed over (academic-integrity moment)
    shared_pii: bool            # child just typed contact details
    trivial_ack: bool           # "ok", "thanks" — lets the planner be skipped
    latency_s: float | None
    affect: dict = field(default_factory=dict)   # updated EMAs, persisted by the turn loop


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def read_cues(message: str, latency_s: float | None, state: dict) -> Cues:
    words = message.split()
    letters = [c for c in message if c.isalpha()]
    caps_ratio = sum(c.isupper() for c in letters) / len(letters) if letters else 0.0
    trivial = bool(_ACK.match(message))

    frustration = 0.6 * bool(_FRUSTRATION.search(message))
    frustration += 0.2 * (caps_ratio > 0.6 and len(letters) > 4)   # SHOUTING
    frustration += 0.2 * ("!!" in message)

    confusion = 0.7 if _CONFUSION.search(message) else (0.2 if message.rstrip().endswith("?") else 0.0)

    disengagement = 0.0
    if latency_s is not None and latency_s > 120:       # long pause before answering
        disengagement += 0.5
    if len(words) <= 2 and not trivial:                 # one-word answers that aren't acknowledgements
        disengagement += 0.3

    raw = {"frustration": _clamp(frustration), "confusion": _clamp(confusion),
           "disengagement": _clamp(disengagement)}
    prev = state.get("affect", {})
    affect = {k: round(ALPHA * v + (1 - ALPHA) * float(prev.get(k, 0.0)), 3) for k, v in raw.items()}
    # Remember how many turns in a row confusion stayed high: the planner's
    # "productive struggle" rule scaffolds only once it persists.
    affect["confusion_streak"] = (int(prev.get("confusion_streak", 0)) + 1) if raw["confusion"] >= 0.5 else 0

    return Cues(
        frustration=raw["frustration"], confusion=raw["confusion"], disengagement=raw["disengagement"],
        answer_seeking=bool(_ANSWER_SEEKING.search(message)), shared_pii=bool(find_pii(message)),
        trivial_ack=trivial, latency_s=latency_s, affect=affect,
    )
