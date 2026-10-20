"""Minimal PayPal REST client for the Veto capability spike.

Standard library only.  The client deliberately implements a small, explicit
subset of the PayPal REST API:

  * OAuth2 client-credentials token, cached until shortly before expiry
  * Orders v2: create, get, authorize, capture (order) and capture an
    authorization through Payments v2
  * Payments v2: get authorization, void authorization
  * Payment Method Tokens v3: setup tokens, payment tokens, list/delete

Every POST requires an idempotency key and sends a deterministic
``PayPal-Request-Id`` derived from it.  Errors are normalised into
:class:`PayPalError` so callers can print the HTTP status, PayPal error
``name``, the fine-grained ``details[].issue`` values and the ``debug_id``.

Nothing here knows about the mock.  ``PAYPAL_BASE`` decides where requests go.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


SANDBOX_BASE = "https://api-m.sandbox.paypal.com"
PRODUCTION_BASE = "https://api-m.paypal.com"

# PayPal recommends UUID-sized values; its docs mention a 38 single-byte
# character limit for PayPal-Request-Id.  "veto-" + 32 hex chars = 37.
_REQUEST_ID_PREFIX = "veto-"
_MAX_REQUEST_ID_LEN = 38


def derive_request_id(idempotency_key: str) -> str:
    """Return a deterministic, URL/header-safe PayPal-Request-Id.

    The idempotency key is the semantic key (in Veto it should be the consent
    nonce).  Hashing it gives a fixed-width header value that never leaks the
    consent nonce to PayPal or into logs.  Retries with the same key produce
    the same header, which is what makes a retry safe.
    """

    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ValueError("an idempotency key is required for every POST")
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:32]
    request_id = f"{_REQUEST_ID_PREFIX}{digest}"
    if len(request_id) > _MAX_REQUEST_ID_LEN:  # defensive; currently 37
        raise ValueError("derived PayPal-Request-Id exceeds 38 characters")
    return request_id


def _as_lower_headers(headers: Mapping[str, Any]) -> Dict[str, str]:
    return {str(k).lower(): str(v) for k, v in headers.items()}


def _short(value: Any, limit: int = 400) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    return text if len(text) <= limit else text[: limit - 1] + "…"


class PayPalError(Exception):
    """A normalised PayPal API or transport failure.

    Attributes
    ----------
    status_code:
        HTTP status, or ``None`` for a network/transport failure.
    name:
        PayPal error name (for example ``UNPROCESSABLE_ENTITY``), or a
        synthetic name such as ``TRANSPORT_ERROR``.
    message:
        Human-readable message from PayPal, or a transport description.
    debug_id:
        PayPal correlation id.  The first thing to quote to PayPal support.
    details:
        Raw ``details`` array from PayPal.
    issues:
        Convenience list of ``details[].issue`` values.
    request_id:
        The derived ``PayPal-Request-Id`` that was sent, when there was one.
    method, path, body:
        The failing request metadata and parsed response body.  Never print
        ``body`` blindly in a UI: it can contain payer information.
    """

    def __init__(
        self,
        status_code: Optional[int],
        name: str,
        message: str,
        *,
        debug_id: Optional[str] = None,
        details: Optional[Sequence[Mapping[str, Any]]] = None,
        request_id: Optional[str] = None,
        method: Optional[str] = None,
        path: Optional[str] = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.name = name or "UNKNOWN_ERROR"
        self.message = message or ""
        self.debug_id = debug_id
        self.details: List[Dict[str, Any]] = [
            dict(item) for item in (details or []) if isinstance(item, Mapping)
        ]
        self.request_id = request_id
        self.method = method
        self.path = path
        self.body = body

    @property
    def issues(self) -> List[str]:
        found: List[str] = []
        for detail in self.details:
            issue = detail.get("issue")
            if issue and issue not in found:
                found.append(str(issue))
        return found

    @property
    def detail_summary(self) -> str:
        parts: List[str] = []
        for detail in self.details:
            issue = detail.get("issue")
            field = detail.get("field")
            description = detail.get("description")
            if issue:
                text = str(issue)
                if field:
                    text += f" @ {field}"
                if description:
                    text += f" — {description}"
                parts.append(text)
        return "; ".join(parts)

    def __str__(self) -> str:
        bits = [f"{self.status_code if self.status_code is not None else 'no HTTP'}"
                f" {self.name}: {self.message}"]
        if self.issues:
            bits.append("issues=" + ",".join(self.issues))
        if self.debug_id:
            bits.append(f"debug_id={self.debug_id}")
        if self.request_id:
            bits.append(f"request_id={self.request_id}")
        return " | ".join(bits)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status_code": self.status_code,
            "name": self.name,
            "message": self.message,
            "debug_id": self.debug_id,
            "issues": self.issues,
            "details": self.details,
            "request_id": self.request_id,
            "method": self.method,
            "path": self.path,
        }


class PayPalTransportError(PayPalError):
    """A DNS, TLS, timeout or connection failure (no HTTP status)."""

    def __init__(
        self,
        message: str,
        *,
        method: Optional[str] = None,
        path: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> None:
        super().__init__(
            None,
            "TRANSPORT_ERROR",
            message,
            debug_id=None,
            details=[],
            request_id=request_id,
            method=method,
            path=path,
        )


class PayPalClient:
    """Small, synchronous PayPal REST client.

    Parameters
    ----------
    client_id, client_secret:
        REST app credentials.
    base_url:
        Defaults to the sandbox.  Use ``PAYPAL_BASE`` from the environment.
    merchant_id:
        Optional seller merchant/payer id.  It is stored for display and
        payee checks.  It is *not* sent as a ``PayPal-Auth-Assertion``:
        that header is for partners acting on behalf of a merchant and is not
        needed by a direct merchant integration.
    use_proxy:
        Set ``False`` for the local mock so ambient HTTP(S)_PROXY settings do
        not intercept loopback traffic.  Live mode keeps urllib defaults.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        base_url: str = SANDBOX_BASE,
        *,
        merchant_id: Optional[str] = None,
        timeout: float = 30.0,
        use_proxy: bool = True,
        user_agent: str = "Veto-PayPal-Spike/1.0 (+python-stdlib)",
    ) -> None:
        if not client_id or not client_secret:
            raise ValueError("client_id and client_secret are required")
        if not base_url or not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")

        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/")
        self.merchant_id = merchant_id or None
        self.timeout = float(timeout)
        self.user_agent = user_agent

        if use_proxy:
            self._opener = urllib.request.build_opener()
        else:
            self._opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({})
            )

        self._token_lock = threading.Lock()
        self._token: Optional[str] = None
        self._token_expires_at = 0.0
        self._token_issued_at = 0.0
        self._token_expires_in = 0
        self._token_fetches = 0

        # Diagnostics for the gate.  Guarded by no lock deliberately: the gate
        # is single-threaded and these are best-effort observations.
        self.last_request_id: Optional[str] = None
        self.last_status: Optional[int] = None
        self.last_response_headers: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Environment helpers
    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls, **kwargs: Any) -> "PayPalClient":
        client_id = (os.environ.get("PAYPAL_CLIENT_ID") or "").strip()
        client_secret = (os.environ.get("PAYPAL_CLIENT_SECRET") or "").strip()
        missing = [
            name
            for name, value in (
                ("PAYPAL_CLIENT_ID", client_id),
                ("PAYPAL_CLIENT_SECRET", client_secret),
            )
            if not value
        ]
        if missing:
            raise ValueError("missing environment variable(s): " + ", ".join(missing))
        base = (os.environ.get("PAYPAL_BASE") or SANDBOX_BASE).strip()
        merchant = (os.environ.get("PAYPAL_MERCHANT_ID") or "").strip() or None
        kwargs.setdefault("merchant_id", merchant)
        return cls(client_id, client_secret, base, **kwargs)

    @property
    def is_sandbox(self) -> bool:
        return "sandbox" in self.base_url.lower()

    @property
    def is_production(self) -> bool:
        return self.base_url.lower().rstrip("/") == PRODUCTION_BASE

    @property
    def token_fetch_count(self) -> int:
        return self._token_fetches

    def token_cache_info(self) -> Dict[str, Any]:
        now = time.monotonic()
        return {
            "fetches": self._token_fetches,
            "cached": bool(self._token and now < self._token_expires_at),
            "expires_in": max(0, int(self._token_expires_at - now)),
            "expires_in_reported": self._token_expires_in,
        }

    def invalidate_token(self) -> None:
        with self._token_lock:
            self._token = None
            self._token_expires_at = 0.0

    # ------------------------------------------------------------------
    # OAuth2
    # ------------------------------------------------------------------
    def access_token(self) -> str:
        """Return a cached bearer token, fetching one when needed."""

        now = time.monotonic()
        with self._token_lock:
            if self._token and now < self._token_expires_at:
                return self._token
            return self._fetch_token_locked()

    def _fetch_token_locked(self) -> str:
        basic = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode("utf-8")
        ).decode("ascii")
        headers = {
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": self.user_agent,
            # The token endpoint does not take a caller idempotency key.  Send
            # a deterministic one anyway so "every POST has a request id"
            # remains literally true; PayPal ignores it on this endpoint.
            "PayPal-Request-Id": derive_request_id(f"oauth:{self.client_id}"),
        }
        data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
        status, response_headers, raw = self._http(
            "POST",
            f"{self.base_url}/v1/oauth2/token",
            headers,
            data,
            path="/v1/oauth2/token",
            request_id=headers["PayPal-Request-Id"],
        )
        if not 200 <= status < 300:
            raise self._error_from_response(
                status,
                response_headers,
                raw,
                method="POST",
                path="/v1/oauth2/token",
                request_id=headers["PayPal-Request-Id"],
            )

        payload = self._parse_json(raw)
        if not isinstance(payload, Mapping) or not payload.get("access_token"):
            raise PayPalError(
                status,
                "INVALID_OAUTH_RESPONSE",
                "PayPal token endpoint returned no access_token",
                debug_id=response_headers.get("paypal-debug-id"),
                method="POST",
                path="/v1/oauth2/token",
                request_id=headers["PayPal-Request-Id"],
            )

        expires_in = int(payload.get("expires_in") or 32400)
        # Refresh a little early.  For very short-lived tokens, use at most 10%
        # as the skew so we do not throw away the whole token immediately.
        skew = min(60, max(5, int(expires_in * 0.1)))
        now = time.monotonic()
        self._token = str(payload["access_token"])
        self._token_issued_at = now
        self._token_expires_at = now + max(1, expires_in - skew)
        self._token_expires_in = expires_in
        self._token_fetches += 1
        return self._token

    # ------------------------------------------------------------------
    # Orders v2
    # ------------------------------------------------------------------
    def create_order(
        self,
        *,
        intent: str,
        purchase_units: Sequence[Mapping[str, Any]],
        payment_source: Optional[Mapping[str, Any]] = None,
        application_context: Optional[Mapping[str, Any]] = None,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        """Create an order.  ``intent`` is ``AUTHORIZE`` or ``CAPTURE``."""

        normalized_intent = str(intent).upper()
        if normalized_intent not in ("AUTHORIZE", "CAPTURE"):
            raise ValueError("intent must be AUTHORIZE or CAPTURE")
        if not purchase_units:
            raise ValueError("purchase_units must contain at least one unit")
        body: Dict[str, Any] = {
            "intent": normalized_intent,
            "purchase_units": list(purchase_units),
        }
        if payment_source is not None:
            body["payment_source"] = dict(payment_source)
        if application_context is not None:
            body["application_context"] = dict(application_context)
        return self._request(
            "POST", "/v2/checkout/orders", body=body, idempotency_key=idempotency_key
        )

    def get_order(self, order_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/v2/checkout/orders/{self._id(order_id)}")

    def authorize_order(
        self,
        order_id: str,
        *,
        idempotency_key: str,
        payment_source: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {}
        if payment_source is not None:
            body["payment_source"] = dict(payment_source)
        return self._request(
            "POST",
            f"/v2/checkout/orders/{self._id(order_id)}/authorize",
            body=body,
            idempotency_key=idempotency_key,
        )

    def capture_order(
        self,
        order_id: str,
        *,
        idempotency_key: str,
        payment_source: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Capture an order created with ``intent=CAPTURE``.

        This is *not* the correct call after ``authorize``.  For an
        authorization, use :meth:`capture_authorization`; PayPal returns
        ``ACTION_DOES_NOT_MATCH_INTENT`` if this endpoint is used on an
        ``AUTHORIZE`` intent order.
        """

        body: Dict[str, Any] = {}
        if payment_source is not None:
            body["payment_source"] = dict(payment_source)
        return self._request(
            "POST",
            f"/v2/checkout/orders/{self._id(order_id)}/capture",
            body=body,
            idempotency_key=idempotency_key,
        )

    # ------------------------------------------------------------------
    # Payments v2
    # ------------------------------------------------------------------
    def get_authorization(self, authorization_id: str) -> Dict[str, Any]:
        return self._request(
            "GET", f"/v2/payments/authorizations/{self._id(authorization_id)}"
        )

    def capture_authorization(
        self,
        authorization_id: str,
        *,
        idempotency_key: str,
        amount: Optional[Mapping[str, Any]] = None,
        final_capture: bool = True,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"final_capture": bool(final_capture)}
        if amount is not None:
            body["amount"] = dict(amount)
        return self._request(
            "POST",
            f"/v2/payments/authorizations/{self._id(authorization_id)}/capture",
            body=body,
            idempotency_key=idempotency_key,
        )

    def void_authorization(
        self, authorization_id: str, *, idempotency_key: str
    ) -> Optional[Dict[str, Any]]:
        return self._request(
            "POST",
            f"/v2/payments/authorizations/{self._id(authorization_id)}/void",
            body={},
            idempotency_key=idempotency_key,
        )

    # ------------------------------------------------------------------
    # Payment Method Tokens v3
    # ------------------------------------------------------------------
    def create_setup_token(
        self,
        *,
        payment_source: Mapping[str, Any],
        idempotency_key: str,
        customer_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not payment_source:
            raise ValueError("payment_source is required")
        body: Dict[str, Any] = {"payment_source": dict(payment_source)}
        if customer_id:
            body["customer"] = {"id": customer_id}
        return self._request(
            "POST", "/v3/vault/setup-tokens", body=body, idempotency_key=idempotency_key
        )

    def get_setup_token(self, setup_token_id: str) -> Dict[str, Any]:
        return self._request(
            "GET", f"/v3/vault/setup-tokens/{self._id(setup_token_id)}"
        )

    def create_payment_token(
        self,
        *,
        payment_source: Mapping[str, Any],
        idempotency_key: str,
    ) -> Dict[str, Any]:
        if not payment_source:
            raise ValueError("payment_source is required")
        return self._request(
            "POST",
            "/v3/vault/payment-tokens",
            body={"payment_source": dict(payment_source)},
            idempotency_key=idempotency_key,
        )

    def get_payment_token(self, payment_token_id: str) -> Dict[str, Any]:
        return self._request(
            "GET", f"/v3/vault/payment-tokens/{self._id(payment_token_id)}"
        )

    def list_payment_tokens(
        self,
        *,
        customer_id: str,
        page_size: int = 20,
        page: int = 1,
        total_required: bool = False,
    ) -> Dict[str, Any]:
        if not customer_id:
            raise ValueError("customer_id is required to list payment tokens")
        return self._request(
            "GET",
            "/v3/vault/payment-tokens",
            query={
                "customer_id": customer_id,
                "page_size": str(max(1, int(page_size))),
                "page": str(max(1, int(page))),
                "total_required": "true" if total_required else "false",
            },
        )

    def delete_payment_token(
        self, payment_token_id: str, *, idempotency_key: Optional[str] = None
    ) -> None:
        return self._request(
            "DELETE",
            f"/v3/vault/payment-tokens/{self._id(payment_token_id)}",
            idempotency_key=idempotency_key,
        )

    # ------------------------------------------------------------------
    # Helpers shared by gate / application code
    # ------------------------------------------------------------------
    @staticmethod
    def find_link(payload: Optional[Mapping[str, Any]], rel: str) -> Optional[str]:
        if not isinstance(payload, Mapping):
            return None
        for link in payload.get("links") or []:
            if isinstance(link, Mapping) and link.get("rel") == rel:
                href = link.get("href")
                return str(href) if href else None
        return None

    @staticmethod
    def first_authorization(order: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        for unit in order.get("purchase_units") or []:
            payments = unit.get("payments") or {}
            for auth in payments.get("authorizations") or []:
                if isinstance(auth, Mapping):
                    return auth
        # Some PayPal responses (or the mock's authorize response) may return
        # the authorization object directly.
        if order.get("id") and str(order.get("status", "")).upper() in (
            "CREATED",
            "CAPTURED",
            "VOIDED",
            "PENDING",
        ) and not order.get("purchase_units"):
            return order
        return None

    @staticmethod
    def first_capture(order: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        for unit in order.get("purchase_units") or []:
            payments = unit.get("payments") or {}
            for capture in payments.get("captures") or []:
                if isinstance(capture, Mapping):
                    return capture
        return None

    @staticmethod
    def _id(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("resource id must not be empty")
        return urllib.parse.quote(text, safe="")

    # ------------------------------------------------------------------
    # HTTP plumbing
    # ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Mapping[str, Any]] = None,
        body: Optional[Mapping[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        _retried_auth: bool = False,
    ) -> Any:
        method = method.upper()
        url = f"{self.base_url}{path}"
        if query:
            encoded = urllib.parse.urlencode(
                {k: v for k, v in query.items() if v is not None}
            )
            if encoded:
                url = f"{url}?{encoded}"

        headers = {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }
        request_id: Optional[str] = None
        if method == "POST":
            if not idempotency_key:
                raise ValueError(f"idempotency_key is required for POST {path}")
            request_id = derive_request_id(idempotency_key)
            headers["PayPal-Request-Id"] = request_id
        elif method == "DELETE" and idempotency_key:
            request_id = derive_request_id(idempotency_key)
            headers["PayPal-Request-Id"] = request_id

        data: Optional[bytes] = None
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"

        if path != "/v1/oauth2/token":
            headers["Authorization"] = f"Bearer {self.access_token()}"

        try:
            status, response_headers, raw = self._http(
                method, url, headers, data, path=path, request_id=request_id
            )
        except PayPalTransportError:
            raise

        self.last_status = status
        self.last_response_headers = response_headers
        self.last_request_id = request_id

        if status == 401 and not _retried_auth and path != "/v1/oauth2/token":
            # The cached token may have been revoked or expired early.  One
            # retry with a fresh token is safe because the Request-Id is
            # stable.  Do not loop.
            self.invalidate_token()
            return self._request(
                method,
                path,
                query=query,
                body=body,
                idempotency_key=idempotency_key,
                _retried_auth=True,
            )

        if not 200 <= status < 300:
            raise self._error_from_response(
                status,
                response_headers,
                raw,
                method=method,
                path=path,
                request_id=request_id,
            )

        if status == 204 or not raw:
            return None
        payload = self._parse_json(raw)
        if payload is None:
            raise PayPalError(
                status,
                "INVALID_JSON_RESPONSE",
                f"PayPal returned a non-JSON success response ({len(raw)} bytes)",
                debug_id=response_headers.get("paypal-debug-id"),
                method=method,
                path=path,
                request_id=request_id,
                body=_short(raw),
            )
        return payload

    def _http(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        data: Optional[bytes],
        *,
        path: str,
        request_id: Optional[str],
    ) -> Tuple[int, Dict[str, str], bytes]:
        request = urllib.request.Request(
            url, data=data, headers=dict(headers), method=method
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
                return (
                    int(response.status),
                    _as_lower_headers(response.headers),
                    raw,
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            return (
                int(exc.code),
                _as_lower_headers(exc.headers),
                raw,
            )
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
            reason = getattr(exc, "reason", exc)
            raise PayPalTransportError(
                f"{type(exc).__name__}: {reason}",
                method=method,
                path=path,
                request_id=request_id,
            ) from exc
        except OSError as exc:
            raise PayPalTransportError(
                f"{type(exc).__name__}: {exc}",
                method=method,
                path=path,
                request_id=request_id,
            ) from exc

    @staticmethod
    def _parse_json(raw: bytes) -> Any:
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _error_from_response(
        self,
        status: int,
        response_headers: Mapping[str, str],
        raw: bytes,
        *,
        method: str,
        path: str,
        request_id: Optional[str],
    ) -> PayPalError:
        payload = self._parse_json(raw)
        debug_id = response_headers.get("paypal-debug-id")
        if isinstance(payload, Mapping):
            name = str(payload.get("name") or f"HTTP_{status}")
            message = str(payload.get("message") or f"PayPal returned HTTP {status}")
            debug_id = str(payload.get("debug_id") or debug_id or "") or None
            details = payload.get("details")
            if not isinstance(details, list):
                details = []
            return PayPalError(
                status,
                name,
                message,
                debug_id=debug_id,
                details=details,
                request_id=request_id,
                method=method,
                path=path,
                body=payload,
            )
        text = raw.decode("utf-8", errors="replace").strip()
        return PayPalError(
            status,
            f"HTTP_{status}",
            text[:500] if text else f"PayPal returned HTTP {status}",
            debug_id=debug_id,
            request_id=request_id,
            method=method,
            path=path,
            body=text[:2000],
        )


__all__ = [
    "PayPalClient",
    "PayPalError",
    "PayPalTransportError",
    "SANDBOX_BASE",
    "PRODUCTION_BASE",
    "derive_request_id",
]
