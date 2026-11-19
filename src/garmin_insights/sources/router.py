"""Source router: official Personal API first, unofficial library as fallback / for extra data types."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from garmin_insights import config
from garmin_insights.sources.official import AuthError, OfficialAPIError, OfficialClient, RateLimited
from garmin_insights.sources.unofficial import UnofficialClient, UnofficialUnavailable

log = logging.getLogger(__name__)


def save_raw(kind: str, key: str, source: str, payload: Any) -> None:
    if payload is None:
        return
    d = config.data_dir() / "raw" / kind
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.{source}.json").write_text(json.dumps(payload, default=str))


class Router:
    def __init__(self, settings: config.Settings | None = None):
        self.settings = settings or config.Settings.load()
        self.official = OfficialClient(delay_s=0.3)
        self.unofficial = UnofficialClient(delay_s=self.settings.request_delay_s) if self.settings.use_unofficial else None
        self.used: dict[str, str] = {}

    @property
    def has_unofficial(self) -> bool:
        return bool(self.unofficial and self.unofficial.connect())

    def fetch(self, kind: str, key: str, official: Callable[[], Any] | None,
              unofficial: Callable[[UnofficialClient], Any] | None = None,
              empty: Callable[[Any], bool] = lambda x: not x) -> tuple[Any, str | None]:
        """Try official → unofficial. Returns (payload, source_name)."""
        if official is not None and self.official.configured:
            try:
                p = official()
                if not empty(p):
                    save_raw(kind, key, "official", p)
                    self.used[kind] = "official"
                    return p, "official"
            except RateLimited:
                raise
            except AuthError:
                if not self.has_unofficial:
                    raise
            except OfficialAPIError as e:
                log.warning("official %s %s failed: %s", kind, key, e)
        if unofficial is not None and self.has_unofficial:
            try:
                p = unofficial(self.unofficial)
                if not empty(p):
                    save_raw(kind, key, "unofficial", p)
                    self.used[kind] = "unofficial"
                    return p, "unofficial"
            except (UnofficialUnavailable, Exception) as e:  # noqa: BLE001 - best effort
                log.info("unofficial %s %s failed: %s", kind, key, e)
        return None, None
