"""
runtime/tools.py — the tutor model's tool belt, and the executor that runs it.

Deliberately tiny. Retrieval the planner can foresee is PRE-FETCHED by the
harness (context/memory.py), so the reply model normally needs no tools at
all and the planner passes tool_choice={"type": "none"}. Tools exist only for
needs nobody predicted.

TOOL_DEFS never changes between turns, on purpose: tool definitions sit at
the very front of the prompt-cache prefix, so editing or reordering them per
turn would invalidate the cache for every session.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from .db import Store
from .llm import ToolCall
from .privacy import scrub

TOOL_DEFS: list[dict] = [
    {
        "name": "flag_for_adult",
        "description": ("Alert the child's teacher that a trusted adult should check in. Use when the child "
                        "mentions being unsafe, hurt, very upset, or being asked to keep secrets. Do not tell "
                        "the child details of the alert; just tell them a grown-up can help."),
        "input_schema": {
            "type": "object",
            "properties": {
                "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                "reason": {"type": "string", "description": "One factual sentence, no names."},
            },
            "required": ["severity", "reason"],
        },
    },
    {
        "name": "lookup_curriculum",
        "description": "Search the class lesson plan when the child asks about something the brief did not cover.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
]


@dataclass(frozen=True)
class TurnIds:
    pseudonym: str
    session_id: str
    class_id: str


class ToolExecutor:
    """Runs one ToolCall against an allowlist. Unknown or malformed calls return an error result, never raise."""

    def __init__(self, store: Store):
        self._store = store

    def run(self, call: ToolCall, ids: TurnIds) -> str:
        """Return the tool_result content string (JSON)."""
        try:
            if call.name == "flag_for_adult":
                severity = call.input.get("severity")
                if severity not in ("low", "medium", "high"):
                    return json.dumps({"error": "invalid severity"})
                self._store.flag_for_adult(ids.pseudonym, ids.session_id, severity,
                                           scrub(str(call.input.get("reason", ""))))
                result = {"ok": True}
            elif call.name == "lookup_curriculum":
                hits = self._store.search_curriculum(ids.class_id, str(call.input.get("query", ""))[:200], 2)
                result = {"excerpts": [h[:800] for h in hits]}
            else:
                result = {"error": f"unknown tool {call.name}"}
        except Exception as exc:   # a tool failure must never crash the child's turn
            result = {"error": type(exc).__name__}
        self._store.audit("tool_call", ids.pseudonym, {"tool": call.name, "ok": "error" not in result})
        return json.dumps(result)
