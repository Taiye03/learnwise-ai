"""
context/router.py — choose which Claude tier writes the child-facing reply.

Rules run in priority order; the first that applies wins:
  1. SAFETY    risky or highly frustrated moments get the strongest model.
  2. BUDGET    near the learner's daily token budget, use the small tier.
  3. NEED      a need score from the plan picks small / medium / large.
  4. CACHE     downgrades wait while the prompt cache is warm.

Why rule 4 exists: prompt caches are per model. A session's cached prefix
(system prompt + conversation so far) on tier A is worthless to tier B, so
switching re-bills the whole prefix at full price plus a cache write. A
downgrade only saves money if it sticks, so we wait for the cache to go
cold (no turn within the TTL) or for 3 consecutive lower-need turns.
Upgrades are never delayed: a struggling child doesn't wait on economics.

Ref: https://docs.claude.com/en/docs/build-with-claude/prompt-caching
"""
from __future__ import annotations

from dataclasses import dataclass

from ..runtime.config import Settings
from .cues import Cues
from .planner import Plan

ORDER = ["small", "medium", "large"]
DOWNGRADE_AFTER = 3   # consecutive lower-need turns before a warm-cache downgrade


@dataclass
class RouteChoice:
    tier: str
    model_id: str
    reason: str
    router_state: dict   # persisted into learner state for the next turn


def need_score(plan: Plan, state: dict) -> int:
    """1..7. Higher = needs more nuance than a small model reliably gives."""
    score = plan.complexity
    if plan.move in ("worked_example", "reframe_simpler"):
        score += 1                                   # explaining differently is harder than asking
    if state.get("affect", {}).get("frustration", 0) > 0.5:
        score += 1                                   # tone matters more when the child is upset
    return score


def choose_tier(plan: Plan, cues: Cues, state: dict, tokens_today: int,
                settings: Settings, now: float) -> RouteChoice:
    prev = state.get("router", {})
    prev_tier = prev.get("tier")
    cache_warm = (now - float(prev.get("last_turn_at", 0))) < settings.cache_ttl_seconds

    def pick(tier: str, reason: str, low_streak: int = 0) -> RouteChoice:
        return RouteChoice(tier, settings.tiers[tier].model_id, reason,
                           {"tier": tier, "last_turn_at": now, "low_streak": low_streak})

    # 1. SAFETY — never economize here.
    if plan.risk == "high" or cues.frustration >= 0.8:
        return pick("large", "safety: high risk or acute frustration")

    # 2. BUDGET — the small tier is still Claude, still reviewed; just cheaper.
    if tokens_today >= 0.9 * settings.daily_token_budget:
        return pick("small", "budget: >=90% of daily tokens used")

    # 3. NEED
    score = need_score(plan, state)
    want = "small" if score <= 2 else "medium" if score <= 4 else "large"

    # 4. CACHE stickiness — only ever blocks a DOWNGRADE.
    if prev_tier in ORDER and cache_warm and ORDER.index(want) < ORDER.index(prev_tier):
        streak = int(prev.get("low_streak", 0)) + 1
        if streak < DOWNGRADE_AFTER:
            return pick(prev_tier, f"sticky: cache warm, wanted {want} ({streak}/{DOWNGRADE_AFTER})", streak)
        return pick(want, f"need score {score}; downgrade after {streak} low-need turns")

    return pick(want, f"need score {score}")
