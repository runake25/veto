#!/usr/bin/env python3
"""Veto PayPal capability gate.

    python3 spike/gate.py --mock    # offline, no credentials, must exit 0
    python3 spike/gate.py --live    # real PayPal sandbox, env-driven

The mock proves the protocol shape, the status transitions and idempotency.
Only --live can prove that the PayPal sandbox merchant actually has the Vault
and card capabilities enabled.  This script never pretends otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import textwrap
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

try:  # works both as `python3 spike/gate.py` and `python3 -m spike.gate`
    from .paypal_client import (
        PRODUCTION_BASE,
        SANDBOX_BASE,
        PayPalClient,
        PayPalError,
        derive_request_id,
    )
    from .mock_paypal import (
        MOCK_CLIENT_ID,
        MOCK_CLIENT_SECRET,
        MOCK_MERCHANT_ID,
        MockPayPal,
    )
except ImportError:  # pragma: no cover - exercised by direct script execution
    from paypal_client import (  # type: ignore
        PRODUCTION_BASE,
        SANDBOX_BASE,
        PayPalClient,
        PayPalError,
        derive_request_id,
    )
    from mock_paypal import (  # type: ignore
        MOCK_CLIENT_ID,
        MOCK_CLIENT_SECRET,
        MOCK_MERCHANT_ID,
        MockPayPal,
    )


STATE_PATH = Path(__file__).resolve().parent / ".state" / "live-setup.json"
DEFAULT_RETURN_URL = "https://example.com/veto/paypal/return"
DEFAULT_CANCEL_URL = "https://example.com/veto/paypal/cancel"
TEST_AMOUNT_DEFAULT = "1.00"
TEST_CURRENCY_DEFAULT = "USD"
MOCK_AMOUNT = "1.00"
MOCK_CURRENCY = "USD"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def mask_card(number: str) -> str:
    digits = re.sub(r"\D", "", number or "")
    return "****" + digits[-4:] if len(digits) >= 4 else "****"


def normalize_expiry(raw: str) -> str:
    """Accept YYYY-MM or MM/YYYY or MM/YY and return YYYY-MM."""

    value = (raw or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}", value):
        year, month = int(value[:4]), int(value[5:])
    else:
        match = re.fullmatch(r"(\d{1,2})\s*/\s*(\d{2}|\d{4})", value)
        if not match:
            raise ValueError("card expiry must be YYYY-MM or MM/YYYY")
        month = int(match.group(1))
        year = int(match.group(2))
        if year < 100:
            year += 2000
    if not 1 <= month <= 12:
        raise ValueError("card expiry month must be 01..12")
    if year < 2000:
        raise ValueError("card expiry year looks invalid")
    return f"{year:04d}-{month:02d}"


def one_line_error(exc: BaseException) -> str:
    if isinstance(exc, PayPalError):
        bits = [
            f"{exc.status_code if exc.status_code is not None else 'no HTTP'} "
            f"{exc.name}: {exc.message}"
        ]
        if exc.issues:
            bits.append("issues=" + ",".join(exc.issues))
        if exc.debug_id:
            bits.append("debug_id=" + exc.debug_id)
        if exc.request_id:
            bits.append("request_id=" + exc.request_id)
        return " | ".join(bits)
    return f"{type(exc).__name__}: {exc}"


def order_needs_payer_action(order: Mapping[str, Any]) -> bool:
    if str(order.get("status") or "").upper() == "PAYER_ACTION_REQUIRED":
        return True
    for link in order.get("links") or []:
        if isinstance(link, Mapping) and link.get("rel") == "payer-action":
            return True
    return False


def extract_authorization(
    order: Mapping[str, Any],
) -> Optional[Mapping[str, Any]]:
    return PayPalClient.first_authorization(order)


def paypal_vault_setup_source(
    return_url: str = DEFAULT_RETURN_URL,
    cancel_url: str = DEFAULT_CANCEL_URL,
) -> Dict[str, Any]:
    return {
        "paypal": {
            "description": "Veto: save a company wallet for server-initiated payments",
            "usage_pattern": "IMMEDIATE",
            "usage_type": "MERCHANT",
            "customer_type": "CONSUMER",
            "permit_multiple_payment_tokens": False,
            "experience_context": {
                "brand_name": "Veto Capability Gate",
                "locale": "en-US",
                "return_url": return_url,
                "cancel_url": cancel_url,
            },
        }
    }


def paypal_order_payment_source(vault_id: str) -> Dict[str, Any]:
    return {
        "paypal": {
            "vault_id": vault_id,
            "stored_credential": {
                "payment_initiator": "MERCHANT",
                "usage": "SUBSEQUENT",
                "usage_pattern": "UNSCHEDULED_PREPAID",
            },
        }
    }


def card_order_payment_source(vault_id: str) -> Dict[str, Any]:
    return {
        "card": {
            "vault_id": vault_id,
            "stored_credential": {
                "payment_initiator": "MERCHANT",
                "payment_type": "UNSCHEDULED",
                "usage": "SUBSEQUENT",
            },
        }
    }


def card_vault_source(
    number: str,
    expiry: str,
    name: str,
    *,
    country: str = "US",
    postal_code: Optional[str] = None,
    cvv: Optional[str] = None,
    with_experience_context: bool = False,
    return_url: str = DEFAULT_RETURN_URL,
    cancel_url: str = DEFAULT_CANCEL_URL,
) -> Dict[str, Any]:
    card: Dict[str, Any] = {
        "number": re.sub(r"\D", "", number),
        "expiry": expiry,
        "name": name,
        "billing_address": {"country_code": country.upper()},
    }
    if postal_code:
        card["billing_address"]["postal_code"] = postal_code
    if cvv:
        card["security_code"] = cvv
    if with_experience_context:
        card["experience_context"] = {
            "brand_name": "Veto Capability Gate",
            "locale": "en-US",
            "return_url": return_url,
            "cancel_url": cancel_url,
        }
    return {"card": card}


def purchase_units(amount: str, currency: str) -> List[Dict[str, Any]]:
    return [
        {
            "reference_id": "veto-capability-gate",
            "amount": {"currency_code": currency, "value": amount},
        }
    ]


# ---------------------------------------------------------------------------
# Reporting / matrix
# ---------------------------------------------------------------------------
@dataclass
class Check:
    path: str
    step: str
    outcome: str
    detail: str = ""


class CheckFailure(Exception):
    """A fatal assertion inside one path; recorded before being raised."""


class Reporter:
    def __init__(self) -> None:
        self.checks: List[Check] = []

    def banner(self, title: str, subtitle: str = "") -> None:
        print("\n" + "=" * 80)
        print(title)
        if subtitle:
            print(subtitle)
        print("=" * 80)

    def section(self, title: str) -> None:
        print(f"\n-- {title} " + "-" * max(0, 75 - len(title)))

    def info(self, message: str) -> None:
        print(f"   {message}")

    def step(self, message: str) -> None:
        print(f"\n>> {message}")

    def warn(self, message: str) -> None:
        print(f"   WARN: {message}")

    def record(self, path: str, step: str, outcome: str, detail: str = "") -> None:
        normalized = outcome.upper().strip()
        aliases = {"PEND": "PENDING", "N/A": "N/A", "WARNING": "WARN"}
        normalized = aliases.get(normalized, normalized)
        if normalized not in ("PASS", "FAIL", "SKIP", "PENDING", "WARN", "N/A"):
            raise ValueError(f"bad outcome: {outcome}")
        self.checks.append(Check(path=path, step=step, outcome=normalized, detail=detail))
        marker = {
            "PASS": "[PASS]",
            "FAIL": "[FAIL]",
            "SKIP": "[SKIP]",
            "PENDING": "[PEND]",
            "WARN": "[WARN]",
            "N/A": "[N/A ]",
        }[normalized]
        suffix = f" — {detail}" if detail else ""
        print(f"   {marker} {path}: {step}{suffix}")

    def expect(
        self,
        condition: bool,
        path: str,
        step: str,
        pass_detail: str,
        fail_detail: Optional[str] = None,
        *,
        fatal: bool = True,
    ) -> bool:
        if condition:
            self.record(path, step, "PASS", pass_detail)
            return True
        self.record(path, step, "FAIL", fail_detail or pass_detail)
        if fatal:
            raise CheckFailure(fail_detail or pass_detail)
        return False

    def print_exception(self, exc: BaseException) -> None:
        if isinstance(exc, PayPalError):
            status = exc.status_code if exc.status_code is not None else "no HTTP"
            print(f"   ERROR {status} {exc.name}: {exc.message}")
            if exc.issues:
                print(f"         issues: {', '.join(exc.issues)}")
            if exc.detail_summary:
                print(f"         details: {exc.detail_summary}")
            if exc.debug_id:
                print(f"         debug_id: {exc.debug_id}")
            if exc.request_id:
                print(f"         PayPal-Request-Id: {exc.request_id}")
            if exc.method or exc.path:
                print(f"         request: {exc.method} {exc.path}")
        else:
            print(f"   ERROR {type(exc).__name__}: {exc}")

    def counts(self) -> Dict[str, int]:
        result = {"PASS": 0, "FAIL": 0, "SKIP": 0, "PENDING": 0, "WARN": 0, "N/A": 0}
        for check in self.checks:
            result[check.outcome] = result.get(check.outcome, 0) + 1
        return result

    @property
    def failures(self) -> List[Check]:
        return [c for c in self.checks if c.outcome == "FAIL"]

    def matrix(self) -> None:
        print("\n" + "=" * 80)
        print("PASS / FAIL MATRIX")
        print("=" * 80)
        if not self.checks:
            print("  (no checks ran)")
            return
        path_width = max(6, max(len(c.path) for c in self.checks))
        step_width = min(52, max(8, max(len(c.step) for c in self.checks)))
        print(f"  {'PATH':<{path_width}}  {'RESULT':<8}  {'STEP':<{step_width}}  DETAIL")
        print("  " + "-" * 76)
        detail_width = max(30, 110 - path_width - step_width - 14)
        for check in self.checks:
            detail = check.detail or ""
            wrapped = textwrap.wrap(detail, width=detail_width) or [""]
            print(
                f"  {check.path:<{path_width}}  {check.outcome:<8}  "
                f"{check.step:<{step_width}}  {wrapped[0]}"
            )
            for line in wrapped[1:]:
                print(
                    f"  {'':<{path_width}}  {'':<8}  {'':<{step_width}}  {line}"
                )
        print("=" * 80)
        counts = self.counts()
        print(
            "  TOTALS: "
            + "  ".join(
                f"{name}={counts.get(name, 0)}"
                for name in ("PASS", "FAIL", "SKIP", "PENDING", "WARN")
            )
        )

    def verdict(self, rows: Sequence[str], next_action: str) -> None:
        print("\n" + "=" * 80)
        print("VERDICT")
        print("=" * 80)
        for row in rows:
            print(f"  {row}")
        print("\n  NEXT ACTION (one):")
        for line in textwrap.wrap(next_action, width=74) or [next_action]:
            print(f"    {line}")
        print("=" * 80)


# ---------------------------------------------------------------------------
# Mock mode
# ---------------------------------------------------------------------------
def mock_path_a(client: PayPalClient, mock: MockPayPal, reporter: Reporter) -> bool:
    try:
        reporter.step("Path A — vault a PayPal wallet, then spend server-side")
        setup = client.create_setup_token(
            payment_source=paypal_vault_setup_source(),
            idempotency_key="mock-a-setup-token",
        )
        setup_id = str(setup.get("id") or "")
        approve_url = PayPalClient.find_link(setup, "approve") or ""
        reporter.expect(
            setup.get("status") == "PAYER_ACTION_REQUIRED" and bool(setup_id),
            "A",
            "Create setup token",
            f"id={setup_id} status=PAYER_ACTION_REQUIRED",
            f"unexpected setup response: {setup}",
        )
        reporter.expect(
            bool(approve_url),
            "A",
            "Approve link (Path C, setup only)",
            "approve URL present; mock payer approval is simulated below",
            "setup token has no approve link",
        )

        # A browser would do this.  The mock-only admin path exists so the
        # rest of the vault flow can be exercised offline.
        mock.approve_setup_token(setup_id)
        setup_after = client.get_setup_token(setup_id)
        reporter.expect(
            setup_after.get("status") == "APPROVED",
            "A",
            "Payer approval",
            "mock payer approval -> setup token APPROVED",
            f"setup token status is {setup_after.get('status')!r}, not APPROVED",
        )

        payment_token = client.create_payment_token(
            payment_source={"token": {"id": setup_id, "type": "SETUP_TOKEN"}},
            idempotency_key="mock-a-payment-token",
        )
        vault_id = str(payment_token.get("id") or "")
        customer_id = str((payment_token.get("customer") or {}).get("id") or "")
        reporter.expect(
            bool(vault_id) and "paypal" in (payment_token.get("payment_source") or {}),
            "A",
            "Create payment token",
            f"vault_id={vault_id} customer_id={customer_id}",
            f"unexpected payment token: {payment_token}",
        )

        listed = client.list_payment_tokens(customer_id=customer_id)
        listed_ids = [
            item.get("id") for item in listed.get("payment_tokens") or []
        ]
        reporter.expect(
            vault_id in listed_ids,
            "A",
            "List payment tokens",
            f"vault_id found in customer token list ({len(listed_ids)} token(s))",
            f"vault_id {vault_id} missing from list {listed_ids}",
        )

        create_key = "mock-a-order-create"
        order = client.create_order(
            intent="AUTHORIZE",
            purchase_units=purchase_units(MOCK_AMOUNT, MOCK_CURRENCY),
            payment_source=paypal_order_payment_source(vault_id),
            idempotency_key=create_key,
        )
        order_id = str(order.get("id") or "")
        reporter.expect(
            not order_needs_payer_action(order),
            "A",
            "Create order with vault_id",
            f"order={order_id} status={order.get('status')} — no payer-action link",
            f"order={order_id} requires payer action; this is Path C, not server-side spend",
        )
        expected_request_id = derive_request_id(create_key)
        create_logs = mock.requests_to("POST", "/v2/checkout/orders")
        sent_ids = [entry.get("request_id") for entry in create_logs]
        reporter.expect(
            expected_request_id in sent_ids,
            "A",
            "PayPal-Request-Id sent",
            f"header={expected_request_id} (derived from the idempotency key)",
            f"expected header {expected_request_id}, saw {sent_ids}",
        )

        # Duplicate create with the same idempotency key: the mock must replay
        # the original response rather than create a second order.
        duplicate = client.create_order(
            intent="AUTHORIZE",
            purchase_units=purchase_units(MOCK_AMOUNT, MOCK_CURRENCY),
            payment_source=paypal_order_payment_source(vault_id),
            idempotency_key=create_key,
        )
        replay_header = client.last_response_headers.get(
            "mock-idempotent-replay", ""
        )
        reporter.expect(
            duplicate.get("id") == order_id and replay_header == "true",
            "A",
            "Duplicate Request-Id replay (order)",
            f"same order id {order_id}; Mock-Idempotent-Replay=true",
            f"duplicate returned id={duplicate.get('id')} replay={replay_header!r}",
        )
        reporter.expect(
            len(mock.orders) == 1,
            "A",
            "Replay did not create a second order",
            "mock order store still has exactly 1 order",
            f"mock order store has {len(mock.orders)} orders after the duplicate",
        )

        fetched = client.get_order(order_id)
        reporter.expect(
            fetched.get("status") == "CREATED",
            "A",
            "Get order",
            "order status CREATED before authorization",
            f"get order returned {fetched.get('status')!r}",
        )

        auth_order = client.authorize_order(
            order_id, idempotency_key="mock-a-authorize"
        )
        auth = extract_authorization(auth_order)
        if not auth:
            raise CheckFailure(
                f"authorize response contains no authorization: {auth_order}"
            )
        auth_id = str(auth.get("id") or "")
        reporter.expect(
            bool(auth_id) and auth.get("status") == "CREATED",
            "A",
            "Authorize order",
            f"authorization={auth_id} status=CREATED; order={auth_order.get('status')}",
            f"unexpected authorization: {auth}",
        )
        fetched_auth = client.get_authorization(auth_id)
        reporter.expect(
            fetched_auth.get("status") == "CREATED",
            "A",
            "Get authorization",
            "authorization status CREATED; funds held",
            f"get authorization returned {fetched_auth.get('status')!r}",
        )

        capture_key = "mock-a-capture"
        capture = client.capture_authorization(
            auth_id, idempotency_key=capture_key
        )
        capture_id = str(capture.get("id") or "")
        reporter.expect(
            bool(capture_id) and capture.get("status") == "COMPLETED",
            "A",
            "Capture authorization",
            f"capture={capture_id} status=COMPLETED amount="
            f"{(capture.get('amount') or {}).get('value')} "
            f"{(capture.get('amount') or {}).get('currency_code')}",
            f"unexpected capture: {capture}",
        )

        # Same capture key again: must replay, not double-capture.
        captures_before = len(mock.captures)
        capture_duplicate = client.capture_authorization(
            auth_id, idempotency_key=capture_key
        )
        reporter.expect(
            capture_duplicate.get("id") == capture_id
            and len(mock.captures) == captures_before
            and client.last_response_headers.get("mock-idempotent-replay") == "true",
            "A",
            "Duplicate Request-Id replay (capture)",
            f"same capture id {capture_id}; no second capture",
            "duplicate capture did not replay the original capture",
        )

        completed = client.get_order(order_id)
        completed_capture = PayPalClient.first_capture(completed) or {}
        reporter.expect(
            completed.get("status") == "COMPLETED"
            and completed_capture.get("id") == capture_id
            and completed_capture.get("status") == "COMPLETED",
            "A",
            "Order after capture",
            f"order={order_id} status=COMPLETED capture={capture_id}",
            f"order={completed.get('status')} capture={completed_capture.get('id')}",
        )
        reporter.expect(
            client.get_authorization(auth_id).get("status") == "CAPTURED",
            "A",
            "Authorization after capture",
            "authorization status CAPTURED",
            "authorization did not move to CAPTURED",
        )
        status_history = completed.get("mock_status_history") or []
        reporter.expect(
            status_history[:4]
            == ["CREATED", "APPROVED", "AUTHORIZED", "COMPLETED"],
            "A",
            "Status lifecycle",
            "order history CREATED -> APPROVED -> AUTHORIZED -> COMPLETED "
            "(AUTHORIZED is a synthetic mock status)",
            f"unexpected mock status history: {status_history}",
        )

        # Separate authorization for the void path.  You cannot void a capture.
        void_order = client.create_order(
            intent="AUTHORIZE",
            purchase_units=purchase_units(MOCK_AMOUNT, MOCK_CURRENCY),
            payment_source=paypal_order_payment_source(vault_id),
            idempotency_key="mock-a-order-void",
        )
        void_auth_order = client.authorize_order(
            str(void_order.get("id") or ""),
            idempotency_key="mock-a-authorize-void",
        )
        void_auth = extract_authorization(void_auth_order) or {}
        void_auth_id = str(void_auth.get("id") or "")
        if not void_auth_id:
            raise CheckFailure("void flow authorize response contains no authorization")
        void_result = client.void_authorization(
            void_auth_id, idempotency_key="mock-a-void"
        )
        voided_auth = client.get_authorization(void_auth_id)
        reporter.expect(
            void_result is None and voided_auth.get("status") == "VOIDED",
            "A",
            "Void authorization",
            f"authorization={void_auth_id} status=VOIDED, no capture",
            f"void returned {void_result!r}; status={voided_auth.get('status')!r}",
        )
        voided_order = client.get_order(str(void_order.get("id") or ""))
        reporter.expect(
            voided_order.get("status") == "VOIDED",
            "A",
            "Order after void",
            "order status VOIDED",
            f"order status is {voided_order.get('status')!r}",
        )

        try:
            client.capture_authorization(
                void_auth_id, idempotency_key="mock-a-capture-after-void"
            )
        except PayPalError as exc:
            reporter.expect(
                exc.status_code == 422 and "AUTHORIZATION_VOIDED" in exc.issues,
                "A",
                "Capture after void is rejected",
                "HTTP 422 AUTHORIZATION_VOIDED — fail-closed",
                one_line_error(exc),
            )
        else:
            reporter.record(
                "A",
                "Capture after void is rejected",
                "FAIL",
                "capture succeeded after a void",
            )
            raise CheckFailure("capture succeeded after a void")

        # Deliberately confirm the orders-capture endpoint is the wrong tool
        # after authorize.  This is a common integration mistake.
        try:
            client.capture_order(
                order_id, idempotency_key="mock-a-orders-capture-wrong-endpoint"
            )
        except PayPalError as exc:
            reporter.expect(
                exc.status_code == 422
                and "ACTION_DOES_NOT_MATCH_INTENT" in exc.issues,
                "A",
                "Orders capture endpoint rejects AUTHORIZE intent",
                "HTTP 422 ACTION_DOES_NOT_MATCH_INTENT; use Payments authorization capture",
                one_line_error(exc),
            )
        else:
            reporter.record(
                "A",
                "Orders capture endpoint rejects AUTHORIZE intent",
                "FAIL",
                "orders capture unexpectedly accepted an AUTHORIZE-intent order",
            )

        reporter.record(
            "A",
            "Overall",
            "PASS",
            "vault -> order(vault_id) -> authorize -> capture -> COMPLETED, no redirect",
        )
        return True

    except CheckFailure as exc:
        reporter.record("A", "Overall", "FAIL", str(exc))
        return False
    except PayPalError as exc:
        reporter.print_exception(exc)
        reporter.record("A", "Overall", "FAIL", one_line_error(exc))
        return False
    except Exception as exc:  # defensive: the gate itself should not traceback
        reporter.print_exception(exc)
        reporter.record("A", "Overall", "FAIL", one_line_error(exc))
        return False


def mock_path_b(client: PayPalClient, mock: MockPayPal, reporter: Reporter) -> bool:
    try:
        reporter.step("Path B — vault a sandbox test card, then spend server-side")
        card_source = card_vault_source(
            "4111111111111111",
            "2030-12",
            "Veto Sandbox Buyer",
            country="US",
            postal_code="95131",
        )
        payment_token = client.create_payment_token(
            payment_source=card_source,
            idempotency_key="mock-b-payment-token",
        )
        vault_id = str(payment_token.get("id") or "")
        customer_id = str((payment_token.get("customer") or {}).get("id") or "")
        card_info = (payment_token.get("payment_source") or {}).get("card") or {}
        reporter.expect(
            bool(vault_id) and card_info.get("last_digits") == "1111",
            "B",
            "Create card payment token",
            f"vault_id={vault_id} customer_id={customer_id} "
            f"card={mask_card(card_info.get('last_digits', ''))}",
            f"unexpected card payment token: {payment_token}",
        )

        order = client.create_order(
            intent="AUTHORIZE",
            purchase_units=purchase_units(MOCK_AMOUNT, MOCK_CURRENCY),
            payment_source=card_order_payment_source(vault_id),
            idempotency_key="mock-b-order",
        )
        reporter.expect(
            not order_needs_payer_action(order),
            "B",
            "Create order with card vault_id",
            f"order={order.get('id')} status={order.get('status')} — no payer-action link",
            "card vault order requires a payer action",
        )
        auth_order = client.authorize_order(
            str(order.get("id") or ""), idempotency_key="mock-b-authorize"
        )
        auth = extract_authorization(auth_order) or {}
        auth_id = str(auth.get("id") or "")
        reporter.expect(
            bool(auth_id) and auth.get("status") == "CREATED",
            "B",
            "Authorize card vault order",
            f"authorization={auth_id} status=CREATED",
            f"unexpected authorization: {auth}",
        )
        capture = client.capture_authorization(
            auth_id, idempotency_key="mock-b-capture"
        )
        reporter.expect(
            capture.get("status") == "COMPLETED",
            "B",
            "Capture card vault order",
            f"capture={capture.get('id')} status=COMPLETED",
            f"unexpected capture: {capture}",
        )

        listed = client.list_payment_tokens(customer_id=customer_id)
        reporter.expect(
            vault_id
            in [item.get("id") for item in listed.get("payment_tokens") or []],
            "B",
            "List card payment tokens",
            "card vault token is listed",
            "card vault token missing from list",
        )
        client.delete_payment_token(
            vault_id, idempotency_key="mock-b-delete-token"
        )
        listed_after = client.list_payment_tokens(customer_id=customer_id)
        reporter.expect(
            vault_id
            not in [item.get("id") for item in listed_after.get("payment_tokens") or []],
            "B",
            "Delete payment token",
            "token removed from the customer vault",
            "token still present after delete",
        )

        try:
            client.create_order(
                intent="AUTHORIZE",
                purchase_units=purchase_units(MOCK_AMOUNT, MOCK_CURRENCY),
                payment_source=card_order_payment_source(vault_id),
                idempotency_key="mock-b-deleted-vault-order",
            )
        except PayPalError as exc:
            reporter.expect(
                exc.status_code == 422 and "VAULT_ID_NOT_FOUND" in exc.issues,
                "B",
                "Deleted vault cannot be spent",
                "HTTP 422 VAULT_ID_NOT_FOUND — deleted instrument is unusable",
                one_line_error(exc),
            )
        else:
            reporter.record(
                "B",
                "Deleted vault cannot be spent",
                "FAIL",
                "order creation accepted a deleted vault id",
            )
            raise CheckFailure("deleted vault id was accepted")

        reporter.record(
            "B",
            "Overall",
            "PASS",
            "card vault -> order(vault_id) -> authorize -> capture -> COMPLETED, no redirect",
        )
        return True

    except CheckFailure as exc:
        reporter.record("B", "Overall", "FAIL", str(exc))
        return False
    except PayPalError as exc:
        reporter.print_exception(exc)
        reporter.record("B", "Overall", "FAIL", one_line_error(exc))
        return False
    except Exception as exc:
        reporter.print_exception(exc)
        reporter.record("B", "Overall", "FAIL", one_line_error(exc))
        return False


def run_mock(reporter: Reporter) -> int:
    reporter.banner(
        "VETO PAYPAL CAPABILITY GATE — MOCK",
        "In-process mock_paypal. No credentials. No calls to PayPal.",
    )
    mock: Optional[MockPayPal] = None
    try:
        mock = MockPayPal().start()
        reporter.info(f"mock base URL : {mock.base_url}")
        reporter.info(f"test amount   : {MOCK_AMOUNT} {MOCK_CURRENCY} (fake money)")
        client = PayPalClient(
            MOCK_CLIENT_ID,
            MOCK_CLIENT_SECRET,
            mock.base_url,
            merchant_id=MOCK_MERCHANT_ID,
            timeout=5.0,
            use_proxy=False,
        )

        # -- Client-level checks ---------------------------------------
        reporter.section("Client plumbing")
        token1 = client.access_token()
        reporter.expect(
            bool(token1),
            "Client",
            "OAuth2 token fetch",
            "bearer token acquired from mock /v1/oauth2/token",
            "no access token",
        )
        token2 = client.access_token()
        cache = client.token_cache_info()
        reporter.expect(
            token1 == token2 and cache["fetches"] == 1,
            "Client",
            "OAuth2 token cache",
            f"2 calls -> {cache['fetches']} token fetch; expires_in={cache['expires_in']}s",
            f"cache did not hold: {cache}",
        )

        try:
            client.get_order("DOES-NOT-EXIST")
        except PayPalError as exc:
            reporter.expect(
                exc.status_code == 404
                and exc.name == "RESOURCE_NOT_FOUND"
                and "RESOURCE_NOT_FOUND" in exc.issues
                and bool(exc.debug_id),
                "Client",
                "Normalized error",
                f"HTTP 404 RESOURCE_NOT_FOUND issue={exc.issues[0]} "
                f"debug_id={exc.debug_id}",
                f"unexpected error shape: {one_line_error(exc)}",
            )
        else:
            reporter.record(
                "Client",
                "Normalized error",
                "FAIL",
                "404 request unexpectedly succeeded",
            )

        try:
            client.create_order(
                intent="AUTHORIZE",
                purchase_units=purchase_units(MOCK_AMOUNT, MOCK_CURRENCY),
                idempotency_key="",
            )
        except ValueError:
            reporter.record(
                "Client",
                "Request-Id mandatory on POST",
                "PASS",
                "empty idempotency key rejected before any HTTP call",
            )
        else:
            reporter.record(
                "Client",
                "Request-Id mandatory on POST",
                "FAIL",
                "empty idempotency key was accepted",
            )

        derived = derive_request_id("same-consent-nonce")
        reporter.expect(
            derived == derive_request_id("same-consent-nonce")
            and len(derived) <= 38
            and derived.startswith("veto-"),
            "Client",
            "Deterministic Request-Id derivation",
            f"{derived} (stable, 37 chars)",
            f"unexpected derivation: {derived}",
        )

        # -- Path A ----------------------------------------------------
        a_ok = mock_path_a(client, mock, reporter)

        # -- Path B ----------------------------------------------------
        b_ok = mock_path_b(client, mock, reporter)

        # -- Path C / cross-cutting -----------------------------------
        reporter.section("Path C and idempotency audit")
        reporter.record(
            "C",
            "Vault approval redirect (setup only)",
            "PASS",
            "approve URL was returned for the setup token and used once in mock; "
            "never used for an agent spend",
        )
        reporter.record(
            "C",
            "Agent spend without redirect",
            "PASS" if a_ok else "FAIL",
            "Path A spend order had no approve/payer-action link"
            if a_ok
            else "Path A did not prove a no-redirect spend",
        )
        post_logs = [
            entry
            for entry in mock.request_log
            if entry["method"] == "POST"
            and not entry["path"].startswith("/__mock/")
        ]
        missing_ids = [entry for entry in post_logs if not entry.get("request_id")]
        reporter.expect(
            not missing_ids,
            "Client",
            "Request-Id on every POST",
            f"{len(post_logs)} POSTs, all carried PayPal-Request-Id",
            f"{len(missing_ids)} POST(s) lacked a request id",
        )
        reporter.expect(
            mock.replay_count >= 2,
            "Client",
            "Idempotent replay observed",
            f"mock replayed {mock.replay_count} duplicate request(s)",
            f"expected at least 2 replays, saw {mock.replay_count}",
        )

        reporter.matrix()
        reporter.section("Mock summary")
        reporter.info(f"mock state: {json.dumps(mock.summary(), sort_keys=True)}")
        if mock.last_internal_error:
            reporter.warn("mock recorded an internal error; see mock.last_internal_error")
            print(mock.last_internal_error)

        all_ok = not reporter.failures and a_ok and b_ok
        rows = [
            "PATH A — VIABLE IN MOCK: vaulted PayPal wallet -> AUTHORIZE order with "
            "vault_id -> authorize -> capture COMPLETED, no redirect.",
            "PATH B — VIABLE IN MOCK: vaulted card -> AUTHORIZE order with card "
            "vault_id -> authorize -> capture COMPLETED, no redirect.",
            "PATH C — SETUP ONLY: the approval redirect established the vault; "
            "the spend order had no approve/payer-action link.",
            "",
            "This is a protocol and idempotency proof, NOT proof that the sandbox "
            "merchant has Vault or card capability enabled.",
        ]
        if all_ok:
            reporter.record(
                "ALL",
                "Mock gate",
                "PASS",
                "Path A and Path B flows completed against the mock",
            )
            reporter.verdict(
                rows,
                "Get a PayPal sandbox REST app and a sandbox buyer, export "
                "PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET as in README §2, then run "
                "`python3 spike/gate.py --live`.",
            )
            return 0
        reporter.record("ALL", "Mock gate", "FAIL", "one or more mock checks failed")
        reporter.verdict(
            rows,
            "Fix the first FAIL in the matrix above, then rerun "
            "`python3 spike/gate.py --mock`.",
        )
        return 1

    except Exception as exc:
        reporter.print_exception(exc)
        if mock is not None and mock.last_internal_error:
            print(mock.last_internal_error)
        reporter.record("ALL", "Mock gate", "FAIL", one_line_error(exc))
        reporter.matrix()
        reporter.verdict(
            ["The mock gate crashed before it could produce a verdict."],
            "Fix the crash above and rerun `python3 spike/gate.py --mock`.",
        )
        return 1
    finally:
        if mock is not None:
            mock.stop()


# ---------------------------------------------------------------------------
# Live mode
# ---------------------------------------------------------------------------
def load_state() -> Dict[str, Any]:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: Mapping[str, Any]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(STATE_PATH)
    except OSError:
        # State is a convenience, not a requirement.  Never fail a gate because
        # the filesystem is read-only.
        pass


def live_setup_source() -> Dict[str, Any]:
    return paypal_vault_setup_source(
        env("PAYPAL_RETURN_URL", DEFAULT_RETURN_URL) or DEFAULT_RETURN_URL,
        env("PAYPAL_CANCEL_URL", DEFAULT_CANCEL_URL) or DEFAULT_CANCEL_URL,
    )


def print_blocked(
    reporter: Reporter,
    title: str,
    reason: str,
    next_action: str,
    rows: Optional[Sequence[str]] = None,
) -> int:
    reporter.banner(title, "No capability claim is being made.")
    reporter.record("LIVE", "Preflight", "PENDING", reason)
    reporter.matrix()
    reporter.verdict(
        rows
        or [
            "PATH A — NOT TESTED",
            "PATH B — NOT TESTED",
            "PATH C — NOT TESTED",
        ],
        next_action,
    )
    return 2


def live_spend_flow(
    client: PayPalClient,
    reporter: Reporter,
    *,
    label: str,
    payment_source: Mapping[str, Any],
    amount: str,
    currency: str,
    run_key: str,
    expected_merchant_id: Optional[str],
) -> Dict[str, Any]:
    """Run create -> authorize -> capture and return evidence.

    The caller decides how to record the result.  This function intentionally
    stops after the first failure; a live gate should explain the first broken
    step, not cascade.
    """

    result: Dict[str, Any] = {
        "ok": False,
        "stage": "create order",
        "order": None,
        "order_id": None,
        "authorization_id": None,
        "capture_id": None,
        "duplicate_ok": False,
        "payer_action": False,
        "error": None,
        "needs_payer_action_link": "",
    }
    try:
        order = client.create_order(
            intent="AUTHORIZE",
            purchase_units=purchase_units(amount, currency),
            payment_source=payment_source,
            idempotency_key=f"{run_key}-{label}-create",
        )
        result["order"] = order
        order_id = str(order.get("id") or "")
        result["order_id"] = order_id
        if order_needs_payer_action(order):
            result["payer_action"] = True
            result["stage"] = "create order: PAYER_ACTION_REQUIRED"
            result["needs_payer_action_link"] = (
                PayPalClient.find_link(order, "payer-action")
                or PayPalClient.find_link(order, "approve")
                or ""
            )
            return result

        for unit in order.get("purchase_units") or []:
            payee = unit.get("payee") or {}
            payee_id = payee.get("merchant_id")
            if expected_merchant_id and payee_id and payee_id != expected_merchant_id:
                result["stage"] = (
                    f"create order: payee merchant_id={payee_id} does not match "
                    f"PAYPAL_MERCHANT_ID={expected_merchant_id}"
                )
                result["error"] = CheckFailure(result["stage"])
                return result

        result["stage"] = "authorize"
        auth_order = client.authorize_order(
            order_id, idempotency_key=f"{run_key}-{label}-authorize"
        )
        auth = extract_authorization(auth_order)
        if not auth:
            result["stage"] = "authorize: response had no authorization"
            result["order"] = auth_order
            result["error"] = CheckFailure(result["stage"])
            return result
        auth_id = str(auth.get("id") or "")
        result["authorization_id"] = auth_id
        result["auth_status"] = auth.get("status")
        if not auth_id:
            result["stage"] = "authorize: empty authorization id"
            result["error"] = CheckFailure(result["stage"])
            return result

        result["stage"] = "get authorization"
        fetched_auth = client.get_authorization(auth_id)
        result["auth_status"] = fetched_auth.get("status")

        result["stage"] = "capture authorization"
        capture = client.capture_authorization(
            auth_id, idempotency_key=f"{run_key}-{label}-capture"
        )
        capture_id = str(capture.get("id") or "")
        result["capture_id"] = capture_id
        result["capture"] = capture
        if not capture_id or capture.get("status") != "COMPLETED":
            result["stage"] = "capture authorization: capture not COMPLETED"
            result["error"] = CheckFailure(result["stage"])
            return result

        # The money has moved.  A failure in either verification below is a
        # safety/idempotency finding, not a reason to hide the successful spend.
        result["stage"] = "duplicate capture"
        try:
            duplicate = client.capture_authorization(
                auth_id, idempotency_key=f"{run_key}-{label}-capture"
            )
        except Exception as exc:  # keep the successful capture evidence
            result["duplicate_error"] = exc
            result["duplicate_ok"] = False
        else:
            result["duplicate_ok"] = (
                duplicate.get("id") == capture_id
                and duplicate.get("status") == "COMPLETED"
            )

        result["stage"] = "get order"
        try:
            final_order = client.get_order(order_id)
        except Exception as exc:  # capture is still proven
            result["final_order_error"] = exc
        else:
            result["final_order"] = final_order
            result["final_order_status"] = final_order.get("status")
            captured = PayPalClient.first_capture(final_order) or {}
            result["final_capture_id"] = captured.get("id")
            result["final_capture_status"] = captured.get("status")

        result["ok"] = True
        result["stage"] = "done"
        return result
    except PayPalError as exc:
        result["error"] = exc
        return result
    except Exception as exc:
        result["error"] = exc
        return result


def live_void_flow(
    client: PayPalClient,
    *,
    payment_source: Mapping[str, Any],
    amount: str,
    currency: str,
    run_key: str,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "ok": False,
        "stage": "create void-test order",
        "error": None,
    }
    try:
        order = client.create_order(
            intent="AUTHORIZE",
            purchase_units=purchase_units(amount, currency),
            payment_source=payment_source,
            idempotency_key=f"{run_key}-void-create",
        )
        result["order_id"] = order.get("id")
        if order_needs_payer_action(order):
            result["stage"] = "create void-test order: PAYER_ACTION_REQUIRED"
            return result
        auth_order = client.authorize_order(
            str(order.get("id") or ""), idempotency_key=f"{run_key}-void-authorize"
        )
        auth = extract_authorization(auth_order)
        if not auth:
            result["stage"] = "authorize void test: no authorization"
            return result
        auth_id = str(auth.get("id") or "")
        result["authorization_id"] = auth_id
        result["stage"] = "void authorization"
        client.void_authorization(auth_id, idempotency_key=f"{run_key}-void")
        result["stage"] = "get authorization after void"
        after = client.get_authorization(auth_id)
        result["status_after"] = after.get("status")
        result["ok"] = after.get("status") == "VOIDED"
        result["stage"] = "done" if result["ok"] else "authorization not VOIDED"
        return result
    except PayPalError as exc:
        result["error"] = exc
        return result
    except Exception as exc:
        result["error"] = exc
        return result


def run_live(args: argparse.Namespace, reporter: Reporter) -> int:
    reporter.banner(
        "VETO PAYPAL CAPABILITY GATE — LIVE SANDBOX",
        "Every API call below goes to the real endpoint in PAYPAL_BASE. "
        "No response is fabricated.",
    )

    client_id = env("PAYPAL_CLIENT_ID")
    client_secret = env("PAYPAL_CLIENT_SECRET")
    merchant_id = env("PAYPAL_MERCHANT_ID")
    base = (env("PAYPAL_BASE") or SANDBOX_BASE).rstrip("/")

    missing = []
    if not client_id:
        missing.append("PAYPAL_CLIENT_ID")
    if not client_secret:
        missing.append("PAYPAL_CLIENT_SECRET")
    if missing:
        reporter.info("Missing required environment variable(s): " + ", ".join(missing))
        print(
            "\n  Set them from the Developer Dashboard (Apps & Credentials -> "
            "Sandbox app):\n"
            "    export PAYPAL_CLIENT_ID='<sandbox client id>'\n"
            "    export PAYPAL_CLIENT_SECRET='<sandbox client secret>'\n"
            "    export PAYPAL_MERCHANT_ID='<sandbox business merchant id>'  # optional\n"
            "    export PAYPAL_BASE='https://api-m.sandbox.paypal.com'\n"
        )
        return print_blocked(
            reporter,
            "LIVE BLOCKED — MISSING CREDENTIALS",
            "PAYPAL_CLIENT_ID and/or PAYPAL_CLIENT_SECRET is not set.",
            "Create a sandbox REST app, copy its Client ID and Secret, export "
            "PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET, then rerun "
            "`python3 spike/gate.py --live`.",
            rows=[
                "PATH A — NOT TESTED (no credentials)",
                "PATH B — NOT TESTED (no credentials)",
                "PATH C — NOT TESTED (no credentials)",
            ],
        )

    if base == PRODUCTION_BASE:
        if not args.allow_production:
            return print_blocked(
                reporter,
                "LIVE BLOCKED — PRODUCTION ENDPOINT",
                "PAYPAL_BASE points at api-m.paypal.com. This spike is sandbox-only.",
                "Unset PAYPAL_BASE (or set it to https://api-m.sandbox.paypal.com), "
                "then rerun `python3 spike/gate.py --live`.",
                rows=[
                    "PATH A — NOT TESTED (production refused)",
                    "PATH B — NOT TESTED (production refused)",
                    "PATH C — NOT TESTED (production refused)",
                ],
            )
        if env("PAYPAL_PRODUCTION_CONFIRM") != "I_UNDERSTAND_REAL_MONEY":
            return print_blocked(
                reporter,
                "LIVE BLOCKED — PRODUCTION CONFIRMATION MISSING",
                "Production was requested with --allow-production but "
                "PAYPAL_PRODUCTION_CONFIRM is not set.",
                "Do not use production. Set PAYPAL_BASE to the sandbox endpoint and "
                "rerun `python3 spike/gate.py --live`.",
            )
        reporter.warn(
            "PRODUCTION ENDPOINT AND --allow-production: real money can move. "
            "This is not recommended for a hackathon spike."
        )
    elif not ("sandbox" in base or base.startswith("http://127.0.0.1") or base.startswith("http://localhost")):
        reporter.warn(
            f"PAYPAL_BASE={base!r} does not look like the PayPal sandbox. "
            "Proceeding because you set it explicitly."
        )

    amount = env("PAYPAL_TEST_AMOUNT", TEST_AMOUNT_DEFAULT) or TEST_AMOUNT_DEFAULT
    currency = (env("PAYPAL_TEST_CURRENCY", TEST_CURRENCY_DEFAULT) or TEST_CURRENCY_DEFAULT).upper()
    try:
        amount_decimal = Decimal(amount)
        if amount_decimal <= 0:
            raise ValueError("amount must be greater than zero")
        if amount_decimal > Decimal("10.00"):
            raise ValueError("amount is above the spike's $10.00 safety ceiling")
        amount = f"{amount_decimal:.2f}"
    except (InvalidOperation, ValueError) as exc:
        return print_blocked(
            reporter,
            "LIVE BLOCKED — INVALID TEST AMOUNT",
            f"PAYPAL_TEST_AMOUNT={amount!r}: {exc}",
            "Set PAYPAL_TEST_AMOUNT to a small sandbox amount such as 1.00, then "
            "rerun `python3 spike/gate.py --live`.",
        )
    if not re.fullmatch(r"[A-Z]{3}", currency):
        return print_blocked(
            reporter,
            "LIVE BLOCKED — INVALID CURRENCY",
            f"PAYPAL_TEST_CURRENCY={currency!r} is not a three-letter currency code.",
            "Set PAYPAL_TEST_CURRENCY=USD (or the sandbox seller's currency) and rerun.",
        )

    reporter.info(f"target base : {base}")
    reporter.info(f"merchant id : {merchant_id or '(not set; payee check disabled)'}")
    reporter.info(f"test amount : {amount} {currency} (sandbox money)")
    if not merchant_id:
        reporter.warn(
            "PAYPAL_MERCHANT_ID is not set. This spike does not need it for direct "
            "merchant calls; it enables the payee check. See README."
        )

    client = PayPalClient(
        client_id,
        client_secret,
        base,
        merchant_id=merchant_id,
        timeout=45.0,
        use_proxy=True,
    )

    # 1. Auth.
    try:
        client.access_token()
    except PayPalError as exc:
        reporter.print_exception(exc)
        reporter.record("Client", "OAuth2 token", "FAIL", one_line_error(exc))
        reporter.matrix()
        if exc.status_code is None:
            next_action = (
                "Check the network and PAYPAL_BASE (sandbox should be "
                "https://api-m.sandbox.paypal.com), then rerun "
                "`python3 spike/gate.py --live`."
            )
            reason = "token fetch failed before any capability test"
        elif exc.status_code == 401:
            next_action = (
                "Recopy the sandbox app's Client ID and Secret, check that both are "
                "from the Sandbox tab (not Live), export them, and rerun "
                "`python3 spike/gate.py --live`."
            )
            reason = "authentication failed"
        else:
            next_action = (
                "Fix the token-endpoint failure shown above, then rerun "
                "`python3 spike/gate.py --live`; quote PayPal's debug_id if present."
            )
            reason = "token endpoint returned an error"
        reporter.verdict(
            [
                f"PATH A — NOT TESTED ({reason})",
                f"PATH B — NOT TESTED ({reason})",
                f"PATH C — NOT TESTED ({reason})",
            ],
            next_action,
        )
        return 2
    reporter.record(
        "Client",
        "OAuth2 token",
        "PASS",
        f"bearer token cached; expires_in={client.token_cache_info()['expires_in']}s",
    )

    # 2. Resume state, scoped to this app.  Never mix vaults between apps.
    state = load_state()
    client_hash = fingerprint(client_id)
    if state and (
        state.get("client_id_hash") not in (None, client_hash)
        or (state.get("base_url") and state["base_url"] != base)
    ):
        reporter.warn(
            "Ignoring spike/.state/live-setup.json: it was created for a different "
            "app or PayPal base URL."
        )
        state = {}
    state.update(
        {
            "client_id_hash": client_hash,
            "base_url": base,
            "merchant_id": merchant_id,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )

    vault_id = env("PAYPAL_VAULT_ID")
    setup_token_id = env("PAYPAL_SETUP_TOKEN_ID")
    if not vault_id and not setup_token_id and state:
        vault_id = state.get("vault_id")
        setup_token_id = state.get("setup_token_id")
    if vault_id and setup_token_id:
        reporter.warn(
            "Both PAYPAL_VAULT_ID and a setup token are available; using the vault id "
            "and ignoring the setup token."
        )
        setup_token_id = None

    setup_approve_url = str(state.get("approve_url") or "")
    a_pending = False
    a_spend_ok = False
    a_void_ok = False
    a_duplicate_ok = False
    a_payer_action = False
    a_void_detail = ""
    run_key = f"live-{int(time.time())}-{os.getpid()}"
    spend_result: Dict[str, Any] = {}

    # 3. Establish or reuse the vault.
    reporter.section("Path A — vault readiness")
    if vault_id:
        try:
            token_info = client.get_payment_token(vault_id)
        except PayPalError as exc:
            reporter.print_exception(exc)
            reporter.record("A", "Vault id check", "FAIL", one_line_error(exc))
            vault_id = None
            state.pop("vault_id", None)
            save_state(state)
        else:
            source_keys = list((token_info.get("payment_source") or {}).keys())
            if "paypal" not in source_keys:
                reporter.record(
                    "A",
                    "Vault id check",
                    "FAIL",
                    f"PAYPAL_VAULT_ID={vault_id} is not a PayPal wallet token "
                    f"(sources={source_keys})",
                )
                vault_id = None
                state.pop("vault_id", None)
                save_state(state)
            else:
                reporter.record(
                    "A",
                    "Vault id check",
                    "PASS",
                    f"PAYPAL_VAULT_ID={vault_id} is a saved PayPal wallet token",
                )
                state.update(
                    {
                        "vault_id": vault_id,
                        "customer_id": (token_info.get("customer") or {}).get("id"),
                        "status": "vault-ready",
                    }
                )
                save_state(state)
    elif setup_token_id:
        try:
            setup = client.get_setup_token(setup_token_id)
        except PayPalError as exc:
            reporter.print_exception(exc)
            reporter.record("A", "Resume setup token", "FAIL", one_line_error(exc))
        else:
            status = str(setup.get("status") or "")
            setup_approve_url = (
                PayPalClient.find_link(setup, "approve")
                or setup_approve_url
                or ""
            )
            state.update(
                {
                    "setup_token_id": setup_token_id,
                    "approve_url": setup_approve_url,
                    "status": status,
                }
            )
            if status == "PAYER_ACTION_REQUIRED":
                a_pending = True
                reporter.record(
                    "A",
                    "Vault setup",
                    "PENDING",
                    f"setup token {setup_token_id} still needs payer approval",
                )
                print("\n   OPEN THIS URL WITH THE SANDBOX BUYER:")
                print(f"   {setup_approve_url or '(no approve link returned)'}")
                print(
                    "   Approve the PayPal wallet, then rerun "
                    "`python3 spike/gate.py --live`.\n"
                )
                save_state(state)
            elif status == "APPROVED":
                try:
                    payment_token = client.create_payment_token(
                        payment_source={
                            "token": {"id": setup_token_id, "type": "SETUP_TOKEN"}
                        },
                        idempotency_key=f"vault:{setup_token_id}",
                    )
                except PayPalError as exc:
                    reporter.print_exception(exc)
                    reporter.record(
                        "A", "Create payment token", "FAIL", one_line_error(exc)
                    )
                else:
                    vault_id = str(payment_token.get("id") or "")
                    customer_id = str(
                        (payment_token.get("customer") or {}).get("id") or ""
                    )
                    if not vault_id:
                        reporter.record(
                            "A",
                            "Create payment token",
                            "FAIL",
                            "payment token response had no id",
                        )
                        vault_id = ""
                    else:
                        state.update(
                            {
                                "vault_id": vault_id,
                                "customer_id": customer_id,
                                "status": "vault-ready",
                            }
                        )
                        save_state(state)
                        reporter.record(
                            "A",
                            "Create payment token",
                            "PASS",
                            f"vault_id={vault_id} customer_id={customer_id}",
                        )
            elif status == "VAULTED":
                reporter.record(
                    "A",
                    "Resume setup token",
                    "FAIL",
                    "setup token is already VAULTED; its payment token id is not in "
                    "this process. Set PAYPAL_VAULT_ID to the saved vault id.",
                )
            else:
                reporter.record(
                    "A",
                    "Resume setup token",
                    "FAIL",
                    f"setup token status is {status!r}; expected APPROVED or "
                    "PAYER_ACTION_REQUIRED.",
                )
    else:
        try:
            setup = client.create_setup_token(
                payment_source=live_setup_source(),
                idempotency_key=f"setup:{client_hash}:{int(time.time())}",
            )
        except PayPalError as exc:
            reporter.print_exception(exc)
            reporter.record("A", "Create setup token", "FAIL", one_line_error(exc))
        else:
            setup_token_id = str(setup.get("id") or "")
            setup_approve_url = PayPalClient.find_link(setup, "approve") or ""
            if not setup_token_id or not setup_approve_url:
                reporter.record(
                    "A",
                    "Create setup token",
                    "FAIL",
                    "setup token response missing id or approve link",
                )
            else:
                state.update(
                    {
                        "setup_token_id": setup_token_id,
                        "approve_url": setup_approve_url,
                        "status": setup.get("status"),
                    }
                )
                save_state(state)
                reporter.record(
                    "A",
                    "Create setup token",
                    "PASS",
                    f"setup_token_id={setup_token_id}; approve URL returned",
                )
                a_pending = True
                reporter.record(
                    "A",
                    "Vault setup",
                    "PENDING",
                    "payer approval is required once to establish the vault",
                )
                print("\n   OPEN THIS URL WITH THE SANDBOX BUYER:")
                print(f"   {setup_approve_url}")
                print(
                    "   Log in with the *personal* sandbox account, approve, then rerun "
                    "`python3 spike/gate.py --live`.\n"
                )

    # 4. Spend from the vault.  This is the whole point of the gate.
    if vault_id:
        reporter.section("Path A — server-side spend (the actual gate)")
        spend_result = live_spend_flow(
            client,
            reporter,
            label="a",
            payment_source=paypal_order_payment_source(vault_id),
            amount=amount,
            currency=currency,
            run_key=run_key,
            expected_merchant_id=merchant_id,
        )
        if spend_result.get("payer_action"):
            a_payer_action = True
            reporter.record(
                "A",
                "Create order with vault_id",
                "FAIL",
                "order returned PAYER_ACTION_REQUIRED; server-side spend needs a "
                "payer redirect (Path C).",
            )
            link = spend_result.get("needs_payer_action_link")
            if link:
                print(f"   payer-action URL: {link}")
        elif not spend_result.get("ok"):
            exc = spend_result.get("error")
            if exc is not None:
                reporter.print_exception(exc)
                detail = one_line_error(exc)
            else:
                detail = str(spend_result.get("stage") or "unknown stage")
            reporter.record(
                "A",
                f"Spend step: {spend_result.get('stage')}",
                "FAIL",
                detail,
            )
        else:
            reporter.record(
                "A",
                "Create order with vault_id",
                "PASS",
                f"order={spend_result.get('order_id')} "
                f"status={(spend_result.get('order') or {}).get('status')} "
                f"payee check={'on' if merchant_id else 'off'}",
            )
            reporter.record(
                "A",
                "Authorize",
                "PASS",
                f"authorization={spend_result.get('authorization_id')} "
                f"status={spend_result.get('auth_status')}",
            )
            reporter.record(
                "A",
                "Capture authorization",
                "PASS",
                f"capture={spend_result.get('capture_id')} status=COMPLETED",
            )
            a_duplicate_ok = bool(spend_result.get("duplicate_ok"))
            if a_duplicate_ok:
                reporter.record(
                    "A",
                    "Duplicate capture Request-Id",
                    "PASS",
                    "same capture id returned; no double charge",
                )
            else:
                reporter.record(
                    "A",
                    "Duplicate capture Request-Id",
                    "FAIL",
                    "duplicate capture did not replay the same capture"
                    + (
                        "; " + one_line_error(spend_result["duplicate_error"])
                        if spend_result.get("duplicate_error")
                        else ""
                    ),
                )
            final_capture_id = spend_result.get("final_capture_id")
            final_capture_status = spend_result.get("final_capture_status")
            reporter.record(
                "A",
                "Order after capture",
                "PASS"
                if spend_result.get("final_order_status") == "COMPLETED"
                and final_capture_id == spend_result.get("capture_id")
                and final_capture_status == "COMPLETED"
                else "WARN",
                f"order status={spend_result.get('final_order_status')} "
                f"capture={final_capture_id} status={final_capture_status}",
            )
            a_spend_ok = True

            # 5. Void a separate authorization, so the veto path is real too.
            reporter.section("Path A — void path")
            void_result = live_void_flow(
                client,
                payment_source=paypal_order_payment_source(vault_id),
                amount=amount,
                currency=currency,
                run_key=run_key,
            )
            if void_result.get("error"):
                reporter.print_exception(void_result["error"])
                a_void_detail = one_line_error(void_result["error"])
                reporter.record(
                    "A",
                    f"Void step: {void_result.get('stage')}",
                    "FAIL",
                    a_void_detail,
                )
            elif not void_result.get("ok"):
                a_void_detail = (
                    f"void-test stage={void_result.get('stage')} "
                    f"status_after={void_result.get('status_after')}"
                )
                reporter.record("A", "Void authorization", "FAIL", a_void_detail)
            else:
                a_void_ok = True
                reporter.record(
                    "A",
                    "Void authorization",
                    "PASS",
                    f"authorization={void_result.get('authorization_id')} "
                    f"status={void_result.get('status_after')}",
                )
                reporter.record(
                    "A",
                    "Order after void",
                    "PASS",
                    f"order={void_result.get('order_id')} left without a capture",
                )

    # 6. Path B — only if A did not fully pass.  A is the priority path.
    reporter.section("Path B — card vault")
    b_spend_ok = False
    b_duplicate_ok = False
    b_attempted = False
    b_pending = False
    b_detail = ""
    if a_spend_ok and a_void_ok:
        reporter.record(
            "B",
            "Overall",
            "SKIP",
            "Path A passed; card vaulting is lower priority and was not tested "
            "to avoid extra sandbox charges.",
        )
    else:
        card_number = env("PAYPAL_CARD_NUMBER") or ""
        card_expiry_raw = env("PAYPAL_CARD_EXPIRY") or ""
        card_name = env("PAYPAL_CARD_NAME", "Veto Sandbox Buyer") or "Veto Sandbox Buyer"
        card_cvv = env("PAYPAL_CARD_CVV") or ""
        card_country = (env("PAYPAL_CARD_COUNTRY", "US") or "US").upper()
        card_postal = env("PAYPAL_CARD_POSTAL_CODE")
        if not card_number or not card_expiry_raw:
            reporter.record(
                "B",
                "Overall",
                "SKIP",
                "no card supplied; set PAYPAL_CARD_NUMBER and PAYPAL_CARD_EXPIRY "
                "(and optionally PAYPAL_CARD_NAME, PAYPAL_CARD_CVV, "
                "PAYPAL_CARD_COUNTRY, PAYPAL_CARD_POSTAL_CODE) to test Path B.",
            )
        else:
            b_attempted = True
            try:
                card_expiry = normalize_expiry(card_expiry_raw)
            except ValueError as exc:
                reporter.record(
                    "B", "Card input", "FAIL", f"{exc} (PAYPAL_CARD_EXPIRY)"
                )
                card_expiry = ""
            if card_expiry:
                card_vault = card_vault_source(
                    card_number,
                    card_expiry,
                    card_name,
                    country=card_country,
                    postal_code=card_postal,
                    cvv=card_cvv or None,
                )
                vault_id_b = ""
                token_error: Optional[PayPalError] = None
                try:
                    token_b = client.create_payment_token(
                        payment_source=card_vault,
                        idempotency_key=f"{run_key}-b-vault-direct",
                    )
                except PayPalError as exc:
                    token_error = exc
                    reporter.print_exception(exc)
                    reporter.record(
                        "B",
                        "Direct card payment token",
                        "FAIL",
                        one_line_error(exc),
                    )
                else:
                    vault_id_b = str(token_b.get("id") or "")
                    reporter.record(
                        "B",
                        "Direct card payment token",
                        "PASS",
                        f"vault_id={vault_id_b} card={mask_card(card_number)}",
                    )

                if not vault_id_b:
                    # The v3 schema accepts a card source directly, but PayPal's
                    # guide uses a setup token for cards.  Try the documented
                    # setup-token route as a fallback and report which one worked.
                    reporter.info(
                        "Direct card vault failed; trying the documented card "
                        "setup-token route."
                    )
                    try:
                        card_setup = client.create_setup_token(
                            payment_source=card_vault_source(
                                card_number,
                                card_expiry,
                                card_name,
                                country=card_country,
                                postal_code=card_postal,
                                cvv=card_cvv or None,
                                with_experience_context=True,
                            ),
                            idempotency_key=f"{run_key}-b-card-setup",
                        )
                    except PayPalError as exc:
                        reporter.print_exception(exc)
                        reporter.record(
                            "B",
                            "Card setup-token fallback",
                            "FAIL",
                            one_line_error(exc),
                        )
                    else:
                        card_setup_id = str(card_setup.get("id") or "")
                        card_status = str(card_setup.get("status") or "")
                        if card_status == "APPROVED":
                            try:
                                token_b = client.create_payment_token(
                                    payment_source={
                                        "token": {
                                            "id": card_setup_id,
                                            "type": "SETUP_TOKEN",
                                        }
                                    },
                                    idempotency_key=f"{run_key}-b-vault-fallback",
                                )
                            except PayPalError as exc:
                                reporter.print_exception(exc)
                                reporter.record(
                                    "B",
                                    "Card setup-token fallback",
                                    "FAIL",
                                    one_line_error(exc),
                                )
                            else:
                                vault_id_b = str(token_b.get("id") or "")
                                reporter.record(
                                    "B",
                                    "Card setup-token fallback",
                                    "PASS",
                                    f"vault_id={vault_id_b} via setup token {card_setup_id}",
                                )
                        elif card_status == "PAYER_ACTION_REQUIRED":
                            approve_url = (
                                PayPalClient.find_link(card_setup, "approve") or ""
                            )
                            b_pending = True
                            reporter.record(
                                "B",
                                "Card setup-token fallback",
                                "PENDING",
                                "card requires 3DS/payer approval; open "
                                f"{approve_url or '(approve link missing)'} then rerun",
                            )
                        else:
                            reporter.record(
                                "B",
                                "Card setup-token fallback",
                                "FAIL",
                                f"unexpected setup token status {card_status!r}",
                            )

                if vault_id_b:
                    b_result = live_spend_flow(
                        client,
                        reporter,
                        label="b",
                        payment_source=card_order_payment_source(vault_id_b),
                        amount=amount,
                        currency=currency,
                        run_key=run_key,
                        expected_merchant_id=merchant_id,
                    )
                    if b_result.get("payer_action"):
                        reporter.record(
                            "B",
                            "Create order with card vault_id",
                            "FAIL",
                            "order returned PAYER_ACTION_REQUIRED; card spend would "
                            "need a payer redirect.",
                        )
                        b_detail = "card spend required a payer redirect"
                    elif not b_result.get("ok"):
                        exc = b_result.get("error")
                        if exc is not None:
                            reporter.print_exception(exc)
                            b_detail = one_line_error(exc)
                        else:
                            b_detail = str(b_result.get("stage") or "unknown stage")
                        reporter.record(
                            "B",
                            f"Spend step: {b_result.get('stage')}",
                            "FAIL",
                            b_detail,
                        )
                    else:
                        reporter.record(
                            "B",
                            "Create order with card vault_id",
                            "PASS",
                            f"order={b_result.get('order_id')} "
                            f"status={(b_result.get('order') or {}).get('status')}",
                        )
                        reporter.record(
                            "B",
                            "Authorize card vault order",
                            "PASS",
                            f"authorization={b_result.get('authorization_id')}",
                        )
                        reporter.record(
                            "B",
                            "Capture card vault order",
                            "PASS",
                            f"capture={b_result.get('capture_id')} status=COMPLETED",
                        )
                        b_duplicate_ok = bool(b_result.get("duplicate_ok"))
                        reporter.record(
                            "B",
                            "Duplicate capture Request-Id",
                            "PASS" if b_duplicate_ok else "WARN",
                            "same capture id returned; no double charge"
                            if b_duplicate_ok
                            else (
                                "duplicate capture did not replay the same capture"
                                + (
                                    "; " + one_line_error(b_result["duplicate_error"])
                                    if b_result.get("duplicate_error")
                                    else ""
                                )
                            ),
                        )
                        b_spend_ok = True
                        b_detail = "card vault -> authorize -> capture COMPLETED"
                        # Clean up the card token; it is not needed after proof.
                        try:
                            client.delete_payment_token(
                                vault_id_b,
                                idempotency_key=f"{run_key}-b-delete",
                            )
                        except PayPalError as exc:
                            reporter.record(
                                "B",
                                "Delete payment token",
                                "WARN",
                                one_line_error(exc),
                            )
                        else:
                            reporter.record(
                                "B",
                                "Delete payment token",
                                "PASS",
                                "card vault token deleted",
                            )
            if not b_spend_ok and not b_pending and not b_detail:
                b_detail = "card path did not complete"

    # 7. Path C framing.
    reporter.section("Path C — redirect framing")
    if a_spend_ok or b_spend_ok:
        reporter.record(
            "C",
            "Agent spend without redirect",
            "PASS",
            "authorize/capture completed with no payer redirect",
        )
    elif a_payer_action:
        reporter.record(
            "C",
            "Agent spend without redirect",
            "FAIL",
            "PayPal required a payer action; this is checkout, not an agent spend",
        )
    else:
        reporter.record(
            "C",
            "Agent spend without redirect",
            "SKIP",
            "no successful no-redirect spend to compare",
        )
    if setup_approve_url or setup_token_id:
        reporter.record(
            "C",
            "Vault approval redirect (setup only)",
            "PASS" if not a_pending else "PENDING",
            "approval redirect is available once to establish the vault; it must "
            "never be used for the agent's spend",
        )
    else:
        reporter.record(
            "C",
            "Vault approval redirect (setup only)",
            "SKIP",
            "no setup token was created this run (pre-existing vault id)",
        )

    # 8. Verdict.
    reporter.matrix()
    counts = reporter.counts()
    if a_spend_ok and not a_duplicate_ok:
        rows = [
            "PATH A — SPEND VIABLE, IDEMPOTENCY NOT PROVEN: capture COMPLETED "
            "without a redirect, but a second capture with the same "
            "PayPal-Request-Id did not replay the same capture.",
            f"PATH A void — {'PASS' if a_void_ok else 'FAIL / ' + (a_void_detail or 'not proven')}.",
            f"PATH B — {'PASS' if b_spend_ok else 'NOT TESTED / FAILED'}.",
            "PATH C — not acceptable for agent spend.",
            "",
            "A retried capture must not be able to double-charge. Treat this as a "
            "blocker even though money moved successfully once.",
        ]
        reporter.record(
            "LIVE", "Verdict", "FAIL", "spend worked but idempotent replay did not"
        )
        reporter.verdict(
            rows,
            "Retry the duplicate capture with the exact same PayPal-Request-Id and "
            "quote the debug_id from the matrix to PayPal if it still fails; do not "
            "build UI until the replay is proven.",
        )
        return 1

    if a_spend_ok and a_void_ok:
        a_line = (
            "PATH A — VIABLE: vaulted PayPal wallet, server-side AUTHORIZE -> "
            "capture COMPLETED, and a separate void reached VOIDED."
        )
        next_action = (
            "Record the sandbox vault_id and capture id, then wire paypal_client "
            "into Veto's order step; the agent can pay with nobody at a checkout."
        )
        rows = [
            a_line,
            f"PATH B — NOT TESTED ({'A passed' if b_attempted is False else b_detail or 'lower priority'}).",
            "PATH C — NOT NEEDED FOR SPEND: the approval redirect only established "
            "the vault. It is available for onboarding, never for an agent spend.",
            "",
            f"Evidence: order={spend_result.get('order_id')} "
            f"authorization={spend_result.get('authorization_id')} "
            f"capture={spend_result.get('capture_id')} "
            f"amount={amount} {currency} on {base}. Debug ids are in the matrix.",
        ]
        reporter.record("LIVE", "Verdict", "PASS", "Path A viable on sandbox")
        reporter.verdict(rows, next_action)
        return 0

    if a_spend_ok and not a_void_ok:
        rows = [
            "PATH A — SPEND VIABLE, VOID FAILED: capture worked without a redirect, "
            "but the veto/safety path did not reach VOIDED.",
            f"PATH B — {'PASS' if b_spend_ok else 'NOT TESTED / FAILED'}.",
            "PATH C — not acceptable for agent spend.",
            "",
            f"Void failure: {a_void_detail or 'see matrix'}",
        ]
        reporter.record("LIVE", "Verdict", "FAIL", "spend worked but void failed")
        reporter.verdict(
            rows,
            "Fix the void failure before building UI: re-run the void request with "
            "the same request id, then quote the debug_id from the matrix to PayPal "
            "if it persists.",
        )
        return 1

    if b_spend_ok and a_pending:
        rows = [
            "PATH A — PENDING HUMAN APPROVAL: the wallet setup token still needs the "
            "sandbox buyer to approve it.",
            "PATH B — VIABLE: a vaulted card completed AUTHORIZE -> capture with no "
            "payer redirect.",
            "PATH C — only for establishing the vault; not acceptable for the agent's "
            "spend.",
            "",
            "Path B is a working fallback. A is the priority path and is not disproven "
            "yet, only unapproved.",
            "Path B idempotency — "
            + ("PASS." if b_duplicate_ok else "WARN: replay was not proven."),
        ]
        reporter.record(
            "LIVE", "Verdict", "PASS", "Path B viable; Path A pending approval"
        )
        reporter.verdict(
            rows,
            f"Open {setup_approve_url or 'the approve URL printed above'} with the "
            "sandbox buyer, approve it, then rerun `python3 spike/gate.py --live` "
            "to finish the A test.",
        )
        return 0

    if b_spend_ok:
        rows = [
            "PATH A — NOT VIABLE on this run: the PayPal wallet vault/spend path "
            "did not complete.",
            "PATH B — VIABLE: a vaulted card completed AUTHORIZE -> capture with no "
            "payer redirect.",
            "PATH C — only for establishing the vault; not acceptable for the agent's "
            "spend.",
            "",
            f"Path B evidence: {b_detail}. A is the priority path; B is a fallback.",
            "Path B idempotency — "
            + ("PASS." if b_duplicate_ok else "WARN: replay was not proven; fix before trusting retries."),
        ]
        reporter.record("LIVE", "Verdict", "PASS", "Path B viable; Path A failed")
        reporter.verdict(
            rows,
            "Capture the first Path A failure's debug_id from the matrix, enable "
            "Vault + PayPal Wallet in the sandbox app, and rerun `python3 "
            "spike/gate.py --live`; meanwhile decide whether card-only B is shippable.",
        )
        return 0

    if a_pending or b_pending:
        rows = [
            "PATH A — PENDING HUMAN APPROVAL: a setup token exists but the sandbox "
            "buyer has not approved it yet.",
            f"PATH B — {'PASS' if b_spend_ok else ('PENDING' if b_pending else ('FAILED' if b_attempted else 'SKIPPED (no card configured)'))}.",
            "PATH C — approval redirects are for vault establishment only.",
            "",
            "No server-side spend has been proven yet.",
        ]
        reporter.record("LIVE", "Verdict", "PENDING", "human approval required")
        next_action = (
            f"Open {setup_approve_url or 'the approve URL printed above'} with the "
            "sandbox buyer, approve it, then rerun `python3 spike/gate.py --live`."
            if a_pending
            else "Complete the card approval/3DS step printed above, then rerun "
            "`python3 spike/gate.py --live`."
        )
        reporter.verdict(rows, next_action)
        return 2

    # Distinguish "PayPal told us no" from "we could not test".
    tested_a = bool(vault_id) or not a_pending
    if tested_a or b_attempted:
        rows = [
            "PATH A — FAILED / NOT PROVEN: see the first Path A FAIL in the matrix.",
            f"PATH B — {'FAILED' if b_attempted else 'SKIPPED (no card configured)'}.",
            "PATH C — not acceptable for agent spend. A redirect-only result means "
            "the pitch must change.",
            "",
            "Do not build product UI on this result.",
        ]
        reporter.record("LIVE", "Verdict", "FAIL", "no path proven")
        next_action = (
            "Quote the first Path A failure's debug_id and issue list from the matrix "
            "when checking the app's Vault/card feature options with PayPal support, "
            "then rerun `python3 spike/gate.py --live`."
        )
        reporter.verdict(rows, next_action)
        return 1

    rows = [
        "PATH A — INCONCLUSIVE: vault was not established and no spend was attempted.",
        "PATH B — SKIPPED / INCONCLUSIVE.",
        "PATH C — not acceptable for agent spend.",
    ]
    reporter.record("LIVE", "Verdict", "PENDING", "inconclusive")
    reporter.verdict(
        rows,
        "Fix the first FAIL in the matrix, then rerun "
        "`python3 spike/gate.py --live`; if you have an approved vault, export "
        "PAYPAL_VAULT_ID and rerun.",
    )
    return 2


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Veto PayPal capability gate (mock or live sandbox).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 spike/gate.py --mock\n"
            "  PAYPAL_CLIENT_ID=... PAYPAL_CLIENT_SECRET=... "
            "python3 spike/gate.py --live\n"
            "\n"
            "Exit codes: 0 = a path was proven (or mock passed), "
            "1 = tested and no path proven, 2 = blocked / needs human approval."
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--mock",
        action="store_true",
        help="run the complete flow against the in-process mock_paypal",
    )
    mode.add_argument(
        "--live",
        action="store_true",
        help="run against PAYPAL_BASE (default PayPal sandbox) using env credentials",
    )
    parser.add_argument(
        "--allow-production",
        action="store_true",
        help=(
            "danger: allow PAYPAL_BASE=https://api-m.paypal.com; also requires "
            "PAYPAL_PRODUCTION_CONFIRM=I_UNDERSTAND_REAL_MONEY"
        ),
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    reporter = Reporter()
    if args.mock:
        return run_mock(reporter)
    try:
        return run_live(args, reporter)
    except Exception as exc:  # never dump a traceback at a hackathon operator
        reporter.print_exception(exc)
        reporter.record("LIVE", "Gate crashed", "FAIL", one_line_error(exc))
        reporter.matrix()
        reporter.verdict(
            [
                "PATH A — INCONCLUSIVE (the gate crashed)",
                "PATH B — INCONCLUSIVE (the gate crashed)",
                "PATH C — INCONCLUSIVE (the gate crashed)",
            ],
            "Fix the crash above, then rerun `python3 spike/gate.py --live`; "
            "include the traceback in a bug report.",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
