"""Veto-check: the shortlist behind the agent's choice.

The pipeline asks the model to choose one offer. Sometimes a person has a
particular product in mind, and the honest thing is to show them what the
catalogue actually returned inside the agent's rules — then let them pick that
exact offer.

A check is short-lived and server-side: the Slack button carries only an
opaque id and an index, never a price or a title, so a click cannot invent a
cart. Picking re-enters the same pipeline at PRICE → JUDGE → CONSENT → SETTLE;
the pinned line skips only EXTRACT/SOURCE/CHOOSE, which is exactly the part a
human is replacing.
"""
from __future__ import annotations

import secrets
import threading
import time

from veto import pipeline

CHECK_TTL_SECONDS = 15 * 60
MAX_CANDIDATES = 5

_lock = threading.Lock()
_CHECKS: dict[str, dict] = {}

# The line fields a pick is allowed to carry into the request path.
PINNED_FIELDS = (
    "sku", "title", "qty", "unit_cents", "line_cents", "vendor", "category",
    "catalogue_category", "brand", "image", "url", "source",
)


def prune(now: float | None = None) -> None:
    cutoff = (now if now is not None else time.time()) - CHECK_TTL_SECONDS
    with _lock:
        for key in [key for key, value in _CHECKS.items()
                    if value["created_ts"] < cutoff]:
            _CHECKS.pop(key, None)


def top_candidates(
    agent: dict,
    text: str,
    *,
    qty: int | None = None,
    limit: int = MAX_CANDIDATES,
    extract_fn=None,
    search_fn=None,
) -> dict:
    """Extract the item, search as the agent, keep the first ``limit`` offers.

    Raises ``ValueError`` when the text names nothing buyable. The result keeps
    each offer's own fit flags and verdict, so the caller can decide which ones
    may be chosen without re-judging by hand.
    """
    extract = extract_fn or pipeline.extract_items
    search = search_fn or pipeline.scoped_search

    extracted = extract(text) or {}
    items = extracted.get("items") or []
    if not items:
        raise ValueError("no purchase intent found")
    item = items[0]
    quantity = qty if qty is not None else item.get("qty") or 1
    try:
        quantity = max(1, min(99, int(quantity)))
    except (TypeError, ValueError):
        quantity = 1
    query = str(item.get("query") or item.get("name") or "").strip() or str(text).strip()
    if not query:
        raise ValueError("no purchase intent found")

    result = search(agent, query, quantity)
    candidates = []
    for index, row in enumerate((result.get("results") or [])[: max(1, limit)]):
        candidates.append({
            "index": index,
            "title": str(row.get("title") or ""),
            "sku": str(row.get("sku") or ""),
            "qty": max(1, int(row.get("qty") or quantity)),
            "unit_cents": int(row.get("unit_cents") or 0),
            "line_cents": int(row.get("line_cents") or 0),
            "vendor": str(row.get("vendor") or ""),
            "category": str(row.get("category") or ""),
            "catalogue_category": str(row.get("catalogue_category") or ""),
            "brand": str(row.get("brand") or ""),
            "image": str(row.get("image") or ""),
            "url": str(row.get("url") or ""),
            "source": str(row.get("source") or "channel3"),
            "fits": row.get("fits") or {},
            "verdict": str(row.get("verdict") or "block"),
            "reasons": row.get("reasons") or [],
        })
    return {
        "agent": agent,
        "text": str(text).strip(),
        "query": query,
        "qty": quantity,
        "checked": len(items),
        "source": str(result.get("source") or "none"),
        "filtered": result.get("filtered") or {},
        "candidates": candidates,
    }


def remember(
    agent: dict,
    text: str,
    *,
    qty: int | None = None,
    limit: int = MAX_CANDIDATES,
    extract_fn=None,
    search_fn=None,
) -> tuple[str, dict]:
    """Run a check, keep the shortlist for the buttons, return ``(id, result)``."""
    result = top_candidates(
        agent, text, qty=qty, limit=limit,
        extract_fn=extract_fn, search_fn=search_fn,
    )
    check_id = secrets.token_hex(9)
    with _lock:
        _CHECKS[check_id] = {
            "created_ts": time.time(),
            "agent": agent,
            "text": result["text"],
            "query": result["query"],
            "candidates": result["candidates"],
        }
    return check_id, result


def resolve(check_id: str, index: int) -> dict | None:
    """The stored pick, or None when the check expired or the index is unknown."""
    prune()
    with _lock:
        stored = _CHECKS.get(check_id)
    if stored is None:
        return None
    try:
        candidate = stored["candidates"][int(index)]
    except (IndexError, TypeError, ValueError):
        return None
    return {
        "agent": stored["agent"],
        "text": stored["text"],
        "query": stored["query"],
        "line": {field: candidate.get(field) for field in PINNED_FIELDS},
    }


def clear() -> None:
    """Drop every cached check (tests; a restart would do the same)."""
    with _lock:
        _CHECKS.clear()
