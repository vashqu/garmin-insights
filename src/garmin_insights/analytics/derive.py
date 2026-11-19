"""Post-sync recompute: refresh the analysis bundle and forecasts, drop stale LLM briefings."""

from __future__ import annotations

import logging
import time

from garmin_insights import config
from garmin_insights.analytics import forecast
from garmin_insights.analytics.frame import build, bump_version

log = logging.getLogger(__name__)


def recompute() -> dict:
    t = time.time()
    bump_version()
    b = build()
    n = 0
    if config.Settings.load().forecast_enabled and not b.empty:
        try:
            n = forecast.compute_all(b.daily)
        except Exception as e:  # noqa: BLE001
            log.warning("forecast failed: %s", e)
    return {"status": "ok", "days": int(b.daily["has_data"].sum()) if not b.empty else 0, "forecast_rows": n,
            "seconds": round(time.time() - t, 1)}
