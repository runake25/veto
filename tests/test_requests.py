"""Request lifecycle tests: persistence, consent, capture, decisions, voids.

Every PayPal call is a fake or the in-process simulator; every catalogue/model
call is injected. The suite is deterministic and offline.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from veto import auth as veto_auth
from veto import requests as veto_requests
from veto.policy import RULE_KINDS
from veto.settlement import Settlement, SettlementError, VaultRequired
from veto.store import Store


def agent_fields(**overrides) -> dict:
    fields = {
        "name": "Pantry runner",
        "tag": "pantry",
        "channel": "#pantry",
        "monthly_cap_cents": 80_000,
        "per_order_cap_cents": 30_000,
        "per_unit_cap_cents": 10_000,
        "approval_threshold_cents": 5_000,
    }
    fields.update(overrides)
    return fields


def extract_one(name: str = "toilet paper", qty: int = 2):
    return lambda text: {
        "items": [{
            "name": name,
            "qty": qty,
            "query": name,
            "product_type": name,
            "brand": None,
        }]
    }


def search_one(unit_cents: int = 1_000, category: str = "pantry",
               vendor: str = "amazon.com", title: str = "Bamboo toilet paper, 24 rolls"):
    def search(agent_dict, item, qty):
        return [{
            "line": {
                "sku": "tp-24",
                "title": title,
                "qty": qty,
                "unit_cents": unit_cents,
                "line_cents": unit_cents * qty,
                "vendor": vendor,
                "category": category,
                "brand": "Bamboo",
                "image": "https://img.example/tp.jpg",
                "url": "https://amazon.com/dp/tp",
                "source": "local",
            },
            "source": "local",
        }]
    return search


def choose_first(agent_dict, item, lines):
    return lines[0]


class FakeSettlement:
    """The money mover, minus the money: records calls, returns PayPal-shaped ids."""

    mode = "mock"

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict] = []

    def capture(self, store, workspace, *, request_id, title, total_cents,
                currency="USD", nonce, on_step=None):
        self.calls.append({
            "request_id": request_id,
            "title": title,
            "total_cents": total_cents,
            "currency": currency,
            "nonce": nonce,
            "workspace_id": workspace["id"],
        })
        if self.error is not None:
            raise self.error
        steps = [
            {"kind": "order", "label": "Order created", "detail": "intent AUTHORIZE · ORDER-1"},
            {"kind": "auth", "label": "Authorized", "detail": "AUTH-1 · funds held, not taken"},
            {"kind": "capture", "label": "Captured", "detail": "CAPTURE-1"},
        ]
        for step in steps:
            if on_step is not None:
                on_step(step)
        return {
            "mode": "mock",
            "vault_id": "vault_test",
            "order_id": "ORDER-1",
            "authorization_id": "AUTH-1",
            "capture_id": "CAPTURE-1",
            "status": "COMPLETED",
            "steps": steps,
        }


class LifecycleTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Store(Path(self._tmp.name) / "veto.db")
        self.addCleanup(self.store.close)
        self.workspace = self.store.create_workspace("Bracken & Field", monthly_budget_cents=285_000)
        self.agent = self.make_agent()
        self.user = {"id": "usr_approver", "name": "Caroline Hayes", "role": "approver"}

    def make_agent(self, **overrides) -> dict:
        extra = {
            "vendors": overrides.pop("vendors", ["amazon.com"]),
            "categories": overrides.pop("categories", ["pantry"]),
            "rules": overrides.pop("rules", list(RULE_KINDS)),
            "overrides": overrides.pop("overrides", []),
        }
        return self.store.create_agent(
            self.workspace["id"], agent_fields(**overrides), **extra
        )

    def create(self, *, agent=None, extract=None, search=None, choose=None,
               settlement=None, text="2 packs of toilet paper") -> dict:
        return veto_requests.create(
            self.store,
            workspace=self.workspace,
            agent=agent or self.agent,
            text=text,
            origin="api",
            settlement=settlement or FakeSettlement(),
            extract_fn=extract or extract_one(),
            search_fn=search or search_one(),
            choose_fn=choose or choose_first,
        )

    # -- create + capture ---------------------------------------------
    def test_auto_request_captures_and_records_everything(self) -> None:
        record = self.create()
        self.assertEqual(record["status"], "captured")
        self.assertEqual(record["decision_outcome"], "auto")
        self.assertEqual(record["total_cents"], 2_000)
        self.assertEqual(record["title"], "Toilet paper")
        self.assertEqual(len(record["lines"]), 1)
        self.assertEqual(record["lines"][0]["qty"], 2)
        self.assertEqual(record["lines"][0]["line_cents"], 2_000)
        self.assertEqual(record["reasons"], [])
        self.assertEqual(len(record["consents"]), 1)
        self.assertIsNotNone(record["consents"][0]["used_at"])
        self.assertIsNone(record["consents"][0]["approver_user_id"])
        self.assertEqual(len(record["payments"]), 1)
        payment = record["payments"][0]
        self.assertEqual(payment["status"], "COMPLETED")
        self.assertEqual(payment["order_id"], "ORDER-1")
        self.assertEqual(payment["authorization_id"], "AUTH-1")
        self.assertEqual(payment["capture_id"], "CAPTURE-1")
        self.assertEqual(payment["amount_cents"], 2_000)
        kinds = [event["kind"] for event in record["events"]]
        for kind in ("extract", "catalogue", "price", "decision", "consent", "order", "auth", "capture"):
            self.assertIn(kind, kinds)
        self.assertEqual(self.store.spent_cents(self.workspace["id"]), 2_000)
        self.assertEqual(
            self.store.spent_cents(self.workspace["id"], agent_id=self.agent["id"]), 2_000
        )

    def test_request_ids_are_sequential(self) -> None:
        first = self.create()
        second = self.create()
        self.assertTrue(first["id"].startswith("REQ-"))
        self.assertEqual(first["seq"] + 1, second["seq"])
        self.assertNotEqual(first["id"], second["id"])

    def test_a_paused_agent_cannot_buy(self) -> None:
        # The brake has to hold at the money door, not only in the UI: no
        # request row, no consent, no PayPal object.
        self.store.update_agent(
            self.workspace["id"], self.agent["id"], fields={"status": "paused"}
        )
        paused = self.store.agent(self.workspace["id"], self.agent["id"])
        settlement = FakeSettlement()
        with self.assertRaises(veto_requests.RequestError) as caught:
            self.create(agent=paused, settlement=settlement)
        self.assertEqual(caught.exception.code, "agent_paused")
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.store.list_requests(self.workspace["id"]), [])
        self.assertEqual(settlement.calls, [])

    def test_consent_hash_is_the_cart_not_the_text(self) -> None:
        one = self.create(text="2 packs of toilet paper")
        two = self.create(text="please get 2 packs of toilet paper")
        self.assertEqual(one["consents"][0]["cart_hash"], two["consents"][0]["cart_hash"])
        different = self.create(search=search_one(unit_cents=1_100))
        self.assertNotEqual(
            one["consents"][0]["cart_hash"], different["consents"][0]["cart_hash"]
        )

    def test_monthly_history_counts_against_the_cap(self) -> None:
        cheap = self.make_agent(monthly_cap_cents=2_500)
        self.assertEqual(self.create(agent=cheap).get("status"), "captured")
        refused = self.create(agent=cheap)
        self.assertEqual(refused["status"], "blocked")
        rules = [reason["rule"] for reason in refused["reasons"]]
        self.assertIn("monthly_cap", rules)
        self.assertEqual(self.store.committed_cents(self.workspace["id"]), 0)

    # -- review, approve, veto ----------------------------------------
    def test_review_stays_awaiting_until_a_human_approves(self) -> None:
        review = self.make_agent(approval_threshold_cents=100)
        record = self.create(agent=review)
        self.assertEqual(record["status"], "awaiting")
        self.assertEqual(record["decision_outcome"], "review")
        self.assertIn("approval_threshold", [reason["rule"] for reason in record["reasons"]])
        self.assertIsNone(record["consents"][0]["used_at"])
        self.assertEqual(record["payments"], [])
        self.assertEqual(self.store.committed_cents(self.workspace["id"]), 2_000)
        self.assertEqual(self.store.spent_cents(self.workspace["id"]), 0)
        self.assertEqual(self.store.overview_counts(self.workspace["id"])["needs_you"], 1)

    def test_approve_captures_and_names_the_approver(self) -> None:
        review = self.make_agent(approval_threshold_cents=100)
        record = self.create(agent=review)
        settled = veto_requests.approve(
            self.store,
            workspace=self.workspace,
            request_id=record["id"],
            user=self.user,
            settlement=FakeSettlement(),
        )
        self.assertEqual(settled["status"], "captured")
        self.assertEqual(settled["approver_user_id"], self.user["id"])
        self.assertEqual(settled["consents"][0]["approver_user_id"], self.user["id"])
        self.assertIsNotNone(settled["consents"][0]["used_at"])
        events = [event["kind"] for event in settled["events"]]
        self.assertIn("approve", events)
        self.assertEqual(self.store.committed_cents(self.workspace["id"]), 0)
        self.assertEqual(self.store.spent_cents(self.workspace["id"]), 2_000)

    def test_approving_twice_is_a_conflict(self) -> None:
        review = self.make_agent(approval_threshold_cents=100)
        record = self.create(agent=review)
        veto_requests.approve(
            self.store, workspace=self.workspace, request_id=record["id"],
            user=self.user, settlement=FakeSettlement(),
        )
        with self.assertRaises(veto_requests.RequestError) as caught:
            veto_requests.approve(
                self.store, workspace=self.workspace, request_id=record["id"],
                user=self.user, settlement=FakeSettlement(),
            )
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.code, "invalid_state")

    def test_veto_uses_no_consent_and_creates_no_payment(self) -> None:
        review = self.make_agent(approval_threshold_cents=100)
        record = self.create(agent=review)
        vetoed = veto_requests.veto(
            self.store, workspace=self.workspace, request_id=record["id"], user=self.user
        )
        self.assertEqual(vetoed["status"], "vetoed")
        self.assertEqual(vetoed["payments"], [])
        self.assertIsNone(vetoed["consents"][0]["used_at"])
        self.assertIn("veto", [event["kind"] for event in vetoed["events"]])
        self.assertEqual(self.store.committed_cents(self.workspace["id"]), 0)
        self.assertEqual(self.store.spent_cents(self.workspace["id"]), 0)

    def test_void_withdraws_a_pending_request_before_capture(self) -> None:
        review = self.make_agent(approval_threshold_cents=100)
        record = self.create(agent=review)
        voided = veto_requests.void(
            self.store, workspace=self.workspace, request_id=record["id"], user=self.user
        )
        self.assertEqual(voided["status"], "voided")
        self.assertIn("void", [event["kind"] for event in voided["events"]])
        with self.assertRaises(veto_requests.RequestError):
            veto_requests.approve(
                self.store, workspace=self.workspace, request_id=record["id"],
                user=self.user, settlement=FakeSettlement(),
            )

    def test_captured_money_cannot_be_voided(self) -> None:
        record = self.create()
        with self.assertRaises(veto_requests.RequestError) as caught:
            veto_requests.void(
                self.store, workspace=self.workspace, request_id=record["id"], user=self.user
            )
        self.assertEqual(caught.exception.code, "already_captured")

    def test_expired_consent_cannot_be_approved(self) -> None:
        review = self.make_agent(approval_threshold_cents=100)
        record = self.create(agent=review)
        past = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat(timespec="seconds")
        self.store.execute(
            "UPDATE consents SET expires_at = ? WHERE request_id = ?",
            (past, record["id"]),
        )
        with self.assertRaises(veto_requests.RequestError) as caught:
            veto_requests.approve(
                self.store, workspace=self.workspace, request_id=record["id"],
                user=self.user, settlement=FakeSettlement(),
            )
        self.assertEqual(caught.exception.code, "consent_expired")
        self.assertEqual(self.store.spent_cents(self.workspace["id"]), 0)

    # -- refusals ------------------------------------------------------
    def test_blocked_request_has_no_consent_or_payment(self) -> None:
        mismatched = self.make_agent(categories=["equipment"])
        record = self.create(agent=mismatched, search=search_one(category="pantry"))
        self.assertEqual(record["status"], "blocked")
        self.assertEqual(record["decision_outcome"], "block")
        self.assertEqual(record["consents"], [])
        self.assertEqual(record["payments"], [])
        self.assertIn(
            "category_allowlist", [reason["rule"] for reason in record["reasons"]]
        )

    def test_nothing_to_buy_is_a_400(self) -> None:
        with self.assertRaises(veto_requests.RequestError) as caught:
            veto_requests.create(
                self.store, workspace=self.workspace, agent=self.agent, text="   ",
                settlement=FakeSettlement(),
            )
        self.assertEqual(caught.exception.status, 400)

    # -- wallets and PayPal failures ----------------------------------
    def test_missing_wallet_keeps_an_auto_request_approved(self) -> None:
        settlement = FakeSettlement(VaultRequired("https://paypal.test/approve", "SETUP-1"))
        record = self.create(settlement=settlement)
        self.assertEqual(record["status"], "approved")
        self.assertEqual(record["payments"][0]["status"], "wallet_required")
        self.assertIn("wallet", [event["kind"] for event in record["events"]])
        self.assertEqual(self.store.spent_cents(self.workspace["id"]), 0)

    def test_missing_wallet_makes_approve_ask_for_it(self) -> None:
        review = self.make_agent(approval_threshold_cents=100)
        record = self.create(agent=review)
        with self.assertRaises(veto_requests.WalletRequired) as caught:
            veto_requests.approve(
                self.store, workspace=self.workspace, request_id=record["id"],
                user=self.user,
                settlement=FakeSettlement(VaultRequired("https://paypal.test/approve", "SETUP-1")),
            )
        self.assertEqual(caught.exception.status, 428)
        self.assertEqual(caught.exception.approve_url, "https://paypal.test/approve")
        still = self.store.request(self.workspace["id"], record["id"])
        self.assertEqual(still["status"], "awaiting")
        self.assertEqual(still["payments"][0]["status"], "wallet_required")

    def test_capture_failure_keeps_the_record_and_the_promise(self) -> None:
        boom = SettlementError(
            "capture refused",
            steps=[{"kind": "order", "label": "Order created", "detail": "ORDER-X"}],
            partial={"order_id": "ORDER-X"},
        )
        record = self.create(settlement=FakeSettlement(boom))
        self.assertEqual(record["status"], "approved")
        self.assertEqual(record["payments"][0]["status"], "failed")
        self.assertEqual(record["payments"][0]["order_id"], "ORDER-X")
        self.assertIn("capture_failed", [event["kind"] for event in record["events"]])

    def test_approve_reports_capture_failure(self) -> None:
        review = self.make_agent(approval_threshold_cents=100)
        record = self.create(agent=review)
        boom = SettlementError(
            "capture refused",
            steps=[{"kind": "order", "label": "Order created", "detail": "ORDER-X"}],
        )
        with self.assertRaises(veto_requests.RequestError) as caught:
            veto_requests.approve(
                self.store, workspace=self.workspace, request_id=record["id"],
                user=self.user, settlement=FakeSettlement(boom),
            )
        self.assertEqual(caught.exception.code, "capture_failed")
        still = self.store.request(self.workspace["id"], record["id"])
        self.assertEqual(still["status"], "awaiting")

    # -- serialisation, lists, overview -------------------------------
    def test_public_request_never_leaks_the_nonce(self) -> None:
        review = self.make_agent(approval_threshold_cents=100)
        record = self.create(agent=review)
        payload = veto_requests.public_request(record)
        encoded = json.dumps(payload)
        self.assertNotIn("nonce", encoded)
        self.assertNotIn(record["consents"][0]["nonce"], encoded)
        self.assertEqual(payload["consent"]["cart_hash"], record["consents"][0]["cart_hash"])
        self.assertEqual(payload["lines"][0]["line_cents"], 2_000)
        self.assertEqual(payload["events"][0]["kind"], "extract")

    def test_request_list_and_overview_counts(self) -> None:
        review = self.make_agent(approval_threshold_cents=100)
        self.create()  # captured
        awaiting = self.create(agent=review)  # awaiting
        blocked = self.create(
            agent=self.make_agent(categories=["equipment"]),
            search=search_one(category="pantry"),
        )  # blocked
        rows = self.store.list_requests(self.workspace["id"])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["id"], blocked["id"])  # newest first
        self.assertEqual(len(self.store.list_requests(self.workspace["id"], status="awaiting")), 1)
        self.assertEqual(self.store.count_requests(self.workspace["id"]), 3)
        counts = self.store.overview_counts(self.workspace["id"])
        self.assertEqual(counts["needs_you"], 1)
        self.assertEqual(counts["committed_cents"], 2_000)
        self.assertEqual(counts["settled_month_cents"], 2_000)
        self.assertEqual(counts["agent_count"], 3)

    # -- keys ----------------------------------------------------------
    def test_api_keys_mint_resolve_and_revoke(self) -> None:
        plaintext, record = veto_auth.mint_api_key(self.store, self.agent)
        self.assertTrue(plaintext.startswith("vkey_"))
        self.assertNotIn("key_hash", record)
        resolved = veto_auth.resolve_api_key(self.store, plaintext)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["agent"]["id"], self.agent["id"])
        self.assertEqual(resolved["workspace_id"], self.workspace["id"])
        self.assertEqual(len(self.store.list_api_keys(self.workspace["id"])), 1)
        self.assertIsNone(veto_auth.resolve_api_key(self.store, "vkey_nope"))
        self.assertTrue(self.store.revoke_api_key(self.workspace["id"], record["id"]))
        self.assertIsNone(veto_auth.resolve_api_key(self.store, plaintext))
        self.assertFalse(self.store.revoke_api_key(self.workspace["id"], "key_missing"))

    # -- the real (simulated) PayPal client ---------------------------
    def test_settlement_against_the_paypal_simulator(self) -> None:
        settlement = Settlement(live=False)
        self.addCleanup(settlement.close)
        result = settlement.capture(
            self.store,
            self.workspace,
            request_id="REQ-TEST",
            title="Test cart",
            total_cents=1_500,
            nonce="nonce-test-1",
        )
        self.assertEqual(result["mode"], "mock")
        self.assertTrue(result["order_id"])
        self.assertTrue(result["authorization_id"])
        self.assertTrue(result["capture_id"])
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual([step["kind"] for step in result["steps"]], ["order", "auth", "capture"])
        wallet = self.store.wallet(self.workspace["id"])
        self.assertEqual(wallet["status"], "connected")
        self.assertTrue(wallet["vault_id"])


if __name__ == "__main__":
    unittest.main()
