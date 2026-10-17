"""
runtime/privacy.py — pseudonyms and PII scrubbing, applied BEFORE anything is persisted.

Two rules this file enforces:
  1. The harness never stores a roster id. It stores HMAC(pepper, roster_id).
     Without the pepper (a systemd credential) the pseudonym can't be reversed
     or recomputed from a leaked roster list.
  2. Text is scrubbed of obvious contact details before it reaches Oracle.

HONEST LIMITS: regexes catch emails, phone numbers, URLs and street-style
addresses. They do NOT catch names, school names, or "I live by the big
water tower". That is why sql/01_schema.sql de-identifies by dropping raw
text entirely at the end of the retention window, instead of trusting the
scrubber. Ref: NIST SP 800-188 https://csrc.nist.gov/pubs/sp/800/188/final
"""
from __future__ import annotations

import hashlib
import hmac
import re

# (label, pattern). Order matters: URLs before emails so "x@y.com/path" scrubs once.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("URL", re.compile(r"https?://\S+", re.I)),
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("PHONE", re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")),
    ("ADDRESS", re.compile(
        r"\b\d{1,5}\s+(?:[a-z]+\s+){1,3}(?:st|street|ave|avenue|rd|road|dr|drive|ln|lane|ct|court|blvd|way)\b\.?",
        re.I)),
]


def pseudonymize(roster_id: str, pepper: bytes) -> str:
    """Stable, non-reversible learner key. Same roster id + pepper -> same pseudonym."""
    return hmac.new(pepper, roster_id.encode(), hashlib.sha256).hexdigest()[:32]


def find_pii(text: str) -> list[str]:
    """Labels of PII kinds present in text (used as a *cue*: the child just over-shared)."""
    return [label for label, pat in _PATTERNS if pat.search(text)]


def scrub(text: str) -> str:
    """Replace detected PII with [LABEL] placeholders. Applied to every persisted string."""
    for label, pat in _PATTERNS:
        text = pat.sub(f"[{label}]", text)
    return text
