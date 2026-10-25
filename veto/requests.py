"""The request lifecycle: what gets written, and what happens next.

The pipeline decides what the request is and what the rules say. This module owns
the board around it: the stored request, its lines and reasons, the consent, the
payment, the event trail, and the transitions a human or a PayPal call can cause.

State machine (``requests.status``)::

    blocked   ── the rules refused it; no consent, no PayPal object
    awaiting  ── a human has to sign; the money is committed, not moving
    captured  ── settled; the one state that counts as spend
    vetoed    ── a human refused a review request
    voided    ── a pending request was withdrawn before capture
    approved  ── green-lit, capture still owed (wallet missing or PayPal failed)

Only ``create`` and the deciding calls write. Nothing here talks to a catalogue or
a model directly: the pipeline already did that before the first row appears.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from veto import pipeline as veto_pipeline
from veto import policy
from veto.settlement import Settlement, SettlementError, VaultRequired
from veto.store import Store, month_start_iso, now_iso, parse_iso

CURRENCY = "USD"
CONSENT_TTL_MINUTES = 10
MAX_TEXT_CHARS = 4000

STATUS_BLOCKED = "blocked"
STATUS_AWAITING = "awaiting"
STATUS_APPROVED = "approved"
STATUS_CAPTURED = "captured"
STATUS_VETOED = "vetoed"
STATUS_VOIDED = "voided"

PENDING_STATUSES = (STATUS_AWAITING, STATUS_APPROVED)

_OUTCOME_STATUS = {
    "block": STATUS_BLOCKED,
    "review": STATUS_AWAITING,
    "auto": STATUS_APPROVED,
}


class RequestError(Exception):
    """A lifecycle failure with an HTTP status and a machine-readable code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class WalletRequired(RequestError):
    """The request is approved but the payment needs a payer approval first."""

    def __init__(self, approve_url: str = "", setup_id: str = "") -> None:
        super().__init__(
            428,
            "wallet_required",
            "Approve the company wallet once, then send the request again.",
        )
        self.approve_url = approve_url
        self.setup_id = setup_id


_DEFAULT_SETTLEMENT: Settlement | None = None


def default_settlement() -> Settlement:
    """One simulator / client per process, built on first use."""
    global _DEFAULT_SETTLEMENT
    if _DEFAULT_SETTLEMENT is None:
        _DEFAULT_SETTLEMENT = Settlement()
    return _DEFAULT_SETTLEMENT


# ---------------------------------------------------------------------------
# building the record
# ---------------------------------------------------------------------------
def cart_hash(agent_id: str, lines: list[dict], total_cents: int,
              currency: str = CURRENCY) -> str:
    """sha256 of the canonical cart: the thing a human consents to, not a promise."""
    payload = {
        "agent": agent_id,
        "currency": currency,
        "lines": [
            [
                line.get("sku") or line.get("title") or "",
                int(line.get("qty") or 1),
                int(line.get("unit_cents") or 0),
                int(line.get("line_cents") or 0),
            ]
            for line in lines
        ],
        "total_cents": int(total_cents),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _title(items: list[dict]) -> str:
    names = [str(item.get("name") or "").strip() for item in items[:2]]
    joined = " and ".join(name for name in names if name) or "request"
    return joined[:1].upper() + joined[1:120]


def _consent(record: dict) -> tuple[str, str]:
    """(nonce, expires_at) of the consent row a request carries."""
    consents = record.get("consents") or []
    if not consents:
        raise RequestError(409, "no_consent", "This request has no consent to spend.")
    consent = consents[0]
    return consent.get("nonce") or "", consent.get("expires_at") or ""


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# a cart a human pinned
# ---------------------------------------------------------------------------
def _pinned_result(
    agent: dict,
    raw_lines: list[dict],
    *,
    month_spent_cents: int,
    workspace_monthly_budget_cents: int | None,
) -> dict:
    """Price a hand-picked cart and judge it. No extract, no search, no chooser."""
    lines: list[dict] = []
    items: list[dict] = []
    for raw in raw_lines or []:
        title = str(raw.get("title") or "").strip()
        try:
            unit = int(raw.get("unit_cents") or 0)
            qty = max(1, min(99, int(raw.get("qty") or 1)))
        except (TypeError, ValueError):
            raise RequestError(400, "invalid_cart", "That pick has no usable price.") from None
        if not title or unit <= 0:
            raise RequestError(400, "invalid_cart", "That pick has no priced product.")
        line = {
            "sku": str(raw.get("sku") or ""),
            "title": title[:120],
            "qty": qty,
            "unit_cents": unit,
            "line_cents": unit * qty,
            "vendor": str(raw.get("vendor") or ""),
            "category": str(raw.get("category") or "").lower(),
            "catalogue_category": str(raw.get("catalogue_category") or ""),
            "brand": str(raw.get("brand") or ""),
            "image": str(raw.get("image") or ""),
            "url": str(raw.get("url") or ""),
            "source": str(raw.get("source") or "channel3"),
        }
        lines.append(line)
        items.append({"name": title, "qty": qty})
    if not lines:
        raise RequestError(400, "invalid_cart", "A pick needs at least one product.")

    total_cents = sum(line["line_cents"] for line in lines)
    events = [{
        "kind": "pick",
        "label": "Chosen from a check",
        "detail": f"{len(lines)} item(s) picked by a person, not the model",
    }]
    events.append({
        "kind": "price",
        "label": "Cart priced",
        "detail": " · ".join(f"{line['qty']} x {line['title']}" for line in lines),
    })
    decision = policy.evaluate(
        agent=agent,
        lines=lines,
        month_spent_cents=month_spent_cents,
        workspace_monthly_budget_cents=workspace_monthly_budget_cents,
    )
    events.append({
        "kind": "decision",
        "label": f"Decision: {decision['outcome']}",
        "detail": "; ".join(reason["message"] for reason in decision["reasons"])
                  or "within every rule",
    })
    return {
        "items": items,
        "lines": lines,
        "total_cents": total_cents,
        "currency": CURRENCY,
        "decision": decision,
        "events": events,
        "source": lines[0]["source"],
        "dry_run": False,
    }


def create(
    store: Store,
    *,
    workspace: dict,
    agent: dict,
    text: str,
    origin: str = "api",
    channel: str | None = None,
    requester: str | None = None,
    initiator_user_id: str | None = None,
    settlement: Settlement | None = None,
    route: dict | None = None,
    routine: dict | None = None,
    pinned_lines: list[dict] | None = None,
    progress=None,
    extract_fn=None,
    search_fn=None,
    choose_fn=None,
) -> dict:
    """Run the pipeline, persist the request and settle it if the rules allow.

    ``routine`` carries the standing job this run belongs to: its remaining
    monthly envelope can turn an ``auto`` decision into a review, so a routine
    cannot quietly spend past the ceiling a human approved for it. ``progress``
    is the pipeline's callback, used by the streamed endpoints.

    ``pinned_lines`` is a cart a human picked from a veto-check: the pipeline
    starts at PRICE instead of EXTRACT/SOURCE/CHOOSE, then judges, consents and
    settles exactly like any other request. The line still has to pass policy.
    """
    body = str(text or "").strip()
    if not body:
        raise RequestError(400, "missing_text", "A request needs something to buy.")
    body = body[:MAX_TEXT_CHARS]

    month_start = month_start_iso()
    month_spent = store.spent_cents(
        workspace["id"], agent_id=agent["id"], since=month_start
    )
    if pinned_lines is not None:
        result = _pinned_result(
            agent,
            pinned_lines,
            month_spent_cents=month_spent,
            workspace_monthly_budget_cents=workspace.get("monthly_budget_cents"),
        )
    else:
        try:
            result = veto_pipeline.simulate(
                agent,
                body,
                extract_fn=extract_fn,
                search_fn=search_fn,
                choose_fn=choose_fn,
                progress=progress,
                month_spent_cents=month_spent,
                workspace_monthly_budget_cents=workspace.get("monthly_budget_cents"),
            )
        except ValueError as exc:
            raise RequestError(400, "no_intent", str(exc)) from exc

    decision = result["decision"]
    outcome = decision["outcome"]
    lines = result["lines"]
    total_cents = int(result["total_cents"])

    if routine and outcome == "auto":
        remaining = routine.get("remaining_cents")
        if remaining is not None and total_cents > int(remaining):
            # The envelope is a ceiling on the job, not on one cart. A run that
            # crosses it becomes a review instead of an unsupervised capture.
            message = (
                f"This run would spend ${total_cents / 100:,.2f}; the routine has "
                f"${int(remaining) / 100:,.2f} left this month."
            )
            decision = {
                **decision,
                "outcome": "review",
                "reasons": [
                    *(decision.get("reasons") or []),
                    {
                        "rule": "routine_envelope",
                        "severity": "wait",
                        "message": message,
                        "observed_cents": total_cents,
                        "limit_cents": int(remaining),
                        "unblock": "Approve this run, or raise the routine's envelope.",
                    },
                ],
            }
            outcome = "review"
            result["decision"] = decision
            result["events"] = [
                {**event, "label": "Decision: review", "detail": message}
                if event.get("kind") == "decision" else event
                for event in (result.get("events") or [])
            ] + [{
                "kind": "envelope",
                "label": "Routine envelope reached",
                "detail": f"${int(remaining) / 100:,.2f} left · waiting for a signature",
            }]

    status = _OUTCOME_STATUS.get(outcome, STATUS_BLOCKED)

    events = list(result.get("events") or [])
    if route:
        # the routing decision happened before extraction; the trail keeps that order
        events.insert(0, route)
    consent = None
    if outcome != "block":
        issued = datetime.now(timezone.utc)
        expires = issued + timedelta(minutes=CONSENT_TTL_MINUTES)
        cart = cart_hash(agent["id"], lines, total_cents)
        consent = {
            "cart_hash": cart,
            "nonce": uuid.uuid4().hex,
            "issued_at": issued.isoformat(timespec="seconds"),
            "expires_at": expires.isoformat(timespec="seconds"),
        }
        events.append({
            "kind": "consent",
            "label": "Consent issued",
            "detail": f"sha256:{cart[:12]}… · single-use · {CONSENT_TTL_MINUTES} min",
        })

    record = store.create_request(
        workspace["id"],
        agent["id"],
        origin=origin,
        status=status,
        title=_title(result.get("items") or []),
        text=body,
        channel=channel or agent.get("channel"),
        requester=requester or initiator_user_id,
        decision_outcome=outcome,
        subtotal_cents=total_cents,
        total_cents=total_cents,
        currency=result.get("currency") or CURRENCY,
        initiator_user_id=initiator_user_id,
        lines=lines,
        reasons=decision.get("reasons") or [],
        events=events,
        consent=consent,
    )

    if outcome != "auto":
        return record

    # Inside every rule: the agent pays now, without asking. A missing wallet is
    # recorded on the request rather than refusing it; nothing is captured.
    try:
        return _settle(
            store,
            workspace,
            record,
            settlement=settlement,
            approver_user_id=None,
            raise_on_wallet=False,
            raise_on_failure=False,
        )
    except RequestError:
        return store.request(workspace["id"], record["id"]) or record


# ---------------------------------------------------------------------------
# settle
# ---------------------------------------------------------------------------
def _settle(
    store: Store,
    workspace: dict,
    record: dict,
    *,
    settlement: Settlement | None,
    approver_user_id: str | None,
    raise_on_wallet: bool = True,
    raise_on_failure: bool = True,
) -> dict:
    """Issue the PayPal objects for an approved request, then write the proof.

    The consent's nonce is the idempotency key, so a retried approval replays the
    same order / authorization / capture instead of charging twice.
    """
    money_mover = settlement or default_settlement()
    nonce = _consent(record)[0]
    payment = store.create_payment(
        record["id"],
        mode=money_mover.mode,
        amount_cents=record["total_cents"],
    )
    try:
        result = money_mover.capture(
            store,
            workspace,
            request_id=record["id"],
            title=record.get("title") or record["id"],
            total_cents=int(record["total_cents"]),
            currency=record.get("currency") or CURRENCY,
            nonce=nonce,
        )
    except VaultRequired as exc:
        store.update_payment(payment["id"], status="wallet_required")
        store.add_event(
            record["id"],
            "wallet",
            "Wallet not connected",
            detail="approved, capture waits for a one-time PayPal approval",
        )
        if raise_on_wallet:
            raise WalletRequired(exc.approve_url, exc.setup_id) from exc
        return store.request(workspace["id"], record["id"]) or record
    except SettlementError as exc:
        store.update_payment(payment["id"], status="failed", **exc.partial)
        for step in exc.steps:
            store.add_event(record["id"], step["kind"], step["label"], detail=step.get("detail"))
        store.add_event(record["id"], "capture_failed", "Capture failed", detail=str(exc))
        if raise_on_failure:
            raise RequestError(502, "capture_failed", f"PayPal did not capture: {exc}") from exc
        return store.request(workspace["id"], record["id"]) or record

    for step in result.get("steps") or []:
        store.add_event(record["id"], step["kind"], step["label"], detail=step.get("detail"))
    store.update_payment(
        payment["id"],
        status=result.get("status") or "COMPLETED",
        vault_id=result.get("vault_id"),
        order_id=result.get("order_id"),
        authorization_id=result.get("authorization_id"),
        capture_id=result.get("capture_id"),
    )
    consents = record.get("consents") or []
    if consents:
        store.mark_consent_used(
            consents[0]["id"], approver_user_id=approver_user_id
        )
    store.update_request(
        workspace["id"],
        record["id"],
        status=STATUS_CAPTURED,
        approver_user_id=approver_user_id,
        decided_at=now_iso(),
    )
    return store.request(workspace["id"], record["id"]) or record


# ---------------------------------------------------------------------------
# decide / void
# ---------------------------------------------------------------------------
def _pending(store: Store, workspace: dict, request_id: str) -> dict:
    record = store.request(workspace["id"], request_id)
    if record is None:
        raise RequestError(404, "request_not_found", "That request does not exist.")
    return record


def approve(
    store: Store,
    *,
    workspace: dict,
    request_id: str,
    user: dict,
    settlement: Settlement | None = None,
) -> dict:
    """A human signs a review request and the money moves, or the wallet asks first."""
    record = _pending(store, workspace, request_id)
    if record["status"] != STATUS_AWAITING:
        raise RequestError(
            409,
            "invalid_state",
            f"{request_id} is {record['status']}, not awaiting a signature.",
        )
    consents = record.get("consents") or []
    if not consents:
        raise RequestError(409, "no_consent", "This request has no consent to spend.")
    consent = consents[0]
    if consent.get("used_at"):
        raise RequestError(409, "consent_used", "That consent has already been used.")
    expires = parse_iso(consent.get("expires_at"))
    if expires is not None and expires <= datetime.now(timezone.utc):
        raise RequestError(
            409,
            "consent_expired",
            "This cart's consent expired after 10 minutes. Ask the agent for a new request.",
        )
    store.add_event(
        record["id"],
        "approve",
        "Approved",
        detail=f"by {user.get('name') or user.get('email') or 'a human'}",
    )
    return _settle(
        store,
        workspace,
        record,
        settlement=settlement,
        approver_user_id=user["id"],
    )


def veto(
    store: Store,
    *,
    workspace: dict,
    request_id: str,
    user: dict,
) -> dict:
    """A human refuses a review request: no consent is used, no PayPal object exists."""
    record = _pending(store, workspace, request_id)
    if record["status"] != STATUS_AWAITING:
        raise RequestError(
            409,
            "invalid_state",
            f"{request_id} is {record['status']}, not awaiting a signature.",
        )
    store.update_request(
        workspace["id"],
        request_id,
        status=STATUS_VETOED,
        approver_user_id=user["id"],
        decided_at=now_iso(),
    )
    store.add_event(
        request_id,
        "veto",
        "Vetoed",
        detail=f"by {user.get('name') or user.get('email') or 'a human'} · "
               "zero PayPal objects",
    )
    return store.request(workspace["id"], request_id) or record


def void(
    store: Store,
    *,
    workspace: dict,
    request_id: str,
    user: dict,
) -> dict:
    """Withdraw a request before any capture. Captured money cannot be voided here."""
    record = _pending(store, workspace, request_id)
    if record["status"] == STATUS_CAPTURED:
        raise RequestError(
            409,
            "already_captured",
            "That money is already captured. Refunds are outside this build.",
        )
    if record["status"] not in PENDING_STATUSES:
        raise RequestError(
            409,
            "invalid_state",
            f"{request_id} is {record['status']}, not pending.",
        )
    store.update_request(
        workspace["id"],
        request_id,
        status=STATUS_VOIDED,
        approver_user_id=user["id"],
        decided_at=now_iso(),
    )
    store.add_event(
        request_id,
        "void",
        "Voided",
        detail=f"by {user.get('name') or user.get('email') or 'a human'} · no capture attempted",
    )
    return store.request(workspace["id"], request_id) or record


# ---------------------------------------------------------------------------
# serialisation
# ---------------------------------------------------------------------------
def public_summary(row: dict) -> dict:
    """The list shape: no lines, no events, just what an inbox row needs."""
    return {
        "id": row.get("id"),
        "seq": row.get("seq"),
        "title": row.get("title") or "",
        "status": row.get("status"),
        "decision": row.get("decision_outcome"),
        "total_cents": row.get("total_cents") or 0,
        "currency": row.get("currency") or CURRENCY,
        "channel": row.get("channel"),
        "requester": row.get("requester"),
        "origin": row.get("origin"),
        "line_count": row.get("line_count") or 0,
        "created_at": row.get("created_at"),
        "decided_at": row.get("decided_at"),
        "routine_name": row.get("routine_name"),
        "agent": {
            "id": row.get("agent_id"),
            "name": row.get("agent_name"),
            "tag": row.get("agent_tag"),
        },
    }


def public_request(record: dict, *, full: bool = True) -> dict:
    """The shape the API and the app share. PayPal ids are public; the nonce is not."""
    data = {
        "id": record["id"],
        "seq": record["seq"],
        "title": record.get("title") or "",
        "status": record["status"],
        "decision": record.get("decision_outcome"),
        "total_cents": record.get("total_cents") or 0,
        "currency": record.get("currency") or CURRENCY,
        "channel": record.get("channel"),
        "requester": record.get("requester"),
        "origin": record.get("origin"),
        "text": record.get("text") or "",
        "created_at": record.get("created_at"),
        "decided_at": record.get("decided_at"),
        "approver_user_id": record.get("approver_user_id"),
    }
    agent = record.get("agent") or {}
    data["agent"] = {
        "id": agent.get("id"),
        "name": agent.get("name"),
        "tag": agent.get("tag"),
        "channel": agent.get("channel"),
        "status": agent.get("status"),
    }
    routine = record.get("routine") or {}
    data["routine"] = (
        {
            "id": routine.get("id"),
            "name": routine.get("name"),
            "cadence": routine.get("cadence"),
            "run_id": routine.get("run_id"),
        }
        if routine
        else None
    )
    if not full:
        return data

    data["lines"] = [
        {
            "sku": line.get("sku"),
            "title": line.get("title"),
            "qty": line.get("qty"),
            "unit_cents": line.get("unit_cents"),
            "line_cents": line.get("line_cents"),
            "vendor": line.get("vendor"),
            "category": line.get("category"),
            "brand": line.get("brand"),
            "url": line.get("product_url"),
            "image": line.get("image_url"),
            "source": line.get("source"),
        }
        for line in record.get("lines") or []
    ]
    data["reasons"] = [
        {
            "rule": reason.get("rule"),
            "severity": reason.get("severity"),
            "message": reason.get("message"),
            "observed_cents": reason.get("observed_cents"),
            "limit_cents": reason.get("limit_cents"),
            "unblock": reason.get("unblock"),
        }
        for reason in record.get("reasons") or []
    ]
    data["consent"] = None
    for consent in record.get("consents") or []:
        data["consent"] = {
            "id": consent.get("id"),
            "cart_hash": consent.get("cart_hash"),
            "issued_at": consent.get("issued_at"),
            "expires_at": consent.get("expires_at"),
            "used_at": consent.get("used_at"),
            "approver_user_id": consent.get("approver_user_id"),
        }
        break
    payments = record.get("payments") or []
    data["payment"] = None
    if payments:
        latest = payments[-1]
        data["payment"] = {
            "id": latest.get("id"),
            "mode": latest.get("mode"),
            "status": latest.get("status"),
            "amount_cents": latest.get("amount_cents"),
            "order_id": latest.get("order_id"),
            "authorization_id": latest.get("authorization_id"),
            "capture_id": latest.get("capture_id"),
            "created_at": latest.get("created_at"),
            "updated_at": latest.get("updated_at"),
        }
    data["events"] = [
        {
            "seq": event.get("seq"),
            "kind": event.get("kind"),
            "label": event.get("label"),
            "detail": event.get("detail"),
            "at": event.get("at"),
        }
        for event in record.get("events") or []
    ]
    return data
