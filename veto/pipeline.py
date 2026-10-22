"""The M2 pipeline: text -> items -> scoped catalogue -> priced cart -> policy.

Everything here is a dry run. It reuses the legacy helpers for extraction
(``engine.deepseek``) and catalogue access (``channel3``), but it never writes to
the database, never touches PayPal, and never mints a consent. Persisting a
request, authorising and capturing are M3.
"""
from __future__ import annotations

import json
import re
import time

from veto import engine as legacy_engine
from veto import policy
from veto.channel3 import Channel3, Channel3Error, rank, relevant, to_line
from veto.prompts import CHOOSE_SYSTEM, SYSTEM

SEARCH_LIMIT = 8
# The channel search is bounded by the agent's per-order cap, and never above
# this ceiling, so a mis-typed cap cannot ask the catalogue for a car.
HARD_PRICE_CEILING_CENTS = 100_000
_BIG = 10 ** 9

# -- fallback extraction ----------------------------------------------------
_COMMAND = re.compile(r"^\s*(?:/veto|@veto|veto)\b[:\s,]*", re.I)
_SPLIT = re.compile(r"\s*(?:,|;|\n|\+|\band\b|&)\s*", re.I)
_QTY = re.compile(
    r"^\s*(\d+)\s*(?:x|×|boxes?|packs?|rolls?|units?|ct|count)?[\s,:-]*", re.I
)
_FILLER = re.compile(
    r"^(?:(?:please|kindly|can\s+we\s+get|we\s+need\s+to\s+get|we\s+need|"
    r"we're\s+out\s+of|we\s+are\s+out\s+of|out\s+of|ran\s+out\s+of|"
    r"running\s+low\s+on|low\s+on|need|needs|get|grab|buy|order|restock|"
    r"pick\s+up|add|some|a|an|the|of|more|few|new)\s+)+",
    re.I,
)
_STOP_PHRASES = {"nothing", "none", "nope", "no", "zip", "nada", "nil"}


def _text(value) -> str:
    return str(value or "").strip()


def _int(value, default: int | None = None) -> int | None:
    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_items(raw) -> list[dict]:
    """Normalise whatever the extractor returned into ``[{name, qty}]``."""
    items: list[dict] = []
    if not isinstance(raw, list):
        return items
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"))
        if not name:
            continue
        qty = max(1, _int(item.get("qty"), 1) or 1)
        items.append({"name": name, "qty": qty})
    return items


def fallback_extract(text: str) -> list[dict]:
    """Deterministic extraction for when the LLM is unavailable.

    Splits on and / commas / semicolons, reads a leading quantity, and strips the
    filler words a chat message puts in front of the noun phrase.
    """
    body = _COMMAND.sub("", text or "").strip()
    items: list[dict] = []
    for chunk in _SPLIT.split(body):
        phrase = chunk.strip()
        if not phrase:
            continue
        qty = 1
        match = _QTY.match(phrase)
        if match:
            qty = max(1, int(match.group(1)))
            phrase = phrase[match.end():].strip()
        phrase = _FILLER.sub("", phrase).strip().strip(".!?")
        if len(phrase) < 2 or phrase.lower() in _STOP_PHRASES:
            continue
        items.append({"name": phrase, "qty": qty})
    return items


def extract_items(text: str) -> dict:
    """LLM extraction with a deterministic fallback. Raises only on no intent."""
    prompt_text = _text(text)
    if not prompt_text:
        raise ValueError("no purchase intent found")
    try:
        parsed = legacy_engine.deepseek(SYSTEM, prompt_text)
        items = _clean_items(parsed.get("items") if isinstance(parsed, dict) else None)
        if items:
            return {"items": items, "source": "llm"}
    except Exception:  # noqa: BLE001 - offline, bad JSON, empty result -> fallback
        pass
    items = fallback_extract(prompt_text)
    if not items:
        raise ValueError("no purchase intent found")
    return {"items": items, "source": "fallback"}


# -- catalogue --------------------------------------------------------------
def _search_ceiling_cents(agent: dict) -> int:
    """Search is bounded by the agent's per-order cap, hard-capped globally."""
    cap = _int(agent.get("per_order_cap_cents"))
    return min(cap if cap and cap > 0 else _BIG, HARD_PRICE_CEILING_CENTS)


def _catalog_line(product: dict, qty: int) -> dict:
    unit = _int(product.get("unit"), 0) or 0
    return {
        "sku": _text(product.get("sku")),
        "title": _text(product.get("name")),
        "qty": qty,
        "unit_cents": unit,
        "line_cents": unit * qty,
        "vendor": _text(product.get("vendor")),
        "category": _text(product.get("category")).lower(),
        "brand": "",
        "image": "",
        "url": "",
        "source": "local",
    }


def _channel3_line(line: dict, qty: int) -> dict:
    """Convert channel3.to_line()'s legacy shape into the policy line shape."""
    unit = _int(line.get("unit"), 0) or 0
    actual_qty = max(1, _int(line.get("qty"), qty) or qty)
    return {
        "sku": _text(line.get("sku")),
        "title": _text(line.get("item")),
        "qty": actual_qty,
        "unit_cents": unit,
        "line_cents": _int(line.get("line"), unit * actual_qty) or unit * actual_qty,
        "vendor": _text(line.get("vendor")),
        "category": _text(line.get("category")).lower(),
        "brand": _text(line.get("brand")),
        "image": _text(line.get("image")),
        "url": _text(line.get("url")),
        "source": "channel3",
    }


def _category_allowed(agent: dict, line: dict) -> bool:
    categories = {_text(item).lower() for item in (agent.get("categories") or []) if _text(item)}
    return _text(line.get("category")).lower() in categories


def _singular(query: str) -> str:
    """One cheap plural retry; Channel3 matches tokens, not stems."""
    text = _text(query)
    low = text.lower()
    if len(text) <= 3 or not low.endswith("s") or low.endswith("ss"):
        return ""
    return text[:-1]


def _search_query(query: str) -> str:
    """Drop hyphenated qualifiers for the catalogue call only.

    "anti-fatigue standing mats" narrows Channel3 onto commercial mats at $126; the
    shape the buyer actually means is "standing mats". A hyphenated compound is an
    adjective in these item names, so searching without it widens the page without
    losing the noun. The full phrase is still retried if the stripped one misses.
    """
    text = _text(query)
    words = text.split()
    kept = [word for word in words if "-" not in word]
    if not kept or len(kept) == len(words):
        return text
    return " ".join(kept)


def _candidate_pool(agent: dict, query: str, qty: int, limit: int = SEARCH_LIMIT
                    ) -> tuple[list[dict], dict]:
    """Search as this agent and return ``([{line, source}], filtered_counts)``.

    Channel3 first, bounded by the agent's vendors and price ceiling; the local
    catalogue is the offline fallback. Nothing relevant is dropped here -- the
    scoped search annotates category misses instead.
    """
    query = _text(query)
    qty = max(1, _int(qty, 1) or 1)
    candidates: list[dict] = []
    filtered = {"irrelevant": 0, "category": 0}
    if not query:
        return candidates, filtered

    # Channel3's relevance floor is lexical: try the stripped phrase, its singular,
    # then the item exactly as written, before falling back.
    attempts: list[str] = []
    for base in (_search_query(query), query):
        for variant in (base, _singular(base)):
            if variant and variant not in attempts:
                attempts.append(variant)

    client = Channel3()
    if client.available:
        for attempt in attempts:
            try:
                products = client.search(
                    attempt,
                    limit=limit,
                    max_price_cents=_search_ceiling_cents(agent),
                    website_ids=(list(agent.get("vendors") or []) or None),
                )
            except Channel3Error:
                products = []
            except Exception:  # noqa: BLE001 - a catalogue outage must not kill the dry run
                products = []
            if not products:
                continue
            for product in rank(attempt, products):
                if not relevant(attempt, product):
                    filtered["irrelevant"] += 1
                    continue
                line = to_line(product, qty)
                if line is None:
                    filtered["irrelevant"] += 1
                    continue
                candidates.append({"line": _channel3_line(line, qty), "source": "channel3"})
            if candidates:
                filtered["category"] = sum(
                    1 for candidate in candidates
                    if not _category_allowed(agent, candidate["line"])
                )
                return candidates, filtered

    # No key, a catalogue error, or no relevant Channel3 hit: local catalogue.
    product = None
    for attempt in attempts:
        product = legacy_engine.match_product(attempt)
        if product:
            break
    if product:
        candidates.append({"line": _catalog_line(product, qty), "source": "local"})
    filtered["category"] = sum(
        1 for candidate in candidates if not _category_allowed(agent, candidate["line"])
    )
    return candidates, filtered


def _offer_value(line: dict) -> dict:
    """The fields the chooser sees. Price is a tie-break between equal products; it
    still never sees caps, totals, budgets or payee rules."""
    unit = _int(line.get("unit_cents"), 0) or 0
    return {
        "title": _text(line.get("title")),
        "category": _text(line.get("category")) or "general",
        "vendor": _text(line.get("vendor")),
        "unit_price_usd": round(unit / 100, 2),
    }


def _fallback_choice(agent: dict, lines: list[dict]) -> dict | None:
    """No model: take the first offer inside the charter, else the first offer.

    Deterministic and offline-safe. It cannot tell coffee from a coffee pot, which
    is exactly why the model is the default.
    """
    allowed = [line for line in lines if _category_allowed(agent, line)]
    if allowed:
        return allowed[0]
    return lines[0] if lines else None


def choose_offer(agent: dict, item: dict, lines: list[dict]) -> dict | None:
    """Pick the offer that is the requested product, or None when none is.

    The agent may choose WHAT to buy, so this is a model call, but it never sees
    prices or rules. If the model is unavailable the deterministic fallback keeps
    the dry run working; if the model says nothing matches, the item is left
    unresolved and the user is told what the catalogue did offer instead.
    """
    if not lines:
        return None
    prompt = CHOOSE_SYSTEM
    payload = json.dumps({
        "request": {"item": _text(item.get("name")), "qty": max(1, _int(item.get("qty"), 1) or 1)},
        "agent_categories": [_text(cat).lower() for cat in (agent.get("categories") or [])],
        "offers": [_offer_value(line) | {"index": index} for index, line in enumerate(lines)],
    })
    try:
        parsed = legacy_engine.deepseek(prompt, payload)
    except Exception:  # noqa: BLE001 - offline, bad JSON, timeout -> deterministic
        return _fallback_choice(agent, lines)
    if not isinstance(parsed, dict) or "pick" not in parsed:
        return _fallback_choice(agent, lines)
    pick = parsed.get("pick")
    if pick is None:  # the model looked and found nothing that is the product
        return None
    if isinstance(pick, bool) or not isinstance(pick, int):
        return None
    if 0 <= pick < len(lines):
        return lines[pick]
    return None


def _normalise_candidates(candidates: list) -> list[tuple[dict, str]]:
    """Tolerate both ``{line, source}`` and bare lines from an injectable search."""
    found: list[tuple[dict, str]] = []
    for candidate in candidates or []:
        if isinstance(candidate, dict) and isinstance(candidate.get("line"), dict):
            line = candidate["line"]
            source = _text(candidate.get("source")) or "local"
        elif isinstance(candidate, dict):
            line = dict(candidate)
            source = _text(line.get("source")) or "local"
        else:
            continue
        found.append((line, source))
    return found


def _near_miss(line: dict) -> dict:
    return {
        "title": _text(line.get("title")),
        "category": _text(line.get("category")) or "general",
        "vendor": _text(line.get("vendor")),
        "unit_cents": _int(line.get("unit_cents"), 0) or 0,
    }


def search_candidates(agent: dict, query: str, qty: int = 1,
                      limit: int = SEARCH_LIMIT) -> list[dict]:
    """Relevant catalogue candidates for one item, each ``{line, source}``."""
    candidates, _ = _candidate_pool(agent, query, qty, limit)
    return candidates


def _fit(agent: dict, line: dict) -> dict:
    """Which of the displayable constraints this single line satisfies."""
    enabled = {str(kind) for kind in (agent.get("rules") or [])}

    def override(kind: str) -> int | None:
        for item in agent.get("overrides") or []:
            if not isinstance(item, dict) or _text(item.get("kind")) != kind:
                continue
            if _text(item.get("category")).lower() != _text(line.get("category")).lower():
                continue
            value = _int(item.get("value_cents"))
            if value is not None:
                return value
        return None

    category = _text(line.get("category")).lower()
    line_total = _int(line.get("line_cents"), 0) or 0
    unit = _int(line.get("unit_cents"), 0) or 0

    categories = {_text(item).lower() for item in (agent.get("categories") or []) if _text(item)}
    category_ok = "category_allowlist" not in enabled or category in categories

    unit_cap = override("per_unit_cap")
    if unit_cap is None:
        unit_cap = _int(agent.get("per_unit_cap_cents"))
    unit_ok = "per_unit_cap" not in enabled or unit_cap is None or unit <= unit_cap

    order_cap = _int(agent.get("per_order_cap_cents"))
    order_override = override("per_order_cap")
    order_ok = "per_order_cap" not in enabled or (
        (order_cap is None or line_total <= order_cap)
        and (order_override is None or line_total <= order_override)
    )

    threshold = _int(agent.get("approval_threshold_cents"))
    threshold_override = override("approval_threshold")
    threshold_ok = "approval_threshold" not in enabled or (
        (threshold is None or line_total <= threshold)
        and (threshold_override is None or line_total <= threshold_override)
    )
    return {
        "category": bool(category_ok),
        "per_unit": bool(unit_ok),
        "per_order": bool(order_ok),
        "threshold": bool(threshold_ok),
    }


def scoped_search(agent: dict, query: str, qty: int = 1) -> dict:
    """Search as this agent, then annotate every relevant result with its verdict."""
    qty = max(1, _int(qty, 1) or 1)
    candidates, filtered = _candidate_pool(agent, query, qty, SEARCH_LIMIT)
    results = []
    for candidate in candidates:
        line = candidate["line"]
        decision = policy.evaluate(agent=agent, lines=[line], month_spent_cents=0)
        results.append({
            **line,
            "fits": _fit(agent, line),
            "verdict": decision["outcome"],
            "reasons": decision["reasons"],
        })
    return {
        "agent": agent,
        "query": _text(query),
        "qty": qty,
        "source": candidates[0]["source"] if candidates else "none",
        "scoped_by": {
            "vendors": list(agent.get("vendors") or []),
            "max_price_cents": _search_ceiling_cents(agent),
        },
        "results": results,
        "filtered": filtered,
    }


# -- dry run ----------------------------------------------------------------
def _line_total(line: dict) -> int:
    explicit = _int(line.get("line_cents"))
    if explicit is not None:
        return explicit
    return (_int(line.get("unit_cents"), 0) or 0) * max(1, _int(line.get("qty"), 1) or 1)


def simulate(agent: dict, text: str, *, extract_fn=None, search_fn=None,
             choose_fn=None) -> dict:
    """Run the whole loop in memory and return what would have happened.

    ``extract_fn`` / ``search_fn`` / ``choose_fn`` are injectable so tests (and the
    offline demo) never touch the network. Nothing is persisted; no PayPal call is
    possible.
    """
    started = time.perf_counter()
    raw = _text(text)
    if not raw:
        raise ValueError("no purchase intent found")
    extract = extract_fn or extract_items
    search = search_fn or search_candidates

    extracted = extract(raw)
    if not isinstance(extracted, dict):
        extracted = {}
    items = _clean_items(extracted.get("items"))
    if not items:
        raise ValueError("no purchase intent found")

    plural = "" if len(items) == 1 else "s"
    events = [{
        "kind": "extract",
        "label": f"{len(items)} item{plural} extracted",
        "detail": ", ".join(f"{item['name']} x{item['qty']}" for item in items),
    }]

    lines: list[dict] = []
    unresolved: list[str] = []
    near_misses: list[dict] = []
    sources: set[str] = set()
    choose = choose_fn or choose_offer
    for item in items:
        qty = max(1, _int(item.get("qty"), 1) or 1)
        try:
            candidates = search(agent, item["name"], qty) or []
        except Exception:  # noqa: BLE001 - a catalogue failure means "unresolved"
            candidates = []
        normalised = _normalise_candidates(candidates)
        if not normalised:
            unresolved.append(item["name"])
            continue
        try:
            chosen = choose(agent, {"name": item["name"], "qty": qty},
                            [line for line, _ in normalised])
        except Exception:  # noqa: BLE001 - the model must not take the dry run down
            chosen = _fallback_choice(agent, [line for line, _ in normalised])
        if chosen is None:
            unresolved.append(item["name"])
            near_misses.append({
                "item": item["name"],
                "offers": [_near_miss(line) for line, _ in normalised[:3]],
            })
            continue
        source = _text(chosen.get("source")) or "local"
        for candidate_line, candidate_source in normalised:
            if candidate_line is chosen:
                source = candidate_source
                break
        chosen["source"] = source
        lines.append(chosen)
        sources.add(source)

    merchant_hint = ", ".join(agent.get("vendors") or []) or "no merchant"
    if lines:
        via = "Channel3" if "channel3" in sources else "local catalogue"
        found = f"{via} · {merchant_hint}"
    else:
        found = "no match inside these merchants"
    events.append({
        "kind": "catalogue",
        "label": "Searched the catalogue",
        "detail": f"{_text(agent.get('name'))} · {found}",
    })
    events.append({
        "kind": "price",
        "label": "Cart priced",
        "detail": " · ".join(f"{line['qty']} x {line['title']}" for line in lines) or "empty",
    })

    total = sum(_line_total(line) for line in lines)
    decision = policy.evaluate(
        agent=agent,
        lines=lines,
        month_spent_cents=0,
        workspace_monthly_budget_cents=None,
        unresolved=unresolved,
    )
    events.append({
        "kind": "decision",
        "label": f"Decision: {decision['outcome']}",
        "detail": "; ".join(reason["message"] for reason in decision["reasons"])
                  or "within every rule",
    })
    return {
        "agent": agent,
        "items": items,
        "lines": lines,
        "total_cents": total,
        "currency": "USD",
        "decision": decision,
        "events": events,
        "near_misses": near_misses,
        "source": "channel3" if "channel3" in sources else "local",
        "elapsed_ms": int(round((time.perf_counter() - started) * 1000)),
        "dry_run": True,
    }


# -- agent resolution -------------------------------------------------------
def resolve_agent(store, workspace_id: str, agent_id: str | None = None) -> dict:
    """Explicit agent -> the workspace default -> the first active agent.

    Raises ``LookupError("agent_not_found")`` for a bad explicit id and
    ``LookupError("no_agent")`` when the workspace has no agents at all.
    """
    if _text(agent_id):
        agent = store.agent(workspace_id, _text(agent_id))
        if agent is None:
            raise LookupError("agent_not_found")
        return agent
    workspace = store.workspace(workspace_id)
    default_id = (workspace or {}).get("default_agent_id")
    if default_id:
        agent = store.agent(workspace_id, default_id)
        if agent is not None:
            return agent
    agents = store.list_agents(workspace_id)
    for agent in agents:
        if agent.get("status") == "active":
            return agent
    if agents:
        return agents[0]
    raise LookupError("no_agent")
