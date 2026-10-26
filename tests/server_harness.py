"""Test-only harness: load the real ``server`` module against a throwaway DB.

``server.py`` opens a store and builds the PayPal simulator at import time, which
is exactly what we want to exercise for the HTTP handler logic -- but tests must
never touch the live ``.state`` database. This helper sets the environment to a
temp directory first, forces the offline simulator, and returns the cached
module so several test modules can share one import.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class StubHandler:
    """Just enough of ``server.Handler`` to call route methods unbound.

    The handler methods only use a handful of helpers; this stub implements the
    same contracts so the real business logic (role checks, error codes,
    responses) is what gets tested, not a reimplementation.
    """

    def __init__(self, server, session: dict | None) -> None:
        self.server = server
        self.session = session
        self.response: dict | None = None

    def _require_session(self) -> dict:
        if self.session is None:
            raise self.server.ApiError(401, "unauthorized", "Sign in to continue.")
        return self.session

    def _require_role(self, *roles: str) -> dict:
        session = self._require_session()
        try:
            self.server.veto_auth.require_role(session["user"], *roles)
        except PermissionError:
            raise self.server.ApiError(
                403, "forbidden", "You do not have access to that."
            ) from None
        return session

    def _api_error(self, status: int, code: str, message: str) -> None:
        raise self.server.ApiError(status, code, message)

    def _ok(self, data: dict | None = None, status: int = 200) -> None:
        self.response = {
            "ok": True,
            "data": data if data is not None else {},
            "status": status,
        }


def load_server():
    """Import ``server`` with an isolated temp DB and the offline money mover."""
    if "server" in sys.modules:
        return sys.modules["server"]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    tmp = tempfile.mkdtemp(prefix="veto-server-test-")
    atexit.register(shutil.rmtree, tmp, True)
    os.environ["VETO_DB"] = os.path.join(tmp, "veto.db")
    os.environ["VETO_SEED_DEMO"] = "0"
    os.environ["PAYPAL_LIVE"] = "0"  # the in-process simulator, never the network
    import server  # noqa: PLC0415 - deliberately lazy, after the env is set

    return server
