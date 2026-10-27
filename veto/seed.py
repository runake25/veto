"""Seed the demo workspace.

One company (``Bracken & Field``), two humans, four agents whose charters are
different on purpose so the demo can show auto / review / block in one screen.
Seeding is idempotent: everything is matched by workspace name / email / agent
name, so restarting the server never duplicates rows. No request history is
written -- the empty state is part of the demo.
"""
from __future__ import annotations

import sqlite3

from veto import auth
from veto import routines as veto_routines
from veto.store import Store

WORKSPACE_NAME = "Bracken & Field"
DEMO_PASSWORD = "veto-demo-2026"
ADMIN_EMAIL = "ops@brackenfield.demo"
ADMIN_NAME = "Ops admin"
APPROVER_EMAIL = "caroline@brackenfield.demo"
APPROVER_NAME = "Caroline Hayes"
# Round-4 rename: a workspace seeded before D1 still carries this identity.
LEGACY_APPROVER_EMAIL = "priya@brackenfield.demo"
LEGACY_APPROVER_NAME = "Priya Raman"
MONTHLY_BUDGET_CENTS = 285_000

# The six rules the charter UI exposes today. (expires_at is a consent
# property, not a per-agent toggle, so it is not in this list.)
ALL_RULES = (
    "monthly_cap",
    "per_order_cap",
    "per_unit_cap",
    "vendor_allowlist",
    "category_allowlist",
    "approval_threshold",
)

AGENTS = (
    {
        "name": "Pantry runner",
        "tag": "pantry",
        "channel": "#pantry",
        "monthly_cap_cents": 80_000,
        "per_order_cap_cents": 30_000,
        "per_unit_cap_cents": 1_500,
        "approval_threshold_cents": 5_000,
        "vendors": ["grove.co", "whogivesacrap.org", "walmart.com", "target.com", "amazon.com"],
        "categories": ["pantry"],
        "rules": list(ALL_RULES),
        "overrides": [],
        "routine": {
            "name": "Pantry restock",
            "instruction": "Restock the pantry: 2 packs of toilet paper and a bottle of dish soap.",
            "cadence": "weekly",
            "envelope_cents": 25_000,
            "dedupe_days": 10,
        },
    },
    {
        "name": "Desk kit",
        "tag": "equipment",
        "channel": "#studio",
        "monthly_cap_cents": 120_000,
        "per_order_cap_cents": 60_000,
        "per_unit_cap_cents": 15_000,
        "approval_threshold_cents": 5_000,
        "vendors": ["staples.com", "walmart.com", "target.com", "logitech.com", "amazon.com"],
        "categories": ["equipment", "stationery"],
        "rules": list(ALL_RULES),
        "overrides": [
            {"category": "stationery", "kind": "per_unit_cap", "value_cents": 5_000},
        ],
        "routine": {
            "name": "Desk kit top-up",
            "instruction": "Top up the desk kit: a box of pens and two packs of sticky notes.",
            "cadence": "monthly",
            "envelope_cents": 30_000,
            "dedupe_days": 14,
        },
        # A second job on purpose: the agent page shows one card per schedule, and
        # the demo should show that an agent can own more than one.
        "extra_routines": [
            {
                "name": "Paper and envelopes",
                "instruction": "Buy a ream of printer paper and a box of envelopes",
                "cadence": "monthly",
                "envelope_cents": 5_000,
                "dedupe_days": 21,
            },
        ],
    },
    {
        "name": "Client lunch",
        "tag": "hospitality",
        "channel": "#finance",
        "monthly_cap_cents": 60_000,
        "per_order_cap_cents": 25_000,
        "per_unit_cap_cents": None,
        "approval_threshold_cents": 10_000,
        "vendors": ["doordash.com", "grubhub.com", "sweetgreen.com"],
        "categories": ["meals"],
        "rules": ["vendor_allowlist", "per_order_cap", "approval_threshold"],
        "overrides": [],
    },
    {
        "name": "Studio ops",
        "tag": "facilities",
        "channel": "#ops",
        "monthly_cap_cents": 40_000,
        "per_order_cap_cents": 15_000,
        "per_unit_cap_cents": 800,
        "approval_threshold_cents": 3_000,
        "vendors": ["staples.com", "amazon.com"],
        "categories": ["stationery"],
        "rules": list(ALL_RULES),
        "overrides": [
            {"category": "stationery", "kind": "per_unit_cap", "value_cents": 800},
        ],
        "routine": {
            "name": "Studio supplies",
            "instruction": "Order studio supplies: two packs of envelopes and a box of pens.",
            "cadence": "monthly",
            "envelope_cents": 8_000,
            "dedupe_days": 20,
        },
    },
)

DEFAULT_AGENT_NAME = AGENTS[0]["name"]


def _ensure_workspace(store: Store) -> dict:
    workspace = store.workspace_by_name(WORKSPACE_NAME)
    if workspace is not None:
        return workspace
    # Settings lets an admin rename the workspace, so "the demo workspace" is
    # found by its seeded admin identity first; the name check above is only the
    # fast path. Without this, a rename would make the next boot seed a second
    # workspace and duplicate the demo.
    admin = store.user_by_email(ADMIN_EMAIL)
    if admin is not None:
        existing = store.workspace(admin["workspace_id"])
        if existing is not None:
            return existing
    return store.create_workspace(
        WORKSPACE_NAME,
        currency="USD",
        monthly_budget_cents=MONTHLY_BUDGET_CENTS,
    )


def _ensure_user(store: Store, workspace_id: str, email: str, name: str, role: str) -> dict:
    existing = store.user_by_email(email)
    if existing is not None:
        return existing
    return store.create_user(
        workspace_id,
        email,
        name=name,
        password_hash=auth.hash_password(DEMO_PASSWORD),
        role=role,
    )


def migrate_demo_identity(store: Store) -> bool:
    """Rename a DB seeded before the D1 identity change, idempotently.

    The demo approver used to be "Priya Raman" <priya@brackenfield.demo>; round 4
    makes her "Caroline Hayes" <caroline@brackenfield.demo>. The legacy email is
    globally unique, so matching it finds exactly the seeded row no matter how
    the workspace or data evolved. User ids never change, so existing sessions
    and every request that points at this approver stay valid. Returns True only
    when a row actually changed; safe to call on every boot.
    """
    with store.transaction():
        legacy = store.user_by_email(LEGACY_APPROVER_EMAIL)
        current = store.user_by_email(APPROVER_EMAIL)
        if legacy is not None and current is None:
            store.update_user(legacy["id"], email=APPROVER_EMAIL, name=APPROVER_NAME)
            return True
        if current is not None and (current.get("name") or "").strip() == LEGACY_APPROVER_NAME:
            # The email moved but the display name was left behind (partial or
            # hand-edited migration): finish the rename.
            store.update_user(current["id"], name=APPROVER_NAME)
            return True
    return False


def _ensure_agents(store: Store, workspace_id: str) -> None:
    existing = {agent["name"] for agent in store.list_agents(workspace_id)}
    for spec in AGENTS:
        if spec["name"] in existing:
            continue
        store.create_agent(
            workspace_id,
            {key: value for key, value in spec.items()
             if key not in ("vendors", "categories", "rules", "overrides", "routine")},
            vendors=spec["vendors"],
            categories=spec["categories"],
            rules=spec["rules"],
            overrides=spec["overrides"],
        )


def _ensure_routines(store: Store, workspace_id: str) -> None:
    """Give each demo agent its standing jobs once; never reset a live schedule.

    A spec carries one ``routine`` and optionally ``extra_routines``. Seeding is
    idempotent per instruction, so a second job added to a spec later appears on
    an existing database without duplicating the first one.
    """
    agents = {agent["name"]: agent for agent in store.list_agents(workspace_id)}
    for spec in AGENTS:
        agent = agents.get(spec["name"])
        if agent is None:
            continue
        specs = [spec["routine"]] if spec.get("routine") else []
        specs.extend(spec.get("extra_routines") or [])
        existing = {
            routine["instruction"]
            for routine in store.routines_for_agent(workspace_id, agent["id"])
        }
        for routine_spec in specs:
            if routine_spec["instruction"] in existing:
                continue
            store.create_routine(
                workspace_id,
                agent["id"],
                name=routine_spec["name"],
                instruction=routine_spec["instruction"],
                cadence=routine_spec["cadence"],
                envelope_cents=routine_spec["envelope_cents"],
                dedupe_days=routine_spec["dedupe_days"],
                next_run_at=veto_routines.next_run_at(routine_spec["cadence"]),
                created_by="demo",
            )
            existing.add(routine_spec["instruction"])


def _populate(store: Store) -> dict:
    workspace = _ensure_workspace(store)
    admin = _ensure_user(store, workspace["id"], ADMIN_EMAIL, ADMIN_NAME, "admin")
    approver = _ensure_user(
        store, workspace["id"], APPROVER_EMAIL, APPROVER_NAME, "approver"
    )
    _ensure_agents(store, workspace["id"])
    _ensure_routines(store, workspace["id"])

    if store.wallet(workspace["id"]) is None:
        store.set_wallet(workspace["id"], status="disconnected")

    workspace = store.workspace_by_name(WORKSPACE_NAME) or workspace
    if not workspace.get("default_agent_id"):
        pantry = next(
            (agent for agent in store.list_agents(workspace["id"])
             if agent["name"] == DEFAULT_AGENT_NAME),
            None,
        )
        if pantry is not None:
            workspace = store.update_workspace(
                workspace["id"], default_agent_id=pantry["id"]
            ) or workspace

    return {
        "workspace": workspace,
        "admin": admin,
        "approver": approver,
        "agents": store.list_agents(workspace["id"]),
    }


def seed_demo(store: Store) -> dict:
    """Create the demo state if missing and return what a caller can log in as.

    Returns ``{"workspace", "admin", "approver", "agents"}``. Safe to call on
    every boot: existing rows are reused rather than duplicated, and a DB seeded
    under the old demo identity is renamed first.
    """
    migrate_demo_identity(store)
    try:
        with store.transaction():
            return _populate(store)
    except sqlite3.IntegrityError:
        # A second process won the insert race. Read whatever it committed;
        # every missing piece is then created by the idempotent pass.
        return _populate(store)
