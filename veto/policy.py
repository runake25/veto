"""The charter rules, as one pure function.

No IO, no clock, no LLM, no PayPal: give it the agent's charter and a priced cart
and it returns ``auto`` / ``review`` / ``block`` with one reason per violated
rule. This is the most-tested unit in the product on purpose -- the agent may
choose what to buy, but never whether the rules applied.
"""
from __future__ import annotations

RULE_KINDS = (
    "monthly_cap",
    "per_order_cap",
    "per_unit_cap",
    "approval_threshold",
    "vendor_allowlist",
    "category_allowlist",
)

# Hard limits live here rather than in the agents, so a bad charter edit cannot
# turn an agent into an unlimited spender.
BLOCK_RULES = ("category_allowlist", "vendor_allowlist", "monthly_cap", "per_order_cap")
REVIEW_RULES = ("per_unit_cap", "approval_threshold")


# ---------------------------------------------------------------------------
# small value helpers
# ---------------------------------------------------------------------------
def money(cents: int | None) -> str:
    """$1,234.56 -- the only money format that appears in policy messages."""
    return "${:,.2f}".format((cents or 0) / 100)


def _int(value, default: int | None = None) -> int | None:
    """Best-effort integer; ``None``/junk becomes ``default`` (usually None)."""
    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text(value) -> str:
    return str(value or "").strip()


def _reason(rule: str, severity: str, message: str,
            observed_cents: int | None = None, limit_cents: int | None = None,
            unblock: str | None = None) -> dict:
    return {
        "rule": rule,
        "severity": severity,
        "message": message,
        "observed_cents": observed_cents,
        "limit_cents": limit_cents,
        "unblock": unblock,
    }


def _category(line: dict) -> str:
    return _text(line.get("category")).lower()


def _unit_cents(line: dict) -> int:
    return _int(line.get("unit_cents"), 0) or 0


def _line_cents(line: dict) -> int:
    explicit = _int(line.get("line_cents"))
    if explicit is not None:
        return explicit
    return _unit_cents(line) * max(1, _int(line.get("qty"), 1) or 1)


def _override(agent: dict, category: str, kind: str) -> int | None:
    """The category override for this rule, if the charter has one."""
    for override in agent.get("overrides") or []:
        if not isinstance(override, dict):
            continue
        if _text(override.get("category")).lower() != category:
            continue
        if _text(override.get("kind")) != kind:
            continue
        value = _int(override.get("value_cents"))
        if value is not None:
            return value
    return None


def _overrides_for(agent: dict, kind: str) -> list[tuple[str, int]]:
    """(category, value) pairs for one rule, sorted so reasons are deterministic."""
    found: dict[str, int] = {}
    for override in agent.get("overrides") or []:
        if not isinstance(override, dict):
            continue
        if _text(override.get("kind")) != kind:
            continue
        category = _text(override.get("category")).lower()
        value = _int(override.get("value_cents"))
        if category and value is not None:
            found[category] = value
    return sorted(found.items())


def _vendor_allowed(vendor: str, approved: list[str]) -> bool:
    """A vendor domain matches itself or any subdomain of an approved domain."""
    return any(vendor == allowed or vendor.endswith("." + allowed) for allowed in approved)


# ---------------------------------------------------------------------------
# the decision
# ---------------------------------------------------------------------------
def evaluate(*, agent: dict, lines: list[dict], month_spent_cents: int = 0,
             workspace_monthly_budget_cents: int | None = None,
             unresolved: list[str] | None = None) -> dict:
    """Judge one priced cart for one agent.

    Returns ``{"outcome": "auto"|"review"|"block", "reasons": [...]}``. Block
    reasons always come before review reasons; within each severity the rule
    order is category, vendor, monthly, per-order, per-unit, threshold.
    """
    cart = [line for line in (lines or []) if isinstance(line, dict)]
    missing = [str(name) for name in (unresolved or []) if _text(name)]
    enabled = {str(kind) for kind in (agent.get("rules") or [])}

    categories = {_text(item).lower() for item in (agent.get("categories") or []) if _text(item)}
    vendors = [_text(item).lower() for item in (agent.get("vendors") or []) if _text(item)]
    agent_name = _text(agent.get("name")) or "this agent"

    total = sum(_line_cents(line) for line in cart)
    spent = _int(month_spent_cents, 0) or 0
    subtotals: dict[str, int] = {}
    for line in cart:
        subtotals[_category(line)] = subtotals.get(_category(line), 0) + _line_cents(line)

    blocks: list[dict] = []
    reviews: list[dict] = []

    # 0. anything the catalogue could not price is a hard stop: no consent can be
    #    signed over a cart that silently dropped a line.
    if missing:
        blocks.append(_reason(
            "catalogue", "block",
            f"no product for: {', '.join(missing)}",
            unblock="try a different product name or add it to the catalogue",
        ))

    # 1. category allowlist -- the agent may only buy its own categories.
    if "category_allowlist" in enabled:
        seen: set[str] = set()
        for line in cart:
            category = _category(line)
            if category in categories or category in seen:
                continue
            seen.add(category)
            label = category or "uncategorised"
            blocks.append(_reason(
                "category_allowlist", "block",
                f"{label} is not an approved category for {agent_name}",
                unblock=f"add {label} to this agent's categories",
            ))

    # 2. vendor allowlist -- suffix match, so www.amazon.com passes amazon.com.
    if "vendor_allowlist" in enabled:
        seen_vendors: set[str] = set()
        for line in cart:
            vendor = _text(line.get("vendor")).lower()
            if _vendor_allowed(vendor, vendors) or vendor in seen_vendors:
                continue
            seen_vendors.add(vendor)
            label = vendor or "an unknown merchant"
            blocks.append(_reason(
                "vendor_allowlist", "block",
                f"{label} is not an approved merchant",
                unblock=(f"add {vendor} to approved merchants" if vendor
                         else "add the merchant to approved merchants"),
            ))

    # 3. monthly cap -- the agent's own cap, or the workspace budget when the
    #    charter leaves the agent uncapped.
    if "monthly_cap" in enabled:
        cap = _int(agent.get("monthly_cap_cents"))
        if cap is None:
            cap = _int(workspace_monthly_budget_cents)
        if cap is not None:
            observed = spent + total
            if observed > cap:
                blocks.append(_reason(
                    "monthly_cap", "block",
                    f"{money(observed)} would be over the {money(cap)} monthly cap",
                    observed, cap,
                    f"raise the monthly cap to {money(observed)}",
                ))

    # 4. per-order cap -- cart total, plus each per-category override.
    if "per_order_cap" in enabled:
        cap = _int(agent.get("per_order_cap_cents"))
        if cap is not None and total > cap:
            blocks.append(_reason(
                "per_order_cap", "block",
                f"{money(total)} over the {money(cap)} per-order cap",
                total, cap,
                f"raise the per-order cap to {money(total)}",
            ))
        for category, value in _overrides_for(agent, "per_order_cap"):
            subtotal = subtotals.get(category)
            if subtotal is None or subtotal <= value:
                continue
            blocks.append(_reason(
                "per_order_cap", "block",
                f"{category} subtotal {money(subtotal)} over the {money(value)} cap",
                subtotal, value,
                f"raise the {category} per-order cap to {money(subtotal)}",
            ))

    # 5. per-unit cap -- a review, one reason per offending line. A category
    #    override replaces the agent default for lines in that category.
    if "per_unit_cap" in enabled:
        default_cap = _int(agent.get("per_unit_cap_cents"))
        for line in cart:
            category = _category(line)
            override = _override(agent, category, "per_unit_cap")
            cap = override if override is not None else default_cap
            if cap is None:
                continue
            unit = _unit_cents(line)
            if unit <= cap:
                continue
            title = _text(line.get("title")) or _text(line.get("sku")) or "item"
            scope = f"the {category} per-unit cap" if override is not None else "the per-unit cap"
            reviews.append(_reason(
                "per_unit_cap", "review",
                f"{title} is {money(unit)} per unit against a {money(cap)} cap",
                unit, cap,
                f"raise {scope} to {money(unit)}",
            ))

    # 6. approval threshold -- cart total, plus each per-category override.
    if "approval_threshold" in enabled:
        threshold = _int(agent.get("approval_threshold_cents"))
        if threshold is not None and total > threshold:
            reviews.append(_reason(
                "approval_threshold", "review",
                f"{money(total)} over the {money(threshold)} approval threshold",
                total, threshold,
                f"cart must stay under {money(threshold)}",
            ))
        for category, value in _overrides_for(agent, "approval_threshold"):
            subtotal = subtotals.get(category)
            if subtotal is None or subtotal <= value:
                continue
            reviews.append(_reason(
                "approval_threshold", "review",
                f"{category} subtotal {money(subtotal)} over the {money(value)} approval threshold",
                subtotal, value,
                f"keep the {category} subtotal under {money(value)}",
            ))

    reasons = blocks + reviews
    outcome = "block" if blocks else ("review" if reviews else "auto")
    return {"outcome": outcome, "reasons": reasons}
