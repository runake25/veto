"""Veto engine: conversation -> items -> priced cart -> decision -> consent -> capture.

Everything the model touches stops at `extract()`. Pricing, the seven rules, the
consent hash and every PayPal call are plain code.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from veto.prompts import SYSTEM
from veto.channel3 import Channel3, Channel3Error, rank, relevant, to_line  # noqa: E402

from veto.paypal_client import PayPalClient, PayPalError  # noqa: E402

CURRENCY = "USD"
MOCK_CLIENT_ID = "mock-client-id"
MOCK_CLIENT_SECRET = "mock-client-secret"


# ---------------------------------------------------------------------------
# Catalogue. Channel3 replaces this when CHANNEL3_API_KEY is set.
# ---------------------------------------------------------------------------
CATALOG = [
    {"sku": "caps-30", "name": "Vertuo Intenso capsules, 30 ct", "unit": 1800,
     "vendor": "Kettle Supply", "category": "pantry",
     "keys": ["coffee", "capsule", "capsules", "pod", "pods", "espresso", "nespresso"]},
    {"sku": "tp-24", "name": "Bamboo toilet paper, 24 rolls", "unit": 1450,
     "vendor": "Kettle Supply", "category": "pantry",
     "keys": ["toilet", "paper", "toilet paper", "loos", "bathroom", "tissue"]},
    {"sku": "towels-12", "name": "Paper towels, 12 rolls", "unit": 1120,
     "vendor": "Meridian Paper", "category": "pantry",
     "keys": ["paper towels", "towels", "kitchen roll", "hand towels"]},
    {"sku": "marker-4", "name": "Whiteboard marker, 4 pack", "unit": 940,
     "vendor": "Meridian Paper", "category": "stationery",
     "keys": ["marker", "markers", "whiteboard", "dry erase"]},
    {"sku": "a4-5", "name": "A4 copy paper, 5 reams", "unit": 2840,
     "vendor": "Meridian Paper", "category": "stationery",
     "keys": ["a4", "copy paper", "paper", "reams", "printer"]},
    {"sku": "kbd-split", "name": "Split ergonomic keyboard", "unit": 12800,
     "vendor": "Northgate Tools", "category": "equipment",
     "keys": ["keyboard", "ergonomic", "split keyboard"]},
    {"sku": "mat-stand", "name": "Anti-fatigue standing mat, 90 x 60 cm", "unit": 8900,
     "vendor": "Ergo Depot", "category": "equipment",
     "keys": ["mat", "standing mat", "anti-fatigue", "fatigue"]},
    {"sku": "beans-1kg", "name": "Single origin beans, 1 kg", "unit": 2600,
     "vendor": "Kettle Supply", "category": "pantry",
     "keys": ["beans", "coffee beans", "roast"]},
]

CHARTERS = {
    "office": {
        "id": "office", "name": "Office supplies", "approver": "Caroline Hayes",
        "monthly_cap": 80000, "per_order_cap": 30000, "per_unit_cap": 1500,
        "approval_threshold": 5000,
        "vendors": {"Kettle Supply", "Meridian Paper", "Northgate Tools"},
        "merchants": {"grove.co", "whogivesacrap.org", "walmart.com", "target.com",
                      "staples.com", "logitech.com", "amazon.com"},
        "categories": {"pantry", "stationery", "equipment"},
    },
    "lunches": {
        "id": "lunches", "name": "Client lunches", "approver": "Tom Iverson",
        "monthly_cap": 60000, "per_order_cap": 25000, "per_unit_cap": None,
        "approval_threshold": 10000,
        "vendors": {"Brightline Foods"},
        "categories": {"meals"},
    },
}

CATEGORY_TO_CHARTER = {
    "pantry": "office", "stationery": "office", "equipment": "office",
    "meals": "lunches",
}

VENDOR_CHARTER_HINT = {
    "Kettle Supply": "office", "Meridian Paper": "office",
    "Northgate Tools": "office", "Ergo Depot": "office",
    "Brightline Foods": "lunches",
}


def money(cents: int) -> str:
    return "${:,.2f}".format(cents / 100)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


AGENTS = {
    "pantry": {
        "id": "pantry", "name": "Pantry runner", "charter": "office", "channel": "#office-ops",
        "tags": ["pantry", "recurring"], "tag": "pantry",
        "rules": ["per_unit_cap", "approval_threshold", "vendor_allowlist"],
        "categories": ["pantry"],
        "merchants": ["grove.co", "whogivesacrap.org", "walmart.com", "target.com", "amazon.com"],
    },
    "desk": {
        "id": "desk", "name": "Desk kit", "charter": "office", "channel": "#studio",
        "tags": ["equipment", "stationery"], "tag": "equipment",
        "rules": ["per_order_cap", "approval_threshold", "vendor_allowlist"],
        "categories": ["equipment", "stationery"],
        "merchants": ["staples.com", "walmart.com", "target.com", "logitech.com", "amazon.com"],
    },
    "lunch": {
        "id": "lunch", "name": "Client lunch", "charter": "lunches", "channel": "#finance",
        "tags": ["hospitality"], "tag": "hospitality",
        "rules": ["vendor_allowlist", "approval_threshold"],
        "categories": ["meals"],
        "merchants": [],          # food is a service, not a catalogue product
    },
}

# which agent an extracted cart belongs to; the longest phrase wins
AGENT_HINTS = {
    "pantry": ("toilet paper", "paper towel", "coffee", "capsule", "capsules", "pod", "pods",
               "toilet", "tissue", "towels", "soap", "beans", "snack", "tea", "dish", "cleaner"),
    "desk": ("standing mat", "desk mat", "laptop stand", "keyboard", "mouse", "monitor", "dock",
             "marker", "notebook", "pens", "stationery", "copy paper", "a4", "sticky notes",
             "archive box", "envelope"),
    "lunch": ("catering", "restaurant", "dinner", "lunch", "meal"),
}


def guess_agent(items: list) -> dict:
    """Which agent raised this cart, from the items the model extracted."""
    best, score = None, 0
    for item in items:
        low = str(item.get("name") or "").lower()
        for aid, hints in AGENT_HINTS.items():
            for hint in hints:
                if hint in low and len(hint) > score:
                    best, score = aid, len(hint)
    return dict(AGENTS.get(best or "desk"))


def agent_for(charter_id: str, categories: list) -> dict:
    """Fallback when a request predates the agent field."""
    for agent in AGENTS.values():
        if agent["charter"] == charter_id and set(categories) & set(agent["categories"]):
            return dict(agent)
    return dict(AGENTS["desk"])


class VaultRequired(Exception):
    """Live mode needs one payer approval before any spend can happen."""

    def __init__(self, approve_url: str, setup_id: str) -> None:
        super().__init__("payer approval required to vault the company wallet")
        self.approve_url = approve_url
        self.setup_id = setup_id


class ApprovalPending(Exception):
    """The payer has not pressed Approve in PayPal yet."""

    def __init__(self, setup_id: str = "") -> None:
        super().__init__("the payer has not approved the wallet yet")
        self.setup_id = setup_id


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------
def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def deepseek(prompt: str, thread: str, timeout: int = 90) -> dict:
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    base = os.environ.get("DEEPSEEK_BASE", "https://api.deepseek.com").rstrip("/")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")

    body = json.dumps({
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": thread},
        ],
    }).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    raw = data["choices"][0]["message"]["content"]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            raise RuntimeError(f"model did not return JSON: {raw[:200]}")
        return json.loads(m.group(0))


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------
def match_product(text: str) -> dict | None:
    """Deterministic catalogue match. The model never chooses the seller."""
    low = text.lower()
    best, best_score = None, 0
    for product in CATALOG:
        score = 0
        for key in product["keys"]:
            if key in low:
                score = max(score, len(key.split()))
        if score > best_score:
            best, best_score = product, score
    return best


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class Engine:
    def __init__(self, live: bool | None = None) -> None:
        load_dotenv()
        env_live = (os.environ.get("PAYPAL_LIVE", "0") == "1")
        self.live = env_live if live is None else live
        self.mock = None
        self.pp = self._build_paypal()
        self.vault_id: str | None = None
        self.pending_setup: str | None = None
        self.requests: dict[str, dict] = {}
        self.order: list[str] = []
        self.seq = 1045
        self.llm_mode = "deepseek" if os.environ.get("DEEPSEEK_API_KEY") else "unset"
        self.c3 = Channel3()

    @property
    def catalog_mode(self) -> str:
        return "channel3" if self.c3.available else "local"


    # -- paypal ---------------------------------------------------------
    def _build_paypal(self):
        if self.live:
            return PayPalClient.from_env()
        from veto.mock_paypal import MockPayPal
        self.mock = MockPayPal().start()
        return PayPalClient(
            MOCK_CLIENT_ID, MOCK_CLIENT_SECRET, self.mock.base_url,
            merchant_id="MOCK-MERCHANT", timeout=5.0, use_proxy=False,
        )

    @property
    def paypal_mode(self) -> str:
        return "sandbox" if self.live else "mock"

    def _vault_source(self) -> dict:
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

    # -- wallet state on disk -------------------------------------------
    @property
    def _vault_file(self) -> Path:
        return ROOT / ".state" / "vault.json"

    def _read_vault_state(self) -> dict:
        try:
            saved = json.loads(self._vault_file.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(saved, dict):
            return {}
        # a token minted by the local mock must never be replayed against the sandbox
        want = "sandbox" if self.live else "mock"
        if saved.get("mode", "sandbox") != want:
            return {}
        return saved

    def _env_vault(self) -> dict:
        """A pre-approved wallet supplied by the environment.

        Hosted deployments have no browser to walk the /connect approval in, and an
        ephemeral filesystem cannot keep .state. Handing the approved payment token
        in as PAYPAL_VAULT_ID lets the hosted demo capture for real on first boot."""
        vault_id = (os.environ.get("PAYPAL_VAULT_ID") or "").strip()
        if not vault_id or not self.live:
            return {}
        return {"vault_id": vault_id, "mode": "sandbox", "source": "env"}

    def _write_vault_state(self, **fields) -> None:
        state = self._read_vault_state()
        if fields.get("vault_id"):
            state.pop("pending_setup", None)
            state.pop("approve_url", None)
        state.update(fields)
        state.setdefault("mode", "sandbox" if self.live else "mock")
        self._vault_file.parent.mkdir(exist_ok=True)
        self._vault_file.write_text(json.dumps(state, indent=2))

    def ensure_vault(self) -> str:
        """Return the company wallet token, minting one if needed.

        The setup token is persisted, so reloading /connect or restarting the
        server picks up the approval the payer already gave instead of burning
        a fresh one and orphaning the link in their browser."""
        if self.vault_id:
            return self.vault_id

        from_env = self._env_vault()
        if from_env.get("vault_id"):
            self.vault_id = from_env["vault_id"]
            return self.vault_id

        saved = self._read_vault_state()
        if saved.get("vault_id"):
            self.vault_id = saved["vault_id"]
            return self.vault_id

        if not self.live:
            setup = self.pp.create_setup_token(
                payment_source=self._vault_source(),
                idempotency_key=f"veto-vault-setup-{int(time.time()) // 60}",
            )
            setup_id = setup["id"]
            self.pending_setup = setup_id
            self.mock.approve_setup_token(setup_id)
            token = self.pp.create_payment_token(
                payment_source={"token": {"id": setup_id, "type": "SETUP_TOKEN"}},
                idempotency_key="veto-vault-token",
            )
            self.vault_id = token["id"]
            return self.vault_id

        # already waiting on a human? keep the same link alive
        pending = saved.get("pending_setup")
        if pending:
            self.pending_setup = pending
            setup = self.pp.get_setup_token(pending)
            status = (setup.get("status") or "").upper()
            if status == "APPROVED":
                return self.finish_connect()
            link = (PayPalClient.find_link(setup, "approve")
                    or PayPalClient.find_link(setup, "payer-action")
                    or saved.get("approve_url"))
            if link:
                raise VaultRequired(link, pending)
            raise ApprovalPending(pending)

        setup = self.pp.create_setup_token(
            payment_source=self._vault_source(),
            idempotency_key=f"veto-vault-setup-{int(time.time()) // 60}",
        )
        setup_id = setup["id"]
        link = (PayPalClient.find_link(setup, "approve")
                or PayPalClient.find_link(setup, "payer-action"))
        self.pending_setup = setup_id
        if not link:
            raise ApprovalPending(setup_id)
        self._write_vault_state(pending_setup=setup_id, approve_url=link)
        raise VaultRequired(link, setup_id)

    def finish_connect(self) -> str:
        """Call after the payer approves in the browser (live mode only)."""
        saved = self._read_vault_state()
        if saved.get("vault_id") and not self.pending_setup:
            self.vault_id = saved["vault_id"]
            return self.vault_id

        setup_id = self.pending_setup or saved.get("pending_setup")
        if not setup_id:
            raise RuntimeError("no pending setup token; call /api/connect first")

        try:
            token = self.pp.create_payment_token(
                payment_source={"token": {"id": setup_id, "type": "SETUP_TOKEN"}},
                idempotency_key="veto-vault-token",
            )
        except Exception as exc:  # noqa: BLE001 - PayPal says "not approved yet"
            if "SETUP_TOKEN" in str(exc).upper() or "422" in str(exc):
                raise ApprovalPending(setup_id) from exc
            raise
        self.vault_id = token["id"]
        self.pending_setup = None
        self._write_vault_state(vault_id=self.vault_id, customer_id=token.get("customer", {}).get("id"))
        return self.vault_id

    # -- pipeline -------------------------------------------------------
    def create_request(self, text: str, channel: str = "#office-ops",
                       requester: str = "Dan Okafor") -> dict:
        self.seq += 1
        rid = f"REQ-{self.seq}"
        events = [{
            "kind": "slack", "label": "Slack message",
            "detail": f"{channel} · {requester}", "href": text[:140],
        }]

        parsed = deepseek(SYSTEM, text)

        items = [i for i in parsed.get("items", []) if i.get("name")]
        if parsed.get("amendment") and not items:
            raise ValueError("model read the thread as an amendment, not a request")
        if not items:
            raise ValueError("no purchase intent found in that thread")

        events.append({
            "kind": "extract", "label": f"{len(items)} item(s) extracted",
            "detail": ", ".join(f"{i['name']} x{i.get('qty', 1)}" for i in items),
        })

        agent = guess_agent(items)
        charter_preview = CHARTERS.get(agent["charter"], CHARTERS["office"])
        lines, unresolved, sources = [], [], []
        for item in items:
            qty = max(1, int(item.get("qty", 1)))
            built = self._price_item(item["name"], qty, agent, charter_preview)
            if not built:
                unresolved.append(item["name"])
                continue
            lines.append(built["line"])
            sources.append(built["source"])

        if sources:
            via = "Channel3" if "channel3" in sources else "local catalogue"
            merchants = sorted({l.get("vendor", "") for l in lines if l.get("vendor")})
            events.append({
                "kind": "catalogue",
                "label": "Searched the catalogue",
                "detail": f"{agent['name']} · {via} · " + (", ".join(merchants[:3]) or "no merchant"),
            })

        if unresolved:
            events.append({"kind": "block", "label": "No product found",
                           "detail": ", ".join(unresolved)})

        events.append({
            "kind": "price", "label": "Cart priced",
            "detail": " · ".join(f"{l['qty']} x {l['item']}" for l in lines) or "empty",
        })

        total = sum(l["line"] for l in lines)
        charter = CHARTERS.get(agent["charter"]) or self._pick_charter(lines)
        decision = self._evaluate(charter, lines, total, unresolved, agent)

        events.append({
            "kind": "decision", "label": f"Decision: {decision['outcome']}",
            "detail": "; ".join(r["message"] for r in decision["reasons"]) or "within every rule",
        })

        req = {
            "id": rid, "title": " and ".join(i["name"] for i in items[:2]),
            "channel": channel, "requester": requester, "text": text,
            "lines": lines, "total": total, "currency": CURRENCY,
            "charter": charter, "agent": agent, "decision": decision,
            "status": "awaiting" if decision["outcome"] == "review" else decision["outcome"],
            "events": events, "created": now_iso(),
            "consent": self._consent(rid, lines, total, charter),
            "paypal": {}, "amendment": None,
        }
        self.requests[rid] = req
        self.order.append(rid)

        if decision["outcome"] == "auto":
            try:
                self._pay(req)
            except VaultRequired as exc:
                # the cart is approved; the capture waits for the company wallet
                req["paypal"]["needs_vault"] = (exc.args[0] if exc.args else "")
                req["events"].append({
                    "kind": "vault", "label": "Wallet not connected",
                    "detail": "approved, capture waits for a one-time PayPal approval",
                })
        elif decision["outcome"] == "block":
            req["status"] = "blocked"

        return req

    def _price_item(self, name: str, qty: int, agent: dict, charter: dict) -> dict | None:
        """Search the catalogue as this agent, inside its rules, then fall back to
        the local list. Returns {"line": <cart line>, "source": "channel3"|"local"}."""
        if self.c3.available and agent["merchants"] and "meals" not in agent["categories"]:
            try:
                products = self.c3.search(
                    name, limit=8,
                    max_price_cents=charter["per_order_cap"],
                    website_ids=agent["merchants"],
                )
            except Channel3Error:
                products = []
            for product in rank(name, products):
                if not relevant(name, product):
                    break
                line = to_line(product, qty)
                if line and line["category"] in agent["categories"]:
                    return {"line": line, "source": "channel3"}
        product = match_product(name)
        if not product:
            return None
        return {"line": {
            "sku": product["sku"], "item": product["name"], "qty": qty,
            "unit": product["unit"], "vendor": product["vendor"],
            "category": product["category"], "line": product["unit"] * qty,
            "source": "local",
        }, "source": "local"}

    def _pick_charter(self, lines: list[dict]) -> dict:
        if lines:
            first = CATEGORY_TO_CHARTER.get(lines[0]["category"], "office")
            return CHARTERS[first]
        return CHARTERS["office"]

    def _month_spent(self, charter_id: str) -> int:
        return sum(
            r["total"] for r in self.requests.values()
            if r["charter"]["id"] == charter_id and r["status"] in ("auto", "captured")
        )

    def _evaluate(self, charter: dict, lines: list[dict], total: int,
                  unresolved: list[str], agent: dict | None = None) -> dict:
        """The charter supplies the limits; the agent chooses which rules apply.

        An agent only holds some of the seven rules. Anything it does not hold is
        not evaluated at all, so two agents on one charter can treat the same cart
        differently: that is the point of giving each agent its own rule set."""
        reasons: list[dict] = []
        held = set((agent or {}).get("rules") or [])

        if unresolved:
            reasons.append({"rule": "catalogue", "message": f"no product for: {', '.join(unresolved)}"})
            return {"outcome": "block", "reasons": reasons}

        approved = set(charter.get("vendors") or ()) | set(charter.get("merchants") or ())

        def allowed(domain: str) -> bool:
            return any(domain == a or domain.endswith("." + a) for a in approved)

        if "vendor_allowlist" in held:
            bad_vendor = sorted({l["vendor"] for l in lines if l["vendor"] and not allowed(l["vendor"])})
            if bad_vendor:
                reasons.append({"rule": "vendor_allowlist",
                                "message": f"vendor not approved: {', '.join(bad_vendor)}"})
        if "category_allowlist" in held:
            bad_cat = sorted({l["category"] for l in lines if l["category"] not in charter["categories"]})
            if bad_cat:
                reasons.append({"rule": "category_allowlist",
                                "message": f"category not allowed: {', '.join(bad_cat)}"})

        if "per_order_cap" in held and total > charter["per_order_cap"]:
            reasons.append({"rule": "per_order_cap",
                            "message": f"{money(total)} over the {money(charter['per_order_cap'])} per-order cap"})

        if "monthly_cap" in held and self._month_spent(charter["id"]) + total > charter["monthly_cap"]:
            reasons.append({"rule": "monthly_cap",
                            "message": f"would take the month past {money(charter['monthly_cap'])}"})

        hard = {"vendor_allowlist", "category_allowlist", "per_order_cap", "monthly_cap", "catalogue"}
        if any(r["rule"] in hard for r in reasons):
            return {"outcome": "block", "reasons": reasons}

        review = []
        if "per_unit_cap" in held and charter["per_unit_cap"]:
            for l in lines:
                if l["unit"] > charter["per_unit_cap"]:
                    review.append({
                        "rule": "per_unit_cap",
                        "message": f"{money(l['unit'])} per unit against a {money(charter['per_unit_cap'])} cap",
                        "observed": l["unit"], "limit": charter["per_unit_cap"],
                    })
        if "approval_threshold" in held and total > charter["approval_threshold"]:
            review.append({
                "rule": "approval_threshold",
                "message": f"{money(total)} over the {money(charter['approval_threshold'])} approval threshold",
                "observed": total, "limit": charter["approval_threshold"],
            })

        if review:
            return {"outcome": "review", "reasons": review}
        return {"outcome": "auto", "reasons": []}

    def _consent(self, rid: str, lines: list[dict], total: int, charter: dict) -> dict:
        payload = {
            "request": rid, "charter": charter["id"],
            "lines": [[l["sku"], l["qty"], l["unit"]] for l in lines],
            "total": total, "currency": CURRENCY,
        }
        cart_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()
        nonce = uuid.uuid4().hex
        expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(timespec="seconds")
        return {
            "cart_hash": cart_hash, "nonce": nonce, "expires_at": expires,
            "single_use": True, "approver": charter["approver"], "issued": False,
        }

    # -- money ----------------------------------------------------------
    def _pay(self, req: dict) -> dict:
        consent = req["consent"]
        if not consent.get("issued"):
            consent["issued"] = True
            consent["issued_at"] = now_iso()
            req["events"].append({
                "kind": "consent", "label": "Consent issued",
                "detail": f"sha256:{consent['cart_hash'][:12]}… · single-use · 10 min",
            })

        self.ensure_vault()
        nonce = consent["nonce"]
        amount = {"currency_code": CURRENCY, "value": f"{req['total'] / 100:.2f}"}

        order = self.pp.create_order(
            intent="AUTHORIZE",
            purchase_units=[{
                "reference_id": req["id"],
                "description": req["title"][:120],
                "amount": amount,
            }],
            payment_source={
                "paypal": {
                    "vault_id": self.vault_id,
                    "stored_credential": {
                        "payment_initiator": "MERCHANT",
                        "usage": "SUBSEQUENT",
                        "usage_pattern": "UNSCHEDULED_PREPAID",
                    },
                }
            },
            idempotency_key=nonce,
        )
        order_id = order["id"]
        req["paypal"]["order_id"] = order_id
        req["events"].append({"kind": "order", "label": "Order created",
                              "detail": f"intent AUTHORIZE · {order_id}"})

        # A vaulted wallet makes this a merchant-initiated payment: PayPal fills the
        # order while creating it, so the authorization already exists and calling
        # /authorize again answers 422 ORDER_ALREADY_AUTHORIZED. Read what it built;
        # only authorize ourselves when the order came back empty.
        auth = PayPalClient.first_authorization(order)
        if auth is None and (order.get("status") or "").upper() != "COMPLETED":
            auth_resp = self.pp.authorize_order(order_id, idempotency_key=nonce + ":auth")
            auth = PayPalClient.first_authorization(auth_resp) or auth_resp
        auth = auth or {}
        auth_id = auth.get("id", "")
        if auth_id:
            req["paypal"]["authorization_id"] = auth_id
            req["events"].append({"kind": "auth", "label": "Authorized",
                                  "detail": f"{auth_id} · funds held, not taken"})

        cap = PayPalClient.first_capture(order)
        if cap is None and auth_id:
            cap_resp = self.pp.capture_authorization(auth_id, idempotency_key=nonce + ":capture")
            cap = PayPalClient.first_capture(cap_resp) or cap_resp
        cap = cap or {}
        cap_id = cap.get("id", "")
        req["paypal"]["capture_id"] = cap_id
        req["paypal"]["status"] = (cap.get("status") or "COMPLETED")
        req["events"].append({
            "kind": "capture", "label": "Captured",
            "detail": f"{cap_id or 'no capture id'} · {money(req['total'])} moved",
        })
        req["status"] = "captured"
        return req

    def approve(self, rid: str) -> dict:
        req = self.requests.get(rid)
        if not req:
            raise KeyError(rid)
        if req["status"] != "awaiting":
            raise ValueError(f"{rid} is {req['status']}, not awaiting")
        return self._pay(req)

    def veto(self, rid: str) -> dict:
        req = self.requests.get(rid)
        if not req:
            raise KeyError(rid)
        if req["status"] != "awaiting":
            raise ValueError(f"{rid} is {req['status']}, not awaiting")
        req["status"] = "vetoed"
        req["events"].append({
            "kind": "veto", "label": "Vetoed",
            "detail": "no consent issued · zero orders created",
        })
        return req

    # -- views ----------------------------------------------------------
    def snapshot(self) -> dict:
        return {
            "paypal_mode": self.paypal_mode,
            "llm_mode": self.llm_mode,
            "catalog_mode": self.catalog_mode,
            "vault_id": (self.vault_id[:10] + "…") if self.vault_id else None,
            "charters": {
                cid: {
                    **{k: (sorted(v) if isinstance(v, set) else v) for k, v in c.items()},
                    "spent": self._month_spent(cid),
                }
                for cid, c in CHARTERS.items()
            },
            "requests": [self._view(r) for r in self.requests.values()],
        }

    @staticmethod
    def _view(req: dict) -> dict:
        def ser(o):
            return o
        return {
            "id": req["id"], "title": req["title"][:1].upper() + req["title"][1:], "status": req["status"],
            "agent": req.get("agent") or agent_for(req["charter"]["id"], [l.get("category") for l in req["lines"]]),
            "channel": req["channel"], "requester": req["requester"],
            "charter": req["charter"]["name"],
            "lines": [{k: (money(v) if k in ("unit", "line") else v) for k, v in l.items()}
                      for l in req["lines"]],
            "total": money(req["total"]),
            "decision": req["decision"],
            "reasons": [{**r, "observed": money(r["observed"]) if isinstance(r.get("observed"), int) else r.get("observed"),
                         "limit": money(r["limit"]) if isinstance(r.get("limit"), int) else r.get("limit")}
                        for r in req["decision"]["reasons"]],
            "consent": {**req["consent"], "cart_hash": req["consent"]["cart_hash"]},
            "paypal": req["paypal"],
            "events": req["events"],
            "amendment": req["amendment"],
            "created": req["created"],
        }
