"""A tiny, explicit local fake of the PayPal endpoints Veto uses.

This is not PayPal and it is not a simulator of PayPal's risk engine.  It
implements the request/response *shape* of the small API surface in
``paypal_client.py`` so the whole A/B/C flow can run offline with zero
credentials.

What is real here:
  * OAuth2 client-credentials exchange and bearer enforcement
  * ``PayPal-Request-Id`` idempotent replay (same method + path + id -> the
    original status/body, with a ``Mock-Idempotent-Replay: true`` header)
  * order / authorization / capture / vault state transitions
  * realistic 4xx envelopes with ``name``, ``details[].issue`` and ``debug_id``
  * a separate payments-authorization capture endpoint, because that is the
    correct endpoint after an AUTHORIZE-intent order

What is deliberately fake:
  * IDs are synthetic but correctly shaped
  * no payer login, 3DS, risk checks, funding, fees, currencies or webhooks
  * the mock-only admin endpoint approves a setup token directly.  Real
    PayPal requires the payer to approve in a browser.
  * the order status ``AUTHORIZED`` is a synthetic convenience.  Real Orders
    v2 has CREATED/SAVED/APPROVED/VOIDED/COMPLETED/PAYER_ACTION_REQUIRED; the
    authorization itself has CREATED/CAPTURED/VOIDED/PENDING.
"""

from __future__ import annotations

import argparse
import json
import random
import string
import threading
import time
import traceback
import urllib.parse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Mapping, Optional, Tuple


MOCK_CLIENT_ID = "mock-client-id"
MOCK_CLIENT_SECRET = "mock-client-secret"
MOCK_ACCESS_TOKEN = "mock-access-token"
MOCK_PAYER_ID = "MOCKPAYERID123"
MOCK_MERCHANT_ID = "MOCKMERCHANT123"
MOCK_SELLER_EMAIL = "sb-seller@business.example.com"
MOCK_BUYER_EMAIL = "sb-buyer@personal.example.com"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def utc_in(days: int = 0, hours: int = 0) -> str:
    return (
        (datetime.now(timezone.utc) + timedelta(days=days, hours=hours))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _money(value: Any, name: str = "amount.value") -> Decimal:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MockAPIError(
            422,
            "UNPROCESSABLE_ENTITY",
            f"The requested action could not be performed: invalid {name}.",
            issue="INVALID_PARAMETER_VALUE",
            field=name,
            description=f"{name} must be a decimal string such as 1.00",
        ) from exc
    if decimal <= 0:
        raise MockAPIError(
            422,
            "UNPROCESSABLE_ENTITY",
            "Amount must be greater than zero.",
            issue="INVALID_PARAMETER_VALUE",
            field=name,
        )
    return decimal


def _format_money(value: Decimal) -> str:
    return f"{value:.2f}"


class MockAPIError(Exception):
    def __init__(
        self,
        status: int,
        name: str,
        message: str,
        *,
        issue: Optional[str] = None,
        field: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.name = name
        self.message = message
        self.issue = issue
        self.field = field
        self.description = description

    def payload(self, debug_id: str) -> Dict[str, Any]:
        details: List[Dict[str, Any]] = []
        if self.issue:
            detail: Dict[str, Any] = {"issue": self.issue}
            if self.field:
                detail["field"] = self.field
            if self.description:
                detail["description"] = self.description
            details.append(detail)
        return {
            "name": self.name,
            "message": self.message,
            "debug_id": debug_id,
            "details": details,
        }


class MockPayPal:
    """In-process PayPal fake.  Use as a context manager or .start()/.stop()."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        *,
        client_id: str = MOCK_CLIENT_ID,
        client_secret: str = MOCK_CLIENT_SECRET,
        latency: float = 0.0,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.client_id = client_id
        self.client_secret = client_secret
        self.latency = max(0.0, float(latency))
        self.base_url = ""

        self._lock = threading.RLock()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._seq = 0
        self._customer_seq = 0
        self._started_at = time.time()

        self.orders: Dict[str, Dict[str, Any]] = {}
        self.authorizations: Dict[str, Dict[str, Any]] = {}
        self.captures: Dict[str, Dict[str, Any]] = {}
        self.setup_tokens: Dict[str, Dict[str, Any]] = {}
        self.payment_tokens: Dict[str, Dict[str, Any]] = {}
        self.idempotency: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        self.request_log: List[Dict[str, Any]] = []
        self.last_internal_error: Optional[str] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> "MockPayPal":
        if self._server is not None:
            return self
        mock = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                mock._handle(self, "GET")

            def do_POST(self) -> None:  # noqa: N802
                mock._handle(self, "POST")

            def do_DELETE(self) -> None:  # noqa: N802
                mock._handle(self, "DELETE")

            def do_PUT(self) -> None:  # noqa: N802
                mock._handle(self, "PUT")

            def log_message(self, fmt: str, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._server.daemon_threads = True
        self.port = int(self._server.server_address[1])
        self.base_url = f"http://{self.host}:{self.port}"
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="veto-mock-paypal",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=2.0)

    def __enter__(self) -> "MockPayPal":
        return self.start()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Inspection helpers used by gate.py
    # ------------------------------------------------------------------
    @property
    def replay_count(self) -> int:
        with self._lock:
            return sum(1 for entry in self.request_log if entry.get("replayed"))

    @property
    def request_count(self) -> int:
        with self._lock:
            return len(self.request_log)

    def requests_to(self, method: str, path: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                dict(item)
                for item in self.request_log
                if item["method"] == method and item["path"] == path
            ]

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "base_url": self.base_url,
                "orders": len(self.orders),
                "authorizations": len(self.authorizations),
                "captures": len(self.captures),
                "setup_tokens": len(self.setup_tokens),
                "payment_tokens": len(self.payment_tokens),
                "requests": len(self.request_log),
                "replays": sum(1 for e in self.request_log if e.get("replayed")),
            }

    # ------------------------------------------------------------------
    # Mock-only payer approval (real PayPal uses a browser redirect)
    # ------------------------------------------------------------------
    def approve_setup_token(self, setup_token_id: str) -> Dict[str, Any]:
        """Simulate the payer approving the setup token in a browser."""

        with self._lock:
            setup = self.setup_tokens.get(setup_token_id)
            if setup is None:
                raise MockAPIError(
                    404,
                    "RESOURCE_NOT_FOUND",
                    "The specified resource does not exist.",
                    issue="RESOURCE_NOT_FOUND",
                )
            if setup["status"] == "PAYER_ACTION_REQUIRED":
                setup["status"] = "APPROVED"
                setup["approved_at"] = utc_now()
                setup["update_time"] = setup["approved_at"]
            return self._setup_token_response(setup)

    # ------------------------------------------------------------------
    # Request handling
    # ------------------------------------------------------------------
    def _handle(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        raw_body = b""
        try:
            length = int(handler.headers.get("Content-Length") or 0)
            if length:
                raw_body = handler.rfile.read(length)
        except (ValueError, OSError):
            raw_body = b""

        parsed = urllib.parse.urlparse(handler.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)
        headers = {k.lower(): v for k, v in handler.headers.items()}
        request_id = headers.get("paypal-request-id")
        debug_id = "mock-" + "".join(
            random.choice(string.hexdigits.lower()[:16]) for _ in range(12)
        )
        if self.latency:
            time.sleep(self.latency)

        entry: Dict[str, Any] = {
            "method": method,
            "path": path,
            "query": parsed.query,
            "request_id": request_id,
            "status": 0,
            "replayed": False,
            "at": utc_now(),
        }

        try:
            if self._is_token_path(path):
                status, payload, extra = self._oauth_token(
                    method, raw_body, headers, debug_id
                )
            else:
                if not path.startswith("/__mock/"):
                    self._require_auth(headers, debug_id)
                if method == "POST" and not path.startswith("/__mock/"):
                    if not request_id:
                        raise MockAPIError(
                            400,
                            "INVALID_REQUEST",
                            "A PayPal-Request-Id header is required for POST requests.",
                            issue="MISSING_REQUEST_ID",
                            field="PayPal-Request-Id",
                        )
                if (
                    method == "POST"
                    and request_id
                    and not path.startswith("/__mock/")
                    and not self._is_token_path(path)
                ):
                    cache_key = (method, path + ("?" + parsed.query if parsed.query else ""), request_id)
                    cached = self.idempotency.get(cache_key)
                    if cached is not None:
                        entry["status"] = cached["status"]
                        entry["replayed"] = True
                        self.request_log.append(entry)
                        replay_headers = dict(cached["headers"])
                        replay_headers["Mock-Idempotent-Replay"] = "true"
                        self._send_raw(
                            handler,
                            cached["status"],
                            cached["body"],
                            replay_headers,
                        )
                        return

                body = self._parse_body(raw_body, headers, debug_id)
                status, payload, extra = self._dispatch(
                    method,
                    path,
                    query,
                    body,
                    headers,
                    debug_id,
                    request_id,
                )

            body = b"" if payload is None else json.dumps(
                payload, separators=(",", ":")
            ).encode("utf-8")
            response_headers = {
                "PayPal-Debug-Id": debug_id,
                "Content-Type": "application/json",
            }
            if request_id:
                response_headers["PayPal-Request-Id"] = request_id
            if extra:
                response_headers.update({str(k): str(v) for k, v in extra.items()})

            if (
                method == "POST"
                and request_id
                and not path.startswith("/__mock/")
                and not self._is_token_path(path)
            ):
                cache_key = (
                    method,
                    path + ("?" + parsed.query if parsed.query else ""),
                    request_id,
                )
                self.idempotency[cache_key] = {
                    "status": status,
                    "body": body,
                    "headers": dict(response_headers),
                }

            entry["status"] = status
            self.request_log.append(entry)
            self._send_raw(handler, status, body, response_headers)
            return

        except MockAPIError as exc:
            body = json.dumps(exc.payload(debug_id), separators=(",", ":")).encode(
                "utf-8"
            )
            response_headers = {
                "PayPal-Debug-Id": debug_id,
                "Content-Type": "application/json",
            }
            if request_id:
                response_headers["PayPal-Request-Id"] = request_id

            if (
                method == "POST"
                and request_id
                and not path.startswith("/__mock/")
                and not self._is_token_path(path)
            ):
                cache_key = (
                    method,
                    path + ("?" + parsed.query if parsed.query else ""),
                    request_id,
                )
                self.idempotency[cache_key] = {
                    "status": exc.status,
                    "body": body,
                    "headers": dict(response_headers),
                }

            entry["status"] = exc.status
            self.request_log.append(entry)
            self._send_raw(handler, exc.status, body, response_headers)
            return

        except Exception:
            self.last_internal_error = traceback.format_exc()
            debug_id = "mock-internal-" + str(int(time.time() * 1000))
            body = json.dumps(
                {
                    "name": "INTERNAL_SERVER_ERROR",
                    "message": "The mock hit an internal error. See mock.last_internal_error.",
                    "debug_id": debug_id,
                    "details": [],
                },
                separators=(",", ":"),
            ).encode("utf-8")
            entry["status"] = 500
            self.request_log.append(entry)
            self._send_raw(
                handler,
                500,
                body,
                {
                    "PayPal-Debug-Id": debug_id,
                    "Content-Type": "application/json",
                },
            )

    def _send_raw(
        self,
        handler: BaseHTTPRequestHandler,
        status: int,
        body: bytes,
        headers: Mapping[str, str],
    ) -> None:
        try:
            handler.send_response(status)
            for key, value in headers.items():
                handler.send_header(key, value)
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            if body:
                handler.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    @staticmethod
    def _is_token_path(path: str) -> bool:
        return path.rstrip("/") == "/v1/oauth2/token"

    def _require_auth(self, headers: Mapping[str, str], debug_id: str) -> None:
        auth = headers.get("authorization", "")
        if auth != f"Bearer {MOCK_ACCESS_TOKEN}":
            raise MockAPIError(
                401,
                "AUTHENTICATION_FAILURE",
                "Authentication failed due to invalid authentication credentials "
                "or a missing Authorization header.",
                issue="AUTHENTICATION_FAILURE",
            )

    def _parse_body(
        self, raw: bytes, headers: Mapping[str, str], debug_id: str
    ) -> Dict[str, Any]:
        if not raw:
            return {}
        content_type = headers.get("content-type", "")
        if "application/x-www-form-urlencoded" in content_type:
            return {
                k: v[0]
                for k, v in urllib.parse.parse_qs(
                    raw.decode("utf-8", errors="replace")
                ).items()
            }
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MockAPIError(
                400,
                "INVALID_REQUEST",
                "Request is not well-formed, syntactically incorrect, or violates schema.",
                issue="MALFORMED_REQUEST_JSON",
                description=str(exc),
            ) from exc
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise MockAPIError(
                400,
                "INVALID_REQUEST",
                "JSON body must be an object.",
                issue="MALFORMED_REQUEST_JSON",
            )
        return payload

    def _oauth_token(
        self,
        method: str,
        raw: bytes,
        headers: Mapping[str, str],
        debug_id: str,
    ) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        if method != "POST":
            raise MockAPIError(
                405, "METHOD_NOT_ALLOWED", "The token endpoint only accepts POST."
            )
        auth = headers.get("authorization", "")
        form = {
            k: v[0]
            for k, v in urllib.parse.parse_qs(
                raw.decode("utf-8", errors="replace")
            ).items()
        }
        if not auth.lower().startswith("basic "):
            raise MockAPIError(
                401,
                "AUTHENTICATION_FAILURE",
                "Client authentication failed.",
                issue="AUTHENTICATION_FAILURE",
            )
        try:
            import base64

            decoded = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8")
            supplied_id, supplied_secret = decoded.split(":", 1)
        except Exception as exc:
            raise MockAPIError(
                401,
                "AUTHENTICATION_FAILURE",
                "Client authentication failed.",
                issue="AUTHENTICATION_FAILURE",
            ) from exc
        if supplied_id != self.client_id or supplied_secret != self.client_secret:
            raise MockAPIError(
                401,
                "AUTHENTICATION_FAILURE",
                "Client authentication failed.",
                issue="AUTHENTICATION_FAILURE",
            )
        if form.get("grant_type") != "client_credentials":
            raise MockAPIError(
                400,
                "UNSUPPORTED_GRANT_TYPE",
                "The grant type is not supported.",
                issue="UNSUPPORTED_GRANT_TYPE",
            )
        return (
            200,
            {
                "scope": (
                    "https://uri.paypal.com/services/payments/payment/authcapture "
                    "https://uri.paypal.com/services/vault/payment-tokens/readwrite"
                ),
                "access_token": MOCK_ACCESS_TOKEN,
                "token_type": "Bearer",
                "app_id": "APP-MOCKVETO0001",
                "expires_in": 32400,
                "nonce": "mock-nonce",
            },
            {},
        )

    # ------------------------------------------------------------------
    # Router
    # ------------------------------------------------------------------
    def _dispatch(
        self,
        method: str,
        path: str,
        query: Mapping[str, List[str]],
        body: Dict[str, Any],
        headers: Mapping[str, str],
        debug_id: str,
        request_id: Optional[str],
    ) -> Tuple[int, Optional[Dict[str, Any]], Dict[str, str]]:
        parts = [part for part in path.strip("/").split("/") if part]

        # Mock-only administration.
        if parts[:2] == ["__mock", "state"] and method == "GET":
            return 200, self.summary(), {}
        if (
            len(parts) == 4
            and parts[0] == "__mock"
            and parts[1] == "setup-tokens"
            and parts[3] == "approve"
            and method == "POST"
        ):
            setup = self.approve_setup_token(parts[2])
            return 200, setup, {}
        if parts[:2] == ["__mock", "reset"] and method == "POST":
            with self._lock:
                self.orders.clear()
                self.authorizations.clear()
                self.captures.clear()
                self.setup_tokens.clear()
                self.payment_tokens.clear()
                self.idempotency.clear()
                self.request_log.clear()
            return 200, {"reset": True}, {}

        # Orders v2
        if parts == ["v2", "checkout", "orders"] and method == "POST":
            return 201, self._create_order(body), {}
        if len(parts) == 4 and parts[:3] == ["v2", "checkout", "orders"]:
            if method == "GET":
                return 200, self._get_order(parts[3]), {}
        if (
            len(parts) == 5
            and parts[:3] == ["v2", "checkout", "orders"]
            and method == "POST"
        ):
            if parts[4] == "authorize":
                return 201, self._authorize_order(parts[3]), {}
            if parts[4] == "capture":
                return 201, self._capture_order(parts[3], body), {}

        # Payments v2
        if (
            len(parts) == 4
            and parts[:3] == ["v2", "payments", "authorizations"]
            and method == "GET"
        ):
            return 200, self._get_authorization(parts[3]), {}
        if (
            len(parts) == 5
            and parts[:3] == ["v2", "payments", "authorizations"]
            and method == "POST"
        ):
            if parts[4] == "capture":
                return 201, self._capture_authorization(parts[3], body), {}
            if parts[4] == "void":
                return self._void_authorization(parts[3])
            if parts[4] == "reauthorize":
                raise MockAPIError(
                    422,
                    "UNPROCESSABLE_ENTITY",
                    "Reauthorize is intentionally not implemented in the mock.",
                    issue="NOT_IMPLEMENTED_IN_MOCK",
                )

        # Payment Method Tokens v3
        if parts == ["v3", "vault", "setup-tokens"] and method == "POST":
            return 201, self._create_setup_token(body), {}
        if len(parts) == 4 and parts[:3] == ["v3", "vault", "setup-tokens"]:
            if method == "GET":
                return 200, self._get_setup_token(parts[3]), {}
        if parts == ["v3", "vault", "payment-tokens"] and method == "POST":
            return 201, self._create_payment_token(body), {}
        if parts == ["v3", "vault", "payment-tokens"] and method == "GET":
            return 200, self._list_payment_tokens(query), {}
        if len(parts) == 4 and parts[:3] == ["v3", "vault", "payment-tokens"]:
            if method == "GET":
                return 200, self._get_payment_token(parts[3]), {}
            if method == "DELETE":
                return self._delete_payment_token(parts[3])

        raise MockAPIError(
            404,
            "RESOURCE_NOT_FOUND",
            f"No mock route for {method} {path}.",
            issue="RESOURCE_NOT_FOUND",
        )

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------
    def _create_order(self, body: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            intent = str(body.get("intent") or "").upper()
            if intent not in ("AUTHORIZE", "CAPTURE"):
                raise MockAPIError(
                    422,
                    "UNPROCESSABLE_ENTITY",
                    "The order intent is invalid.",
                    issue="INVALID_PARAMETER_VALUE",
                    field="intent",
                )
            units = body.get("purchase_units")
            if not isinstance(units, list) or not units:
                raise MockAPIError(
                    422,
                    "UNPROCESSABLE_ENTITY",
                    "At least one purchase unit is required.",
                    issue="MISSING_REQUIRED_PARAMETER",
                    field="purchase_units",
                )
            total, currency = self._validate_units(units)

            source_response: Dict[str, Any] = {}
            payer_action_required = True
            payment_source = body.get("payment_source")
            if isinstance(payment_source, dict):
                if "paypal" in payment_source:
                    paypal = payment_source.get("paypal") or {}
                    vault_id = paypal.get("vault_id") if isinstance(paypal, dict) else None
                    if vault_id:
                        token = self.payment_tokens.get(str(vault_id))
                        if token is None or token["type"] != "paypal":
                            raise MockAPIError(
                                422,
                                "UNPROCESSABLE_ENTITY",
                                "The vault id does not reference a saved PayPal wallet.",
                                issue="VAULT_ID_NOT_FOUND",
                                field="payment_source.paypal.vault_id",
                            )
                        source_response = {
                            "paypal": {
                                "email_address": MOCK_BUYER_EMAIL,
                                "account_id": MOCK_PAYER_ID,
                                "name": {
                                    "given_name": "Sandbox",
                                    "surname": "Buyer",
                                },
                                "address": {"country_code": "US"},
                            }
                        }
                        payer_action_required = False
                elif "card" in payment_source:
                    card = payment_source.get("card") or {}
                    vault_id = card.get("vault_id") if isinstance(card, dict) else None
                    if vault_id:
                        token = self.payment_tokens.get(str(vault_id))
                        if token is None or token["type"] != "card":
                            raise MockAPIError(
                                422,
                                "UNPROCESSABLE_ENTITY",
                                "The vault id does not reference a saved card.",
                                issue="VAULT_ID_NOT_FOUND",
                                field="payment_source.card.vault_id",
                            )
                        card_source = token["source"]
                        source_response = {
                            "card": {
                                "last_digits": card_source["last_digits"],
                                "expiry": card_source["expiry"],
                                "name": card_source.get("name", "Sandbox Buyer"),
                                "brand": card_source.get("brand", "VISA"),
                                "billing_address": card_source.get(
                                    "billing_address", {"country_code": "US"}
                                ),
                            }
                        }
                        payer_action_required = False

            now = utc_now()
            order_id = self._new_id("5O1")
            order: Dict[str, Any] = {
                "id": order_id,
                "intent": intent,
                "status": "CREATED",
                "purchase_units": [
                    {
                        "reference_id": unit.get("reference_id") or "default",
                        "amount": {
                            "currency_code": currency,
                            "value": _format_money(total),
                        },
                        "payee": {
                            "email_address": MOCK_SELLER_EMAIL,
                            "merchant_id": MOCK_MERCHANT_ID,
                        },
                    }
                    for unit in units
                ],
                "create_time": now,
                "update_time": now,
                "links": [
                    {
                        "href": f"{self.base_url}/v2/checkout/orders/{order_id}",
                        "rel": "self",
                        "method": "GET",
                    }
                ],
                "mock_status_history": ["CREATED"],
                "mock_payer_action_required": payer_action_required,
            }
            if source_response:
                order["payment_source"] = source_response
            if payer_action_required:
                order["links"].extend(
                    [
                        {
                            "href": (
                                "https://sandbox.paypal.com/checkoutnow?token="
                                + order_id
                            ),
                            "rel": "approve",
                            "method": "GET",
                        },
                        {
                            "href": (
                                "https://sandbox.paypal.com/checkoutnow?token="
                                + order_id
                                + "&payeraction=1"
                            ),
                            "rel": "payer-action",
                            "method": "GET",
                        },
                    ]
                )
            self.orders[order_id] = order
            return self._public_order(order)

    def _get_order(self, order_id: str) -> Dict[str, Any]:
        with self._lock:
            order = self.orders.get(order_id)
            if order is None:
                raise MockAPIError(
                    404,
                    "RESOURCE_NOT_FOUND",
                    "The specified resource does not exist.",
                    issue="RESOURCE_NOT_FOUND",
                )
            return self._public_order(order)

    def _authorize_order(self, order_id: str) -> Dict[str, Any]:
        with self._lock:
            order = self.orders.get(order_id)
            if order is None:
                raise MockAPIError(
                    404,
                    "RESOURCE_NOT_FOUND",
                    "The specified resource does not exist.",
                    issue="RESOURCE_NOT_FOUND",
                )
            if order["intent"] != "AUTHORIZE":
                raise MockAPIError(
                    422,
                    "UNPROCESSABLE_ENTITY",
                    "The order intent does not support authorization.",
                    issue="ACTION_DOES_NOT_MATCH_INTENT",
                )
            if order["status"] == "CAPTURED" or order["status"] == "COMPLETED":
                raise MockAPIError(
                    422,
                    "UNPROCESSABLE_ENTITY",
                    "The order has already been captured.",
                    issue="ORDER_ALREADY_CAPTURED",
                )
            if order["status"] in ("AUTHORIZED", "APPROVED"):
                raise MockAPIError(
                    422,
                    "UNPROCESSABLE_ENTITY",
                    "The order has already been authorized.",
                    issue="ORDER_ALREADY_AUTHORIZED",
                )
            if order["status"] == "VOIDED":
                raise MockAPIError(
                    422,
                    "UNPROCESSABLE_ENTITY",
                    "The order has been voided.",
                    issue="ORDER_ALREADY_VOIDED",
                )
            if order.get("mock_payer_action_required"):
                raise MockAPIError(
                    422,
                    "UNPROCESSABLE_ENTITY",
                    "Payer action is required before this order can be authorized.",
                    issue="PAYER_ACTION_REQUIRED",
                )

            unit = order["purchase_units"][0]
            amount = dict(unit["amount"])
            now = utc_now()
            auth_id = self._new_id("0AW")
            auth: Dict[str, Any] = {
                "id": auth_id,
                "status": "CREATED",
                "amount": amount,
                "seller_protection": {
                    "status": "ELIGIBLE",
                    "dispute_categories": [
                        "ITEM_NOT_RECEIVED",
                        "UNAUTHORIZED_TRANSACTION",
                    ],
                },
                "expiration_time": utc_in(days=29),
                "create_time": now,
                "update_time": now,
                "links": [],
                "_order_id": order_id,
                "capture_ids": [],
            }
            self.authorizations[auth_id] = auth
            order["status"] = "AUTHORIZED"
            order["update_time"] = now
            order["mock_status_history"].extend(["APPROVED", "AUTHORIZED"])
            order.setdefault("purchase_units", [])[0].setdefault("payments", {})[
                "authorizations"
            ] = [self._public_authorization(auth)]
            order["links"] = [
                {
                    "href": f"{self.base_url}/v2/checkout/orders/{order_id}",
                    "rel": "self",
                    "method": "GET",
                }
            ]
            return self._public_order(order)

    def _capture_order(
        self, order_id: str, body: Dict[str, Any]
    ) -> Dict[str, Any]:
        with self._lock:
            order = self.orders.get(order_id)
            if order is None:
                raise MockAPIError(
                    404,
                    "RESOURCE_NOT_FOUND",
                    "The specified resource does not exist.",
                    issue="RESOURCE_NOT_FOUND",
                )
            if order["intent"] == "AUTHORIZE":
                raise MockAPIError(
                    422,
                    "UNPROCESSABLE_ENTITY",
                    "The order was created with intent=AUTHORIZE. Capture the "
                    "authorization through /v2/payments/authorizations/{id}/capture.",
                    issue="ACTION_DOES_NOT_MATCH_INTENT",
                    field="intent",
                )
            if order.get("mock_payer_action_required"):
                raise MockAPIError(
                    422,
                    "UNPROCESSABLE_ENTITY",
                    "Payer action is required before this order can be captured.",
                    issue="PAYER_ACTION_REQUIRED",
                )
            if order["status"] in ("COMPLETED", "CAPTURED"):
                raise MockAPIError(
                    422,
                    "UNPROCESSABLE_ENTITY",
                    "The order has already been captured.",
                    issue="ORDER_ALREADY_CAPTURED",
                )
            unit = order["purchase_units"][0]
            now = utc_now()
            capture_id = self._new_id("3C6")
            capture = self._make_capture(capture_id, unit["amount"], now)
            self.captures[capture_id] = capture
            order["status"] = "COMPLETED"
            order["update_time"] = now
            order["mock_status_history"].extend(["APPROVED", "AUTHORIZED", "COMPLETED"])
            unit.setdefault("payments", {})["captures"] = [
                self._public_capture(capture)
            ]
            return self._public_order(order)

    def _get_authorization(self, authorization_id: str) -> Dict[str, Any]:
        with self._lock:
            auth = self.authorizations.get(authorization_id)
            if auth is None:
                raise MockAPIError(
                    404,
                    "RESOURCE_NOT_FOUND",
                    "The specified resource does not exist.",
                    issue="RESOURCE_NOT_FOUND",
                )
            return self._public_authorization(auth)

    def _capture_authorization(
        self, authorization_id: str, body: Dict[str, Any]
    ) -> Dict[str, Any]:
        with self._lock:
            auth = self.authorizations.get(authorization_id)
            if auth is None:
                raise MockAPIError(
                    404,
                    "RESOURCE_NOT_FOUND",
                    "The specified resource does not exist.",
                    issue="RESOURCE_NOT_FOUND",
                )
            if auth["status"] == "CAPTURED":
                raise MockAPIError(
                    422,
                    "UNPROCESSABLE_ENTITY",
                    "The authorization has already been captured.",
                    issue="AUTHORIZATION_ALREADY_CAPTURED",
                )
            if auth["status"] == "VOIDED":
                raise MockAPIError(
                    422,
                    "UNPROCESSABLE_ENTITY",
                    "The authorization has been voided.",
                    issue="AUTHORIZATION_VOIDED",
                )
            if auth["status"] == "EXPIRED":
                raise MockAPIError(
                    422,
                    "UNPROCESSABLE_ENTITY",
                    "The authorization has expired.",
                    issue="AUTHORIZATION_EXPIRED",
                )

            requested = body.get("amount") if isinstance(body, dict) else None
            amount = dict(auth["amount"])
            if requested:
                if not isinstance(requested, Mapping):
                    raise MockAPIError(
                        422,
                        "UNPROCESSABLE_ENTITY",
                        "amount must be an object.",
                        issue="INVALID_PARAMETER_VALUE",
                        field="amount",
                    )
                if requested.get("currency_code") and (
                    requested["currency_code"] != amount["currency_code"]
                ):
                    raise MockAPIError(
                        422,
                        "UNPROCESSABLE_ENTITY",
                        "The capture currency must match the authorization currency.",
                        issue="CURRENCY_MISMATCH",
                        field="amount.currency_code",
                    )
                requested_value = _money(
                    requested.get("value"), "amount.value"
                )
                authorized_value = Decimal(str(amount["value"]))
                if requested_value > authorized_value:
                    raise MockAPIError(
                        422,
                        "UNPROCESSABLE_ENTITY",
                        "The capture amount exceeds the authorized amount.",
                        issue="CAPTURE_AMOUNT_EXCEEDS_AUTHORIZED_AMOUNT",
                        field="amount.value",
                    )
                amount["value"] = _format_money(requested_value)

            now = utc_now()
            capture_id = self._new_id("3C6")
            capture = self._make_capture(capture_id, amount, now)
            self.captures[capture_id] = capture
            auth["status"] = "CAPTURED"
            auth["update_time"] = now
            auth["capture_ids"].append(capture_id)

            order = self.orders.get(auth["_order_id"])
            if order is not None:
                order["status"] = "COMPLETED"
                order["update_time"] = now
                order["mock_status_history"].append("COMPLETED")
                unit = order["purchase_units"][0]
                unit.setdefault("payments", {})["authorizations"] = [
                    self._public_authorization(auth)
                ]
                unit["payments"]["captures"] = [self._public_capture(capture)]
                order["links"] = [
                    {
                        "href": f"{self.base_url}/v2/checkout/orders/{order['id']}",
                        "rel": "self",
                        "method": "GET",
                    }
                ]
            return self._public_capture(capture)

    def _void_authorization(
        self, authorization_id: str
    ) -> Tuple[int, None, Dict[str, str]]:
        with self._lock:
            auth = self.authorizations.get(authorization_id)
            if auth is None:
                raise MockAPIError(
                    404,
                    "RESOURCE_NOT_FOUND",
                    "The specified resource does not exist.",
                    issue="RESOURCE_NOT_FOUND",
                )
            if auth["status"] == "CAPTURED":
                raise MockAPIError(
                    422,
                    "UNPROCESSABLE_ENTITY",
                    "The authorization has already been captured; refund the "
                    "capture instead of voiding the authorization.",
                    issue="AUTHORIZATION_ALREADY_CAPTURED",
                )
            if auth["status"] == "VOIDED":
                raise MockAPIError(
                    422,
                    "UNPROCESSABLE_ENTITY",
                    "The authorization has already been voided.",
                    issue="AUTHORIZATION_VOIDED",
                )
            now = utc_now()
            auth["status"] = "VOIDED"
            auth["update_time"] = now
            order = self.orders.get(auth["_order_id"])
            if order is not None:
                order["status"] = "VOIDED"
                order["update_time"] = now
                order["mock_status_history"].append("VOIDED")
                unit = order["purchase_units"][0]
                unit.setdefault("payments", {})["authorizations"] = [
                    self._public_authorization(auth)
                ]
                order["links"] = [
                    {
                        "href": f"{self.base_url}/v2/checkout/orders/{order['id']}",
                        "rel": "self",
                        "method": "GET",
                    }
                ]
            return 204, None, {}

    # ------------------------------------------------------------------
    # Vault
    # ------------------------------------------------------------------
    def _create_setup_token(self, body: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            source = body.get("payment_source")
            if not isinstance(source, Mapping):
                raise MockAPIError(
                    422,
                    "UNPROCESSABLE_ENTITY",
                    "payment_source is required.",
                    issue="MISSING_REQUIRED_PARAMETER",
                    field="payment_source",
                )
            now = utc_now()
            setup_id = self._new_id("4G4")
            customer_id = self._new_customer_id()
            if "paypal" in source:
                paypal = source.get("paypal")
                if not isinstance(paypal, Mapping):
                    raise MockAPIError(
                        422,
                        "UNPROCESSABLE_ENTITY",
                        "payment_source.paypal must be an object.",
                        issue="INVALID_PARAMETER_VALUE",
                        field="payment_source.paypal",
                    )
                context = paypal.get("experience_context") or {}
                if not isinstance(context, Mapping):
                    context = {}
                for field_name in ("return_url", "cancel_url"):
                    if not context.get(field_name):
                        raise MockAPIError(
                            422,
                            "UNPROCESSABLE_ENTITY",
                            "A contingency flow requires return_url and cancel_url.",
                            issue="MISSING_REQUIRED_PARAMETER",
                            field=(
                                "payment_source.paypal.experience_context."
                                + field_name
                            ),
                        )
                setup: Dict[str, Any] = {
                    "id": setup_id,
                    "customer": {"id": customer_id},
                    "status": "PAYER_ACTION_REQUIRED",
                    "payment_source": {
                        "paypal": {
                            "description": paypal.get("description")
                            or "Save a PayPal wallet for future payments",
                            "usage_pattern": paypal.get("usage_pattern")
                            or "IMMEDIATE",
                            "usage_type": paypal.get("usage_type") or "MERCHANT",
                            "customer_type": paypal.get("customer_type")
                            or "CONSUMER",
                            "permit_multiple_payment_tokens": bool(
                                paypal.get("permit_multiple_payment_tokens", False)
                            ),
                            "email_address": MOCK_BUYER_EMAIL,
                            "payer_id": MOCK_PAYER_ID,
                        }
                    },
                    "create_time": now,
                    "update_time": now,
                    "_type": "paypal",
                    "_approved": False,
                }
                setup["links"] = [
                    {
                        "href": f"{self.base_url}/v3/vault/setup-tokens/{setup_id}",
                        "rel": "self",
                        "method": "GET",
                    },
                    {
                        "href": (
                            "https://sandbox.paypal.com/agreements/approve"
                            "?approval_session_id="
                            + setup_id
                        ),
                        "rel": "approve",
                        "method": "GET",
                    },
                ]
                self.setup_tokens[setup_id] = setup
                return self._setup_token_response(setup)

            if "card" in source:
                card = source.get("card")
                if not isinstance(card, Mapping):
                    raise MockAPIError(
                        422,
                        "UNPROCESSABLE_ENTITY",
                        "payment_source.card must be an object.",
                        issue="INVALID_PARAMETER_VALUE",
                        field="payment_source.card",
                    )
                card_info = self._validate_card(card)
                setup = {
                    "id": setup_id,
                    "customer": {"id": customer_id},
                    "status": "APPROVED",
                    "payment_source": {"card": card_info["public"]},
                    "create_time": now,
                    "update_time": now,
                    "_type": "card",
                    "_approved": True,
                    "_card": card_info["stored"],
                }
                setup["links"] = [
                    {
                        "href": f"{self.base_url}/v3/vault/setup-tokens/{setup_id}",
                        "rel": "self",
                        "method": "GET",
                    },
                    {
                        "href": f"{self.base_url}/v3/vault/payment-tokens",
                        "rel": "confirm",
                        "method": "POST",
                    },
                ]
                self.setup_tokens[setup_id] = setup
                return self._setup_token_response(setup)

            raise MockAPIError(
                422,
                "UNPROCESSABLE_ENTITY",
                "Only paypal and card setup tokens are supported by this mock.",
                issue="INVALID_PAYMENT_SOURCE",
                field="payment_source",
            )

    def _get_setup_token(self, setup_token_id: str) -> Dict[str, Any]:
        with self._lock:
            setup = self.setup_tokens.get(setup_token_id)
            if setup is None:
                raise MockAPIError(
                    404,
                    "RESOURCE_NOT_FOUND",
                    "The specified resource does not exist.",
                    issue="RESOURCE_NOT_FOUND",
                )
            return self._setup_token_response(setup)

    def _create_payment_token(self, body: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            source = body.get("payment_source")
            if not isinstance(source, Mapping):
                raise MockAPIError(
                    422,
                    "UNPROCESSABLE_ENTITY",
                    "payment_source is required.",
                    issue="MISSING_REQUIRED_PARAMETER",
                    field="payment_source",
                )

            if "token" in source:
                token = source.get("token")
                if not isinstance(token, Mapping):
                    raise MockAPIError(
                        422,
                        "UNPROCESSABLE_ENTITY",
                        "payment_source.token must be an object.",
                        issue="INVALID_PARAMETER_VALUE",
                        field="payment_source.token",
                    )
                token_id = str(token.get("id") or "")
                token_type = str(token.get("type") or "")
                if token_type != "SETUP_TOKEN":
                    raise MockAPIError(
                        422,
                        "UNPROCESSABLE_ENTITY",
                        "payment_source.token.type must be SETUP_TOKEN.",
                        issue="INVALID_PARAMETER_VALUE",
                        field="payment_source.token.type",
                    )
                setup = self.setup_tokens.get(token_id)
                if setup is None:
                    raise MockAPIError(
                        404,
                        "RESOURCE_NOT_FOUND",
                        "The setup token does not exist.",
                        issue="SETUP_TOKEN_NOT_FOUND",
                    )
                if setup["status"] == "VAULTED":
                    raise MockAPIError(
                        422,
                        "UNPROCESSABLE_ENTITY",
                        "The setup token has already been used to create a payment token.",
                        issue="SETUP_TOKEN_ALREADY_TOKENIZED",
                    )
                if setup["status"] != "APPROVED":
                    raise MockAPIError(
                        422,
                        "UNPROCESSABLE_ENTITY",
                        "The setup token has not been approved by the payer.",
                        issue="SETUP_TOKEN_NOT_APPROVED",
                        field="payment_source.token.id",
                    )
                return self._vault_from_setup(setup)

            if "card" in source:
                card = source.get("card")
                if not isinstance(card, Mapping):
                    raise MockAPIError(
                        422,
                        "UNPROCESSABLE_ENTITY",
                        "payment_source.card must be an object.",
                        issue="INVALID_PARAMETER_VALUE",
                        field="payment_source.card",
                    )
                card_info = self._validate_card(card)
                customer_id = self._new_customer_id()
                token_id = self._new_vault_id()
                token = {
                    "id": token_id,
                    "customer": {"id": customer_id},
                    "type": "card",
                    "source": card_info["stored"],
                    "create_time": utc_now(),
                }
                self.payment_tokens[token_id] = token
                return self._payment_token_response(token)

            raise MockAPIError(
                422,
                "UNPROCESSABLE_ENTITY",
                "Only a setup token or card source can be vaulted by this mock.",
                issue="INVALID_PAYMENT_SOURCE",
                field="payment_source",
            )

    def _vault_from_setup(self, setup: Dict[str, Any]) -> Dict[str, Any]:
        token_id = self._new_vault_id()
        if setup["_type"] == "paypal":
            source = {
                "email_address": MOCK_BUYER_EMAIL,
                "payer_id": MOCK_PAYER_ID,
                "name": {"given_name": "Sandbox", "surname": "Buyer"},
                "address": {"country_code": "US"},
            }
            token_type = "paypal"
        else:
            source = setup["payment_source"]["card"]
            token_type = "card"
        token = {
            "id": token_id,
            "customer": {"id": setup["customer"]["id"]},
            "type": token_type,
            "source": source,
            "create_time": utc_now(),
        }
        self.payment_tokens[token_id] = token
        setup["status"] = "VAULTED"
        setup["update_time"] = utc_now()
        return self._payment_token_response(token)

    def _get_payment_token(self, payment_token_id: str) -> Dict[str, Any]:
        with self._lock:
            token = self.payment_tokens.get(payment_token_id)
            if token is None:
                raise MockAPIError(
                    404,
                    "RESOURCE_NOT_FOUND",
                    "The specified resource does not exist.",
                    issue="RESOURCE_NOT_FOUND",
                )
            return self._payment_token_response(token)

    def _list_payment_tokens(
        self, query: Mapping[str, List[str]]
    ) -> Dict[str, Any]:
        with self._lock:
            customer_ids = query.get("customer_id") or []
            customer_id = customer_ids[0] if customer_ids else ""
            if not customer_id:
                raise MockAPIError(
                    400,
                    "INVALID_REQUEST",
                    "customer_id is required.",
                    issue="MISSING_REQUIRED_PARAMETER",
                    field="customer_id",
                )
            page_size = self._query_int(query, "page_size", 20, minimum=1, maximum=100)
            page = self._query_int(query, "page", 1, minimum=1)
            tokens = [
                self._payment_token_response(token)
                for token in self.payment_tokens.values()
                if token["customer"]["id"] == customer_id
            ]
            tokens.sort(key=lambda item: item["id"])
            start = (page - 1) * page_size
            page_tokens = tokens[start : start + page_size]
            total_pages = max(1, (len(tokens) + page_size - 1) // page_size)
            return {
                "payment_tokens": page_tokens,
                "total_items": len(tokens),
                "total_pages": total_pages,
                "links": [],
            }

    def _delete_payment_token(
        self, payment_token_id: str
    ) -> Tuple[int, None, Dict[str, str]]:
        with self._lock:
            if payment_token_id not in self.payment_tokens:
                raise MockAPIError(
                    404,
                    "RESOURCE_NOT_FOUND",
                    "The specified resource does not exist.",
                    issue="RESOURCE_NOT_FOUND",
                )
            del self.payment_tokens[payment_token_id]
            return 204, None, {}

    # ------------------------------------------------------------------
    # Response shaping
    # ------------------------------------------------------------------
    def _public_order(self, order: Mapping[str, Any]) -> Dict[str, Any]:
        public = json.loads(json.dumps({k: v for k, v in order.items()}))
        # Keep the mock history, remove private keys if any are added later.
        return public

    def _public_authorization(self, auth: Mapping[str, Any]) -> Dict[str, Any]:
        now = utc_now()
        auth_id = auth["id"]
        return {
            "id": auth_id,
            "status": auth["status"],
            "amount": dict(auth["amount"]),
            "seller_protection": dict(auth["seller_protection"]),
            "expiration_time": auth["expiration_time"],
            "create_time": auth["create_time"],
            "update_time": auth.get("update_time", now),
            "links": [
                {
                    "href": f"{self.base_url}/v2/payments/authorizations/{auth_id}",
                    "rel": "self",
                    "method": "GET",
                },
                {
                    "href": f"{self.base_url}/v2/payments/authorizations/{auth_id}/capture",
                    "rel": "capture",
                    "method": "POST",
                },
                {
                    "href": f"{self.base_url}/v2/payments/authorizations/{auth_id}/void",
                    "rel": "void",
                    "method": "POST",
                },
                {
                    "href": f"{self.base_url}/v2/payments/authorizations/{auth_id}/reauthorize",
                    "rel": "reauthorize",
                    "method": "POST",
                },
                {
                    "href": f"{self.base_url}/v2/checkout/orders/{auth['_order_id']}",
                    "rel": "up",
                    "method": "GET",
                },
            ],
        }

    def _make_capture(
        self, capture_id: str, amount: Mapping[str, Any], now: str
    ) -> Dict[str, Any]:
        gross = Decimal(str(amount["value"]))
        fee = min(Decimal("0.30"), gross * Decimal("0.029"))
        net = gross - fee
        return {
            "id": capture_id,
            "status": "COMPLETED",
            "amount": dict(amount),
            "final_capture": True,
            "seller_protection": {
                "status": "ELIGIBLE",
                "dispute_categories": [
                    "ITEM_NOT_RECEIVED",
                    "UNAUTHORIZED_TRANSACTION",
                ],
            },
            "seller_receivable_breakdown": {
                "gross_amount": dict(amount),
                "paypal_fee": {
                    "currency_code": amount["currency_code"],
                    "value": _format_money(fee),
                },
                "net_amount": {
                    "currency_code": amount["currency_code"],
                    "value": _format_money(net),
                },
            },
            "create_time": now,
            "update_time": now,
        }

    def _public_capture(self, capture: Mapping[str, Any]) -> Dict[str, Any]:
        capture_id = capture["id"]
        public = json.loads(json.dumps(capture))
        public["links"] = [
            {
                "href": f"{self.base_url}/v2/payments/captures/{capture_id}",
                "rel": "self",
                "method": "GET",
            },
            {
                "href": f"{self.base_url}/v2/payments/captures/{capture_id}/refund",
                "rel": "refund",
                "method": "POST",
            },
        ]
        return public

    def _setup_token_response(self, setup: Mapping[str, Any]) -> Dict[str, Any]:
        public = {
            "id": setup["id"],
            "customer": dict(setup["customer"]),
            "status": setup["status"],
            "payment_source": json.loads(json.dumps(setup["payment_source"])),
            "create_time": setup["create_time"],
            "update_time": setup.get("update_time", utc_now()),
            "links": json.loads(json.dumps(setup.get("links") or [])),
        }
        if setup["status"] == "APPROVED":
            if not any(link["rel"] == "confirm" for link in public["links"]):
                public["links"].append(
                    {
                        "href": f"{self.base_url}/v3/vault/payment-tokens",
                        "rel": "confirm",
                        "method": "POST",
                    }
                )
        return public

    def _payment_token_response(self, token: Mapping[str, Any]) -> Dict[str, Any]:
        token_id = token["id"]
        if token["type"] == "paypal":
            source = {"paypal": json.loads(json.dumps(token["source"]))}
        else:
            source = {"card": json.loads(json.dumps(token["source"]))}
        return {
            "id": token_id,
            "customer": dict(token["customer"]),
            "payment_source": source,
            "links": [
                {
                    "href": f"{self.base_url}/v3/vault/payment-tokens/{token_id}",
                    "rel": "self",
                    "method": "GET",
                },
                {
                    "href": f"{self.base_url}/v3/vault/payment-tokens/{token_id}",
                    "rel": "delete",
                    "method": "DELETE",
                },
            ],
        }

    # ------------------------------------------------------------------
    # Validation / ids
    # ------------------------------------------------------------------
    def _validate_units(
        self, units: List[Any]
    ) -> Tuple[Decimal, str]:
        currencies = set()
        total = Decimal("0.00")
        for unit in units:
            if not isinstance(unit, Mapping):
                raise MockAPIError(
                    422,
                    "UNPROCESSABLE_ENTITY",
                    "Each purchase_unit must be an object.",
                    issue="INVALID_PARAMETER_VALUE",
                    field="purchase_units",
                )
            amount = unit.get("amount")
            if not isinstance(amount, Mapping):
                raise MockAPIError(
                    422,
                    "UNPROCESSABLE_ENTITY",
                    "Each purchase_unit requires an amount.",
                    issue="MISSING_REQUIRED_PARAMETER",
                    field="purchase_units.amount",
                )
            currency = str(amount.get("currency_code") or "").upper()
            if len(currency) != 3 or not currency.isalpha():
                raise MockAPIError(
                    422,
                    "UNPROCESSABLE_ENTITY",
                    "currency_code must be a three-letter code.",
                    issue="INVALID_CURRENCY_CODE",
                    field="purchase_units.amount.currency_code",
                )
            currencies.add(currency)
            total += _money(amount.get("value"), "purchase_units.amount.value")
        if len(currencies) != 1:
            raise MockAPIError(
                422,
                "UNPROCESSABLE_ENTITY",
                "All purchase units must use the same currency.",
                issue="CURRENCY_MISMATCH",
                field="purchase_units.amount.currency_code",
            )
        return total, currencies.pop()

    def _validate_card(self, card: Mapping[str, Any]) -> Dict[str, Any]:
        number = str(card.get("number") or "").replace(" ", "")
        if not number.isdigit() or not (13 <= len(number) <= 19):
            raise MockAPIError(
                422,
                "UNPROCESSABLE_ENTITY",
                "Card number must be 13 to 19 digits.",
                issue="INVALID_PARAMETER_VALUE",
                field="payment_source.card.number",
            )
        expiry = str(card.get("expiry") or "")
        if not _valid_expiry(expiry):
            raise MockAPIError(
                422,
                "UNPROCESSABLE_ENTITY",
                "Card expiry must be YYYY-MM and in the future.",
                issue="INVALID_EXPIRY_DATE",
                field="payment_source.card.expiry",
            )
        name = str(card.get("name") or "Sandbox Buyer")
        billing = card.get("billing_address")
        if not isinstance(billing, Mapping) or not billing.get("country_code"):
            billing = {"country_code": "US"}
        brand = _card_brand(number)
        stored = {
            "last_digits": number[-4:],
            "expiry": expiry,
            "name": name,
            "brand": brand,
            "billing_address": {
                "country_code": str(billing.get("country_code") or "US").upper()
            },
        }
        if billing.get("postal_code"):
            stored["billing_address"]["postal_code"] = str(billing["postal_code"])
        public = dict(stored)
        return {"stored": stored, "public": public}

    def _new_id(self, prefix: str) -> str:
        self._seq += 1
        return prefix + f"{self._seq:014d}"

    def _new_vault_id(self) -> str:
        self._seq += 1
        return "vlt" + f"{self._seq:09d}"

    def _new_customer_id(self) -> str:
        self._customer_seq += 1
        return "customer_" + f"{self._customer_seq:010d}"

    @staticmethod
    def _query_int(
        query: Mapping[str, List[str]],
        name: str,
        default: int,
        *,
        minimum: int = 0,
        maximum: int = 10_000,
    ) -> int:
        values = query.get(name) or []
        if not values:
            return default
        try:
            value = int(values[0])
        except (TypeError, ValueError) as exc:
            raise MockAPIError(
                400,
                "INVALID_REQUEST",
                f"{name} must be an integer.",
                issue="INVALID_PARAMETER_VALUE",
                field=name,
            ) from exc
        if value < minimum:
            value = minimum
        if value > maximum:
            value = maximum
        return value


def _valid_expiry(expiry: str) -> bool:
    if len(expiry) != 7 or expiry[4] != "-":
        return False
    try:
        year = int(expiry[:4])
        month = int(expiry[5:])
    except ValueError:
        return False
    if not (1 <= month <= 12):
        return False
    now = datetime.now(timezone.utc)
    return (year, month) >= (now.year, now.month)


def _card_brand(number: str) -> str:
    if number.startswith("4"):
        return "VISA"
    if number.startswith(("51", "52", "53", "54", "55")) or number.startswith(
        ("2221", "2222", "2223", "2224", "2225", "2226", "2227", "2228", "2229",
         "223", "224", "225", "226", "227", "228", "229", "23", "24", "25",
         "26", "27")
    ):
        return "MASTERCARD"
    if number.startswith(("34", "37")):
        return "AMEX"
    if number.startswith(("6011", "65")):
        return "DISCOVER"
    return "UNKNOWN"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the Veto mock PayPal API as a standalone server."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument(
        "--latency", type=float, default=0.0, help="optional artificial latency"
    )
    args = parser.parse_args(argv)

    mock = MockPayPal(host=args.host, port=args.port, latency=args.latency).start()
    print(f"mock PayPal base URL : {mock.base_url}")
    print(f"client_id            : {MOCK_CLIENT_ID}")
    print(f"client_secret        : {MOCK_CLIENT_SECRET}")
    print()
    print("Point the client at it with:")
    print(f"  export PAYPAL_BASE={mock.base_url}")
    print(f"  export PAYPAL_CLIENT_ID={MOCK_CLIENT_ID}")
    print(f"  export PAYPAL_CLIENT_SECRET={MOCK_CLIENT_SECRET}")
    print()
    print("To approve a setup token manually (mock only):")
    print(
        "  curl -X POST "
        f"{mock.base_url}/__mock/setup-tokens/<SETUP_TOKEN_ID>/approve"
    )
    print(f"Inspect state: curl {mock.base_url}/__mock/state")
    print("Ctrl-C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nstopping mock PayPal")
    finally:
        mock.stop()
    return 0


__all__ = [
    "MockPayPal",
    "MockAPIError",
    "MOCK_CLIENT_ID",
    "MOCK_CLIENT_SECRET",
    "MOCK_ACCESS_TOKEN",
    "MOCK_MERCHANT_ID",
]


if __name__ == "__main__":
    raise SystemExit(main())
