"""Routines: an agent's standing job, and the runs that prove it is doing it.

An agent is not only a set of limits. It is a role with recurring work: restock
the pantry every week, top up the desk kit every month. A routine is that job —
a standing instruction, a cadence, an envelope for the month, and a dedupe
window. Each due run becomes a real request through the same pipeline every
other request takes, so nothing about the rules, consent or capture is special.

Two properties matter for money:

* the scheduler claims a due slot exactly once (``Store.claim_routine`` moves
  ``next_run_at`` in the same statement), so a restart or two ticks cannot
  order twice;
* a run cannot quietly exceed the routine's envelope. The envelope is checked
  before the pipeline (skip when used up) and after pricing (a cart past the
  remaining envelope escalates to a human instead of capturing).
"""
from __future__ import annotations

import calendar
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

from veto import requests as veto_requests
from veto.events import FEED
from veto.store import month_start_iso, now_iso

CADENCES = ("daily", "weekly", "monthly")
CADENCE_WORD = {"daily": "every day", "weekly": "every week", "monthly": "every month"}

# Statuses a routine run can end in, in the words the run history shows.
_OUTCOME_BY_STATUS = {
    "captured": "captured",
    "awaiting": "awaiting",
    "approved": "approved",
    "blocked": "blocked",
}


# ---------------------------------------------------------------------------
# schedule arithmetic
# ---------------------------------------------------------------------------
def _add_month(moment: datetime) -> datetime:
    year = moment.year + (1 if moment.month == 12 else 0)
    month = 1 if moment.month == 12 else moment.month + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def next_run_at(cadence: str, *, start: datetime | None = None) -> str:
    """The next slot after ``start`` (default: now), in the store's format."""
    moment = start or datetime.now(timezone.utc)
    if cadence == "daily":
        nxt = moment + timedelta(days=1)
    elif cadence == "weekly":
        nxt = moment + timedelta(days=7)
    elif cadence == "monthly":
        nxt = _add_month(moment)
    else:
        raise ValueError("cadence must be daily, weekly or monthly")
    return nxt.isoformat(timespec="seconds")


def cadence_word(cadence: str) -> str:
    return CADENCE_WORD.get(cadence, "on a schedule")


# ---------------------------------------------------------------------------
# one run
# ---------------------------------------------------------------------------
def _record_run(
    store,
    routine: dict,
    *,
    due_at: str,
    started_at: str,
    outcome: str,
    request_id: str | None = None,
    amount_cents: int = 0,
    note: str | None = None,
) -> dict:
    run = store.create_routine_run(
        routine["id"],
        routine["workspace_id"],
        due_at=due_at,
        started_at=started_at,
        finished_at=now_iso(),
        outcome=outcome,
        request_id=request_id,
        amount_cents=amount_cents,
        note=note,
    )
    store.update_routine(
        routine["workspace_id"], routine["id"], fields={"last_run_at": started_at}
    )
    return run


def run_once(
    store,
    workspace: dict,
    routine: dict,
    *,
    settlement=None,
    progress=None,
    due_at: str | None = None,
    now: datetime | None = None,
    extract_fn=None,
    search_fn=None,
    choose_fn=None,
) -> dict:
    """Run a routine once and record what happened. Returns the run row.

    ``progress`` is the pipeline's callback, so a manual run can be streamed to
    the console exactly like a dry run. ``extract_fn`` / ``search_fn`` /
    ``choose_fn`` exist for the tests; the scheduler passes none of them.
    """
    moment = now or datetime.now(timezone.utc)
    started = moment.isoformat(timespec="seconds")
    due = due_at or started
    routine_id = routine["id"]

    agent = store.agent(workspace["id"], routine["agent_id"])
    if agent is None:
        return _record_run(
            store, routine, due_at=due, started_at=started, outcome="failed",
            note="The agent behind this routine no longer exists.",
        )
    if (agent.get("status") or "active") != "active":
        # The brake on the agent outranks the schedule: the job keeps its slot,
        # records the skip, and starts ordering again when the agent resumes.
        return _record_run(
            store, routine, due_at=due, started_at=started, outcome="skipped",
            note="The agent is paused, so the job did not order.",
        )

    # A routine is a standing order, not a loop. If it already put an order in
    # flight inside the dedupe window, this run is a recorded skip.
    dedupe_days = int(routine.get("dedupe_days") or 0)
    if dedupe_days > 0:
        cutoff = (moment - timedelta(days=dedupe_days)).isoformat(timespec="seconds")
        last = store.last_routine_order(routine_id, since=cutoff)
        if last is not None:
            return _record_run(
                store, routine, due_at=due, started_at=started, outcome="skipped",
                note=f"Already ordered on {last.get('request_id')} inside the "
                     f"{dedupe_days}-day window.",
            )

    envelope = int(routine.get("envelope_cents") or 0)
    remaining: int | None = None
    if envelope:
        month = store.routine_month_cents(routine_id, since=month_start_iso(moment))
        used = month["captured_cents"] + month["committed_cents"]
        remaining = max(0, envelope - used)
        if remaining <= 0:
            # The month's spend is not this run's amount: a skip moved no money.
            return _record_run(
                store, routine, due_at=due, started_at=started, outcome="skipped",
                note=(f"The month's envelope is already used "
                      f"(${used / 100:,.2f} of ${envelope / 100:,.2f}); "
                      f"no run this time."),
            )

    try:
        record = veto_requests.create(
            store,
            workspace=workspace,
            agent=agent,
            text=routine["instruction"],
            origin="api",
            settlement=settlement,
            routine={
                "id": routine_id,
                "name": routine["name"],
                "remaining_cents": remaining,
            },
            progress=progress,
            extract_fn=extract_fn,
            search_fn=search_fn,
            choose_fn=choose_fn,
        )
    except veto_requests.RequestError as exc:
        return _record_run(
            store, routine, due_at=due, started_at=started, outcome="failed",
            note=exc.message,
        )

    outcome = _OUTCOME_BY_STATUS.get(record["status"], "failed")
    notes = {
        "captured": "Ordered and captured through PayPal.",
        "awaiting": "Escalated: a human has to sign this run.",
        "approved": "Approved; the capture waits for the wallet.",
        "blocked": "Refused by the agent's rules.",
    }
    run = _record_run(
        store, routine,
        due_at=due,
        started_at=started,
        outcome=outcome,
        request_id=record.get("id"),
        amount_cents=int(record.get("total_cents") or 0),
        note=notes.get(outcome, ""),
    )
    run["request"] = record
    return run


# ---------------------------------------------------------------------------
# the scheduler
# ---------------------------------------------------------------------------
def tick(
    store,
    settlement=None,
    *,
    now: datetime | None = None,
    limit: int = 10,
    extract_fn=None,
    search_fn=None,
    choose_fn=None,
) -> list[dict]:
    """Run every due routine once. Called by the scheduler and by the tests."""
    moment = now or datetime.now(timezone.utc)
    stamp = moment.isoformat(timespec="seconds")
    runs: list[dict] = []
    for routine in store.due_routines(now=stamp, limit=limit):
        workspace = store.workspace(routine["workspace_id"])
        if workspace is None:
            continue
        # Move the schedule first: a second tick (or a restart) must not run
        # this due slot again. A missed slot is caught up once, then cadence
        # restarts from now, so a sleeping host cannot produce a burst.
        if not store.claim_routine(
            routine["id"],
            expected_next=routine["next_run_at"],
            next_run_at=next_run_at(routine["cadence"], start=moment),
        ):
            continue
        try:
            run = run_once(
                store, workspace, routine, settlement=settlement,
                due_at=routine["next_run_at"], now=moment,
                extract_fn=extract_fn, search_fn=search_fn, choose_fn=choose_fn,
            )
        except Exception:  # noqa: BLE001 - one bad routine must not stop the tick
            traceback.print_exc()
            run = _record_run(
                store, routine,
                due_at=routine["next_run_at"],
                started_at=stamp,
                outcome="failed",
                note="The scheduler could not run this routine.",
            )
        runs.append(run)
    if runs:
        # One refresh signal for the console, however many jobs ran. A run that
        # produced a request also moves the inbox numbers.
        FEED.publish("agents")
        if any(run.get("request_id") for run in runs):
            FEED.publish("requests")
    return runs


def start_scheduler(store, settlement=None, *, interval: float = 30.0) -> threading.Thread:
    """A daemon ticker: the runner is one process, so the schedule lives in it."""

    def loop() -> None:
        while True:
            try:
                tick(store, settlement)
            except Exception:  # noqa: BLE001 - the loop outlives any one failure
                traceback.print_exc()
            time.sleep(max(5.0, float(interval)))

    thread = threading.Thread(target=loop, name="veto-routines", daemon=True)
    thread.start()
    return thread


# ---------------------------------------------------------------------------
# read models
# ---------------------------------------------------------------------------
def public_routine(store, workspace: dict, routine: dict) -> dict:
    """The routine as the app sees it: the job, its schedule, and its month."""
    envelope = int(routine.get("envelope_cents") or 0)
    month = store.routine_month_cents(routine["id"], since=month_start_iso())
    used = month["captured_cents"] + month["committed_cents"]
    return {
        "id": routine["id"],
        "agent_id": routine["agent_id"],
        "name": routine["name"],
        "instruction": routine["instruction"],
        "cadence": routine["cadence"],
        "cadence_word": cadence_word(routine["cadence"]),
        "status": routine["status"],
        "envelope_cents": envelope,
        "spent_cents": month["captured_cents"],
        "committed_cents": month["committed_cents"],
        "remaining_cents": max(0, envelope - used) if envelope else None,
        "dedupe_days": int(routine.get("dedupe_days") or 0),
        "next_run_at": routine.get("next_run_at"),
        "last_run_at": routine.get("last_run_at"),
        "created_at": routine.get("created_at"),
    }


def public_run(run: dict) -> dict:
    return {
        "id": run.get("id"),
        "routine_id": run.get("routine_id"),
        "due_at": run.get("due_at"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "outcome": run.get("outcome"),
        "request_id": run.get("request_id"),
        "amount_cents": int(run.get("amount_cents") or 0),
        "note": run.get("note"),
    }
