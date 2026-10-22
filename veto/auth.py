"""Veto auth: passwords, opaque sessions, agent API keys, cookies.

Passwords are hashed with scrypt (stdlib ``hashlib``). Sessions and API keys
are stored only as SHA-256 digests; the plaintext token is returned once and
never persisted. Every function takes a ``Store`` so this module stays free of
database details (and easy to unit test with an in-memory database).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from veto.store import Store, parse_iso

COOKIE_NAME = "veto_session"
SESSION_TTL_DAYS = 7
SESSION_TTL_SECONDS = SESSION_TTL_DAYS * 24 * 60 * 60  # 604800
SESSION_TOUCH_SECONDS = 60 * 60  # slide the expiry at most once an hour
VALID_ROLES = ("admin", "approver", "viewer")

# scrypt parameters. 16 MiB of memory per hash; cheap enough for a demo login,
# expensive enough to make an offline dictionary attack pay for itself.
_SCRYPT_N = 16384
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_SALT_BYTES = 16
_API_KEY_PREFIX = "vkey_"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# passwords
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    """Return ``scrypt$n$r$p$<salt_hex>$<hash_hex>``; 32-byte digest, 16-byte salt."""
    salt = secrets.token_bytes(_SCRYPT_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return (
        f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}"
        f"${salt.hex()}${digest.hex()}"
    )


def verify_password(stored: str, password: str) -> bool:
    """Constant-time check. A missing/corrupt stored hash is a plain ``False``."""
    try:
        if not stored or not isinstance(stored, str) or not isinstance(password, str):
            return False
        algorithm, n_raw, r_raw, p_raw, salt_hex, hash_hex = stored.split("$")
        if algorithm != "scrypt":
            return False
        n, r, p = int(n_raw), int(r_raw), int(p_raw)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        if not salt or not expected:
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected)
        )
        return hmac.compare_digest(actual, expected)
    except Exception:  # noqa: BLE001 - malformed input must never raise
        return False


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------
def issue_session(store: Store, user: dict) -> str:
    """Create a database session for ``user`` and return the opaque token."""
    workspace_id = user.get("workspace_id")
    if not workspace_id:
        raise ValueError("user has no workspace")
    token = secrets.token_hex(32)
    expires_at = (_now() + timedelta(days=SESSION_TTL_DAYS)).isoformat(timespec="seconds")
    store.create_session(user["id"], workspace_id, _token_hash(token), expires_at)
    return token


def resolve_session(store: Store, token: str | None) -> dict | None:
    """Resolve a cookie token to ``{session_id, user, workspace}`` or ``None``.

    Expired sessions are deleted on sight; live ones slide their expiry forward
    (and stamp ``last_seen_at``) at most once an hour.
    """
    if not token or not isinstance(token, str):
        return None
    session = store.session_by_token_hash(_token_hash(token))
    if session is None:
        return None
    now = _now()
    expires = parse_iso(session.get("expires_at"))
    if expires is None or expires <= now:
        store.delete_session(session["id"])
        return None
    user = store.user(session["user_id"])
    workspace = store.workspace(session["workspace_id"])
    if user is None or workspace is None:
        store.delete_session(session["id"])
        return None

    last_seen = parse_iso(session.get("last_seen_at"))
    if last_seen is None or (now - last_seen).total_seconds() >= SESSION_TOUCH_SECONDS:
        store.touch_session(
            session["id"],
            (now + timedelta(days=SESSION_TTL_DAYS)).isoformat(timespec="seconds"),
        )
    return {"session_id": session["id"], "user": user, "workspace": workspace}


def revoke_session(store: Store, token: str | None) -> bool:
    """Delete the session for this token. Unknown tokens are not an error."""
    if not token or not isinstance(token, str):
        return False
    session = store.session_by_token_hash(_token_hash(token))
    if session is None:
        return False
    return store.delete_session(session["id"])


# ---------------------------------------------------------------------------
# agent API keys
# ---------------------------------------------------------------------------
def mint_api_key(store: Store, agent: dict) -> tuple[str, dict]:
    """Mint ``vkey_<32hex>``; return the plaintext once plus its stored metadata."""
    workspace_id = agent.get("workspace_id")
    if not workspace_id:
        raise ValueError("agent has no workspace")
    plaintext = _API_KEY_PREFIX + secrets.token_hex(16)
    prefix = plaintext[: len(_API_KEY_PREFIX) + 8]  # vkey_ + first 8 hex, safe to display
    record = store.create_api_key(
        agent["id"], workspace_id, prefix, _token_hash(plaintext)
    )
    public_record = {
        "id": record.get("id"),
        "agent_id": record.get("agent_id"),
        "workspace_id": record.get("workspace_id"),
        "prefix": record.get("prefix"),
        "created_at": record.get("created_at"),
    }
    return plaintext, public_record


def resolve_api_key(store: Store, key: str | None) -> dict | None:
    """Resolve an agent key to ``{agent, workspace_id}``; revoked keys are invisible."""
    if not key or not isinstance(key, str):
        return None
    key = key.strip()
    if not key.startswith(_API_KEY_PREFIX):
        return None
    row = store.api_key_by_hash(_token_hash(key))
    if row is None or row.get("revoked_at"):
        return None
    agent = store.agent(row["workspace_id"], row["agent_id"])
    if agent is None:
        return None
    store.touch_api_key(row["id"])
    return {"agent": agent, "workspace_id": row["workspace_id"]}


# ---------------------------------------------------------------------------
# cookies
# ---------------------------------------------------------------------------
def parse_cookies(header: str | None) -> dict:
    """Parse a request ``Cookie`` header into a dict of name -> value."""
    cookies: dict = {}
    if not header or not isinstance(header, str):
        return cookies
    for chunk in header.split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        name, _, value = chunk.partition("=")
        name = name.strip()
        if not name:
            continue
        cookies[name] = value.strip().strip('"')
    return cookies


def session_cookie(token: str, max_age: int = SESSION_TTL_SECONDS, secure: bool = False) -> str:
    """Build the ``Set-Cookie`` value for a signed-in session."""
    cookie = (
        f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={int(max_age)}"
    )
    if secure:
        cookie += "; Secure"
    return cookie


def clear_cookie(secure: bool = False) -> str:
    """Build the ``Set-Cookie`` value that expires the session cookie."""
    cookie = f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
    if secure:
        cookie += "; Secure"
    return cookie


# ---------------------------------------------------------------------------
# authorisation
# ---------------------------------------------------------------------------
def require_role(user: dict | None, *roles: str) -> dict:
    """Raise ``PermissionError`` unless ``user`` holds one of ``roles``."""
    if not user:
        raise PermissionError("authentication required")
    allowed = roles or VALID_ROLES
    if user.get("role") not in allowed:
        raise PermissionError("insufficient role")
    return user
