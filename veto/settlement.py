"""PayPal settlement: one vaulted wallet, one order, one capture.

This is the only module that moves money. The rules and the consent are decided
before it is called; a blocked or vetoed request never reaches it, so a refusal
can never leave a PayPal object behind.

``PAYPAL_LIVE=0`` (the default) runs against the in-process PayPal simulator, so
the whole loop works with no keys. In live mode the approved payment token can be
supplied as ``PAYPAL_VAULT_ID``; without it the first capture asks for one payer
approval and holds the request until it is given.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veto.paypal_client import PayPalClient, PayPalError  # noqa: E402

CURRENCY = "USD"
MOCK_CLIENT_ID = "mock-client-id"
MOCK_CLIENT_SECRET = "mock-client-secret"


class VaultRequired(Exception):
    """Live mode needs one payer approval before any money can move."""

    def __init__(self, approve_url: str = "", setup_id: str = "") -> None:
        super().__init__("the company wallet needs one payer approval")
        self.approve_url = approve_url
        self.setup_id = setup_id


class SettlementError(Exception):
    """PayPal refused or failed a step; the request keeps its objects for the record."""

    def __init__(self, message: str, *, steps: list[dict] | None = None,
                 partial: dict | None = None) -> None:
        super().__init__(message)
        self.steps = list(steps or [])
        self.partial = dict(partial or {})


class Settlement:
    """The money side of the product, kept out of the rules and the request board."""

    def __init__(self, live: bool | None = None) -> None:
        env_live = os.environ.get("PAYPAL_LIVE", "0") == "1"
        self.live = env_live if live is None else live
        self.mock = None
        self.pp = self._build_paypal()

    # -- client --------------------------------------------------------
    def _build_paypal(self) -> PayPalClient:
        if self.live:
            return PayPalClient.from_env()
        from veto.mock_paypal import MockPayPal

        self.mock = MockPayPal().start()
        return PayPalClient(
            MOCK_CLIENT_ID,
            MOCK_CLIENT_SECRET,
            self.mock.base_url,
            merchant_id="MOCK-MERCHANT",
            timeout=5.0,
            use_proxy=False,
        )

    @property
    def mode(self) -> str:
        return "sandbox" if self.live else "mock"

    def close(self) -> None:
        if self.mock is not None:
            self.mock.stop()
            self.mock = None

    # -- wallet --------------------------------------------------------
    @staticmethod
    def _vault_source() -> dict:
        return {
            "paypal": {
                "description": "Veto company wallet",
                "usage_pattern": "IMMEDIATE",
                "usage_type": "MERCHANT",
                "customer_type": "CONSUMER",
                "permit_multiple_payment_tokens": False,
                "experience_context": {
                    "brand_name": "Veto",
                    "locale": "en-US",
                    "return_url": "https://example.com/veto/return",
                    "cancel_url": "https://example.com/veto/cancel",
                },
            }
        }

    def prepare(self, store) -> None:
        """Reconcile stored wallets with this process before it serves a request.

        Mock vaults die with the simulator process that minted them; a stored one
        can never be replayed against a new process, so the wallet starts clean.
        Live mode reflects the approved token from the environment instead.
        """
        if not self.live:
            store.execute(
                "UPDATE wallets SET status = 'disconnected', vault_id = NULL "
                "WHERE mode = 'mock'"
            )
            return
        if self.sync_env_wallet(store):
            return
        # The v1 console approved this sandbox wallet already; one workspace, one
        # wallet, so M3 can keep spending from the same payer approval.
        legacy = self._legacy_vault()
        if not legacy:
            return
        for workspace in store.list_workspaces():
            wallet = store.wallet(workspace["id"]) or {}
            if wallet.get("status") == "connected" and wallet.get("vault_id"):
                continue
            store.set_wallet(
                workspace["id"],
                vault_id=legacy,
                mode="sandbox",
                status="connected",
                connected_at=_now_iso(),
            )

    @staticmethod
    def _legacy_vault() -> str:
        path = ROOT / ".state" / "vault.json"
        try:
            saved = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return ""
        if not isinstance(saved, dict) or saved.get("mode", "sandbox") != "sandbox":
            return ""
        return str(saved.get("vault_id") or "")

    def sync_env_wallet(self, store) -> list[str]:
        """A hosted deploy hands the approved token in; reflect it before any spend."""
        vault_id = (os.environ.get("PAYPAL_VAULT_ID") or "").strip()
        if not vault_id or not self.live:
            return []
        connected = []
        for workspace in store.list_workspaces():
            wallet = store.wallet(workspace["id"])
            if wallet and wallet.get("status") == "connected" and wallet.get("vault_id"):
                continue
            store.set_wallet(
                workspace["id"],
                vault_id=vault_id,
                mode="sandbox",
                status="connected",
                connected_at=_now_iso(),
            )
            connected.append(workspace["id"])
        return connected

    def ensure_wallet(self, store, workspace: dict) -> str:
        """Return a usable vault id, minting one when the mode allows it."""
        workspace_id = workspace["id"]
        wallet = store.wallet(workspace_id) or {}
        if wallet.get("status") == "connected" and wallet.get("vault_id"):
            return wallet["vault_id"]

        vault_id = (os.environ.get("PAYPAL_VAULT_ID") or "").strip()
        if vault_id and self.live:
            store.set_wallet(
                workspace_id,
                vault_id=vault_id,
                mode="sandbox",
                status="connected",
                connected_at=_now_iso(),
            )
            return vault_id

        if not self.live:
            return self._mint_mock_wallet(store, workspace)

        return self._request_payer_approval(store, workspace)

    def _mint_mock_wallet(self, store, workspace: dict) -> str:
        """The simulator approves its own setup token; one token per workspace."""
        setup = self.pp.create_setup_token(
            payment_source=self._vault_source(),
            idempotency_key=f"veto-vault-setup-{int(time.time()) // 60}",
        )
        setup_id = setup["id"]
        self.mock.approve_setup_token(setup_id)
        token = self.pp.create_payment_token(
            payment_source={"token": {"id": setup_id, "type": "SETUP_TOKEN"}},
            idempotency_key="veto-vault-token",
        )
        vault_id = token["id"]
        store.set_wallet(
            workspace["id"],
            vault_id=vault_id,
            mode="mock",
            status="connected",
            connected_at=_now_iso(),
        )
        return vault_id

    def _request_payer_approval(self, store, workspace: dict) -> str:
        """Live mode without a token: reuse a pending approval, or ask for one."""
        workspace_id = workspace["id"]
        setup_key = f"wallet_setup:{workspace_id}"
        saved_setup = store.get_meta(setup_key)
        if saved_setup:
            setup = self.pp.get_setup_token(saved_setup)
            if (setup.get("status") or "").upper() == "APPROVED":
                return self._finish_setup(store, workspace, saved_setup)
            link = (
                PayPalClient.find_link(setup, "approve")
                or PayPalClient.find_link(setup, "payer-action")
                or store.get_meta(f"wallet_approve:{workspace_id}")
                or ""
            )
            raise VaultRequired(link, saved_setup)

        setup = self.pp.create_setup_token(
            payment_source=self._vault_source(),
            idempotency_key=f"veto-vault-setup-{int(time.time()) // 60}",
        )
        setup_id = setup["id"]
        link = (
            PayPalClient.find_link(setup, "approve")
            or PayPalClient.find_link(setup, "payer-action")
            or ""
        )
        store.set_meta(setup_key, setup_id)
        if link:
            store.set_meta(f"wallet_approve:{workspace_id}", link)
        store.set_wallet(workspace_id, mode="sandbox", status="pending")
        raise VaultRequired(link, setup_id)

    def _finish_setup(self, store, workspace: dict, setup_id: str) -> str:
        token = self.pp.create_payment_token(
            payment_source={"token": {"id": setup_id, "type": "SETUP_TOKEN"}},
            idempotency_key="veto-vault-token",
        )
        vault_id = token["id"]
        store.set_wallet(
            workspace["id"],
            vault_id=vault_id,
            mode="sandbox",
            status="connected",
            connected_at=_now_iso(),
        )
        store.set_meta(f"wallet_setup:{workspace['id']}", "")
        return vault_id

    # -- capture -------------------------------------------------------
    def capture(
        self,
        store,
        workspace: dict,
        *,
        request_id: str,
        title: str,
        total_cents: int,
        currency: str = CURRENCY,
        nonce: str,
        on_step=None,
    ) -> dict:
        """Create the order, authorize it and capture it. Returns the PayPal ids.

        ``nonce`` is the consent's single-use nonce, so every PayPal call for this
        cart is idempotent: a retried approval replays the same objects instead of
        charging twice.

        ``on_step`` (optional) is called with each step dict right after it is
        appended, so a caller can stream order / auth / capture to a console
        while the money moves. It may not change the outcome: a broken callback
        is ignored.
        """
        steps: list[dict] = []

        def _record(step: dict) -> None:
            steps.append(step)
            if on_step is None:
                return
            try:
                on_step(step)
            except Exception:  # noqa: BLE001 - progress reporting never blocks money
                pass

        vault_id = self.ensure_wallet(store, workspace)
        amount = {
            "currency_code": currency,
            "value": f"{total_cents / 100:.2f}",
        }
        try:
            order = self.pp.create_order(
                intent="AUTHORIZE",
                purchase_units=[{
                    "reference_id": request_id,
                    "description": title[:120],
                    "amount": amount,
                }],
                payment_source={
                    "paypal": {
                        "vault_id": vault_id,
                        "stored_credential": {
                            "payment_initiator": "MERCHANT",
                            "usage": "SUBSEQUENT",
                            "usage_pattern": "UNSCHEDULED_PREPAID",
                        },
                    }
                },
                idempotency_key=nonce,
            )
        except PayPalError as exc:
            raise SettlementError(f"could not create the order: {exc}",
                                  steps=steps) from exc

        order_id = order["id"]
        _record({"kind": "order", "label": "Order created",
                 "detail": f"intent AUTHORIZE · {order_id}"})

        # A vaulted wallet is a merchant-initiated payment: PayPal fills the order
        # while creating it, so the authorization already exists. Only authorize
        # ourselves when the order came back without one.
        auth = PayPalClient.first_authorization(order)
        if auth is None and (order.get("status") or "").upper() != "COMPLETED":
            try:
                auth_resp = self.pp.authorize_order(order_id, idempotency_key=nonce + ":auth")
            except PayPalError as exc:
                raise SettlementError(f"could not authorize: {exc}",
                                      steps=steps,
                                      partial={"order_id": order_id}) from exc
            auth = PayPalClient.first_authorization(auth_resp) or auth_resp
        auth = auth or {}
        auth_id = auth.get("id", "")
        if auth_id:
            _record({"kind": "auth", "label": "Authorized",
                     "detail": f"{auth_id} · funds held, not taken"})

        cap = PayPalClient.first_capture(order)
        if cap is None and auth_id:
            try:
                cap_resp = self.pp.capture_authorization(
                    auth_id, idempotency_key=nonce + ":capture"
                )
            except PayPalError as exc:
                raise SettlementError(
                    f"could not capture: {exc}",
                    steps=steps,
                    partial={"order_id": order_id, "authorization_id": auth_id},
                ) from exc
            cap = PayPalClient.first_capture(cap_resp) or cap_resp
        cap = cap or {}
        capture_id = cap.get("id", "")
        status = (cap.get("status") or "COMPLETED").upper()
        _record({"kind": "capture", "label": "Captured",
                 "detail": f"{capture_id or 'no capture id'} · {_money(total_cents)} moved"})
        return {
            "mode": self.mode,
            "vault_id": vault_id,
            "order_id": order_id,
            "authorization_id": auth_id,
            "capture_id": capture_id,
            "status": status,
            "steps": steps,
        }


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _money(cents: int) -> str:
    return "${:,.2f}".format((cents or 0) / 100)
