"""
context/prompts — prompt files and their loader.

Each .md file starts with an HTML comment explaining its purpose, what may
change it, and cache implications. Those comments are for humans: the
loader strips them, so documentation never costs tokens or perturbs the cache.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_DIR = Path(__file__).parent
_COMMENT = re.compile(r"<!--.*?-->", re.S)


def load_prompt(name: str) -> str:
    return _COMMENT.sub("", (_DIR / f"{name}.md").read_text()).strip()


@dataclass(frozen=True)
class Prompts:
    tutor_system: str
    planner: str
    reviewer: str


def load_prompts() -> Prompts:
    return Prompts(load_prompt("tutor_system"), load_prompt("planner"), load_prompt("reviewer"))
