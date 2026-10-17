"""
context/assembler.py — build the exact prompt for the reply call, inside a token budget.

Layout, chosen for prompt-cache hits:

  tools        TOOL_DEFS (constant)                      ┐
  system       tutor_system.md + age-band rules          ┘ cache breakpoint 1 (stable for days)
  messages     prior turns, oldest -> newest             ← cache breakpoint 2 (grows each turn)
  final user   <learner_brief> ... </learner_brief>        never cached: changes every turn
               <child_message> ... </child_message>

Everything that changes per turn (plan, signals, retrieved notes) rides in the
FINAL user message. Putting it in the system prompt instead would invalidate
the cached conversation behind it on every single turn.

Budgeting: tokens are ESTIMATED (chars/4) rather than counted with the API's
count-tokens endpoint, because that would be an extra network round trip per
turn. We keep 15% headroom and drop the oldest history first.

Ref: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
"""
from __future__ import annotations

from dataclasses import dataclass

from .cues import Cues
from .planner import Plan

HEADROOM = 0.85
MAX_MEMORY_CHARS = 300
MAX_CURRICULUM_CHARS = 1200

AGE_RULES = {
    "6-7": ("AGE BAND 6-7: At most 40 words. Very short sentences and everyday words. Besides the lesson, the only "
            "safety topics are: never talk to strangers online, and always tell a grown-up when something feels wrong."),
    "8-10": ("AGE BAND 8-10: At most 70 words. Simple sentences. You may explain that AI can make mistakes and "
             "that schoolwork should be the child's own thinking."),
    "11-13": ("AGE BAND 11-13: At most 110 words. You may discuss how AI works, why it can be wrong, academic "
              "integrity, and basic online security habits."),
}


@dataclass
class Context:
    system: list[dict]
    messages: list[dict]
    est_tokens: int
    dropped_turns: int

    def with_revision_note(self, issues: list[str]) -> "Context":
        """Copy with a reviewer note appended to the final user message (used for the one bounded retry)."""
        note = "\n<revision_note>A reviewer rejected your previous draft: " + "; ".join(issues)[:500] + \
               ". Write a new reply that fixes this.</revision_note>"
        msgs = [dict(m) for m in self.messages]
        msgs[-1]["content"] = msgs[-1]["content"] + note
        return Context(self.system, msgs, self.est_tokens, self.dropped_turns)


def est_tokens(text: str) -> int:
    """~4 characters per token for English. Calibrate offline with the count-tokens endpoint if needed."""
    return len(text) // 4 + 1


def render_brief(state: dict, cues: Cues, plan: Plan, memories: list[str], curriculum: list[str]) -> str:
    weakest = sorted(state.get("mastery", {}).items(), key=lambda kv: kv[1])[:5]
    lines = [
        f"Planned move: {plan.move} — {plan.rationale}",
        f"Topic: {plan.topic or state.get('current_topic', '') or 'not set'}",
        (f"Signals (trend): frustration {cues.affect.get('frustration', 0):.2f}, "
         f"confusion {cues.affect.get('confusion', 0):.2f}, disengagement {cues.affect.get('disengagement', 0):.2f}"),
    ]
    if weakest:
        lines.append("Mastery (weakest first): " + ", ".join(f"{k} {v:.2f}" for k, v in weakest))
    if memories:
        lines.append("Notes from earlier sessions:\n" + "\n".join(f"- {m[:MAX_MEMORY_CHARS]}" for m in memories))
    if curriculum:
        lines.append("Lesson plan excerpts:\n" + "\n".join(f"- {c[:MAX_CURRICULUM_CHARS]}" for c in curriculum))
    if cues.shared_pii:
        lines.append("Reminder: the child just shared personal contact details. Gently say we keep those private.")
    if cues.answer_seeking:
        lines.append("Reminder: the child asked for the answer. Guide them to it; do not hand it over.")
    return "\n".join(lines)


def _normalize(turns: list[tuple[str, str]]) -> list[dict]:
    """API needs user/assistant alternation starting with user: merge repeats, drop a leading assistant."""
    out: list[dict] = []
    for role, text in turns:
        api_role = "user" if role == "child" else "assistant"
        body = f"<child_message>\n{text}\n</child_message>" if api_role == "user" else text
        if out and out[-1]["role"] == api_role:
            out[-1]["content"] += "\n\n" + body
        else:
            out.append({"role": api_role, "content": body})
    while out and out[0]["role"] == "assistant":
        out.pop(0)
    # The final message we add is a user message, so history must end on assistant.
    while out and out[-1]["role"] == "user":
        out.pop()
    return out


def build_context(*, system_prompt: str, age_band: str, state: dict, cues: Cues, plan: Plan,
                  memories: list[str], curriculum: list[str], history: list[tuple[str, str]],
                  message: str, budget_tokens: int) -> Context:
    system_text = system_prompt + "\n\n" + AGE_RULES[age_band]
    system = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]

    final_user = (f"<learner_brief>\n{render_brief(state, cues, plan, memories, curriculum)}\n</learner_brief>\n\n"
                  f"<child_message>\n{message}\n</child_message>")

    available = int(budget_tokens * HEADROOM) - est_tokens(system_text) - est_tokens(final_user)

    # Keep the newest turns that fit; drop the oldest first.
    kept: list[tuple[str, str]] = []
    for role, text in reversed(history):
        cost = est_tokens(text) + 8   # + role/tag overhead
        if cost > available:
            break
        kept.append((role, text))
        available -= cost
    kept.reverse()

    messages = _normalize(kept)
    if messages:
        # Breakpoint 2: cache the conversation so far. Next turn re-reads it cheaply.
        last = messages[-1]
        last["content"] = [{"type": "text", "text": last["content"], "cache_control": {"type": "ephemeral"}}]
    messages.append({"role": "user", "content": final_user})

    used = int(budget_tokens * HEADROOM) - available
    return Context(system=system, messages=messages, est_tokens=used, dropped_turns=len(history) - len(kept))
