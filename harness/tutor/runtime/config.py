"""
runtime/config.py — every setting the harness reads, loaded once at boot.

WHY ONE PLACE: nothing else in the harness reads environment variables.
That gives you exactly one file to audit when asking "what can this
process be configured to do?" — a DFIR-friendly property.

SECRETS: read from systemd credentials first ($CREDENTIALS_DIRECTORY/<name>,
mounted read-only and private to this service), falling back to an
environment variable for local development. Plain env vars leak into
/proc/<pid>/environ, core dumps and child processes; credential files don't.
Ref: https://systemd.io/CREDENTIALS/
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _secret(name: str) -> str:
    """Return a secret by name: systemd credential file first, then env var NAME_IN_CAPS."""
    cred_dir = os.environ.get("CREDENTIALS_DIRECTORY")
    if cred_dir:
        path = Path(cred_dir) / name
        if path.exists():
            return path.read_text().strip()
    value = os.environ.get(name.upper())
    if not value:
        # Fail at boot, loudly, rather than at the first child's message.
        raise RuntimeError(f"missing secret {name!r}: add a systemd credential or set {name.upper()}")
    return value


@dataclass(frozen=True)
class ModelTier:
    """One rung of the model ladder. The router speaks in tiers, never in model ids."""
    name: str                 # "small" | "medium" | "large"
    model_id: str             # Claude API model string
    relative_cost: float      # PLACEHOLDER weight vs. the small tier — set from
                              # https://docs.claude.com/en/docs/about-claude/pricing
                              # Only used to compare tiers, never to bill anyone.
    max_output_tokens: int    # hard ceiling on a reply; children's replies are short


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    oracle_user: str
    oracle_password: str
    oracle_dsn: str
    pseudonym_pepper: bytes   # HMAC key that turns roster ids into pseudonyms (see runtime/privacy.py)
    api_bearer_token: str     # shared secret the roster/front-end service presents on every request
    tiers: dict[str, ModelTier]
    planner_tier: str = "small"         # the planning call is cheap by design
    reviewer_tier: str = "small"        # so is the safety review
    context_budget_tokens: int = 12_000 # soft cap on what one reply call may see
    daily_token_budget: int = 200_000   # per learner per day; router degrades to "small" near it
    max_tool_rounds: int = 1            # hard cap: at most ONE tool round per turn
    cache_ttl_seconds: int = 300        # default prompt-cache lifetime (5 min) — router uses it
    embedding_model: str = "ALL_MINILM_L12_V2"  # ONNX model loaded INSIDE Oracle (sql/02_*.sql)
    listen_host: str = "127.0.0.1"      # bind to loopback; a reverse proxy terminates TLS
    listen_port: int = 8088


def load_settings() -> Settings:
    """Build Settings from the environment. Called exactly once, in runtime/process.py."""
    env = os.environ.get
    tiers = {
        # Claude-only by design: every tier is a Claude model. Defaults are the
        # current public model strings; override per tier without code changes.
        "small": ModelTier("small", env("TUTOR_MODEL_SMALL", "claude-haiku-5-5"),
                           float(env("TUTOR_COST_SMALL", "1")), 600),
        "medium": ModelTier("medium", env("TUTOR_MODEL_MEDIUM", "claude-sonnet-5-5"),
                            float(env("TUTOR_COST_MEDIUM", "3")), 600),
        "large": ModelTier("large", env("TUTOR_MODEL_LARGE", "claude-opus-5-5"),
                           float(env("TUTOR_COST_LARGE", "5")), 600),
    }
    return Settings(
        anthropic_api_key=_secret("anthropic_api_key"),
        oracle_user=env("ORACLE_USER", "TUTOR_APP"),
        oracle_password=_secret("oracle_password"),
        oracle_dsn=env("ORACLE_DSN", "localhost:1521/FREEPDB1"),
        pseudonym_pepper=_secret("pseudonym_pepper").encode(),
        api_bearer_token=_secret("api_bearer_token"),
        tiers=tiers,
        context_budget_tokens=int(env("TUTOR_CONTEXT_BUDGET", "12000")),
        daily_token_budget=int(env("TUTOR_DAILY_TOKENS", "200000")),
        listen_host=env("TUTOR_HOST", "127.0.0.1"),
        listen_port=int(env("TUTOR_PORT", "8088")),
    )
