"""Optional fallback: the community ``garminconnect`` library (unofficial Garmin Connect API).

Only used for data the official Personal API does not expose (race predictions, personal records, Body Battery
events, stress intraday, running economy/tolerance, health snapshots, swim details) and as a fallback when an
official call fails. It requires an interactive login (email/password, optional MFA); tokens are cached in
``<data_dir>/tokens/garminconnect``. Garmin may block third-party clients at any time — every call here is
best-effort and failures never break the app.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from garmin_insights import config

log = logging.getLogger(__name__)
PASSWORD_SECRET = "GARMIN_PASSWORD"


class UnofficialUnavailable(RuntimeError):
    pass


class UnofficialClient:
    name = "unofficial"

    def __init__(self, delay_s: float = 1.0):
        self.delay_s = delay_s
        self.api = None
        self._last = 0.0
        self.tokenstore = str(config.data_dir() / "tokens" / "garminconnect")

    # ------------------------------------------------------------- auth
    def has_tokens(self) -> bool:
        from pathlib import Path

        p = Path(self.tokenstore)
        return p.exists() and any(p.iterdir()) if p.is_dir() else p.exists()

    def connect(self) -> bool:
        """Login using cached tokens only (non-interactive). Returns True on success."""
        if self.api is not None:
            return True
        if not self.has_tokens():
            return False
        try:
            from garminconnect import Garmin

            g = Garmin()
            g.login(self.tokenstore)
            self.api = g
            return True
        except Exception as e:  # pragma: no cover - network
            log.warning("garminconnect token login failed: %s", e)
            return False

    def login_start(self, email: str, password: str) -> dict:
        """Interactive login. Returns {"status": "ok"} or {"status": "mfa", "state": <json-able>}."""
        from garminconnect import Garmin

        g = Garmin(email=email, password=password, return_on_mfa=True)
        status, state = g.login(self.tokenstore)
        if status == "needs_mfa":
            self._pending = g
            return {"status": "mfa", "state": json.dumps(state)}
        g.client.dump(self.tokenstore) if hasattr(g, "client") and hasattr(g.client, "dump") else None
        self.api = g
        return {"status": "ok"}

    def login_finish(self, state: str, code: str) -> dict:
        g = getattr(self, "_pending", None)
        if g is None:
            from garminconnect import Garmin

            g = Garmin(return_on_mfa=True)
        g.resume_login(json.loads(state), code)
        try:
            g.client.dump(self.tokenstore)
        except Exception:
            pass
        self.api = g
        return {"status": "ok"}

    # ------------------------------------------------------------- calls
    def call(self, method: str, *args, **kwargs) -> Any:
        if not self.connect():
            raise UnofficialUnavailable("garminconnect not logged in")
        wait = self.delay_s - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        fn = getattr(self.api, method, None)
        if fn is None:
            raise UnofficialUnavailable(f"garminconnect has no method {method}")
        for attempt in range(3):
            try:
                return fn(*args, **kwargs)
            except Exception as e:  # pragma: no cover - network
                msg = str(e)
                if "429" in msg or "Too Many" in msg:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
        raise UnofficialUnavailable(f"{method} rate-limited")
