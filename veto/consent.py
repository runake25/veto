"""The consent window, one policy for both request paths.

Ten minutes is the default: long enough for a person to read a cart, short enough
that a stale approval cannot be replayed later. The hosted demo overrides it with
``VETO_CONSENT_MINUTES`` so a passing judge still finds a cart they can sign; the
console reads the actual window from the record, so the screen never lies.
"""
from __future__ import annotations

import os

DEFAULT_MINUTES = 10


def ttl_minutes() -> int:
    raw = os.environ.get("VETO_CONSENT_MINUTES", str(DEFAULT_MINUTES)) or str(DEFAULT_MINUTES)
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MINUTES


def window_word() -> str:
    """'10 min' / '12 h' — the window as a person reads it."""
    minutes = ttl_minutes()
    if minutes % 60 == 0 and minutes >= 120:
        return f"{minutes // 60} h"
    return f"{minutes} min"
