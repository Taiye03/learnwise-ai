"""
context/memory.py — long-term learner memory: what to recall, what to keep.

Recall is harness-driven: the planner decides IF and WITH WHAT QUERY, and
this module fetches before the reply call. The tutor never "searches its
memory" as a tool call.

Keep is reviewer-driven: the safety reviewer already reads every exchange,
so it also proposes at most one learning-relevant sentence per turn. We scrub
and length-cap it before it is embedded (in-database) and stored.

Mastery uses a simple running estimate per skill: p <- p + RATE*(outcome - p).
It's the same shape as an Elo update and good enough to rank weak skills;
swap in Bayesian Knowledge Tracing later if the data supports it
(Corbett & Anderson, 1994, https://doi.org/10.1007/BF01099821).
"""
from __future__ import annotations

from ..runtime.db import Store
from ..runtime.privacy import scrub

RATE = 0.2
MAX_NOTE_CHARS = 300


def recall(store: Store, pseudonym: str, query: str, k: int = 4) -> list[str]:
    if not query.strip():
        return []
    return store.search_memory(pseudonym, query[:200], k)


def keep(store: Store, pseudonym: str, note: str) -> bool:
    """Store one reviewer-proposed note. Returns False if there was nothing worth keeping."""
    note = scrub(note.strip())[:MAX_NOTE_CHARS]
    if len(note) < 12:          # skip "", "n/a", "none."
        return False
    store.add_memory(pseudonym, "learning", note)
    return True


def update_mastery(state: dict, skill: str, correct: bool | None) -> None:
    """In-place update of state['mastery'][skill]; no-op without evidence."""
    if not skill or correct is None:
        return
    mastery = state.setdefault("mastery", {})
    p = float(mastery.get(skill, 0.5))       # unknown skills start at a neutral 0.5
    mastery[skill] = round(p + RATE * ((1.0 if correct else 0.0) - p), 3)
