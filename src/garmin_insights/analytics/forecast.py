"""7-day forecasts with Chronos-Bolt-tiny (Amazon, Apache-2.0, 9M params, CPU). Optional dependency."""

from __future__ import annotations

import logging
import threading
from datetime import date, timedelta

import numpy as np
import pandas as pd

from garmin_insights.storage import db

log = logging.getLogger(__name__)
MODEL_ID = "amazon/chronos-bolt-tiny"
METRICS = ("readiness", "rhr", "sleep_h", "stress_avg")
MIN_HISTORY = 42
HORIZON = 7
_pipe = None
_lock = threading.Lock()


def available() -> bool:
    try:
        import chronos  # noqa: F401
        import torch  # noqa: F401

        return True
    except ImportError:
        return False


def _pipeline():
    global _pipe
    with _lock:
        if _pipe is None:
            import torch
            from chronos import BaseChronosPipeline

            torch.set_num_threads(2)
            _pipe = BaseChronosPipeline.from_pretrained(MODEL_ID, device_map="cpu", torch_dtype=torch.float32)
        return _pipe


def forecast_series(s: pd.Series, horizon: int = HORIZON) -> pd.DataFrame | None:
    s = s.dropna()
    if len(s) < MIN_HISTORY:
        return None
    import torch

    ctx = s.asfreq("D").interpolate(limit=3).dropna()
    q, mean = _pipeline().predict_quantiles(torch.tensor(ctx.values, dtype=torch.float32),
                                            prediction_length=horizon, quantile_levels=[0.1, 0.5, 0.9])
    q = q[0].numpy()
    idx = pd.date_range(ctx.index[-1] + timedelta(days=1), periods=horizon, freq="D")
    return pd.DataFrame({"q10": q[:, 0], "q50": q[:, 1], "q90": q[:, 2]}, index=idx)


def compute_all(daily: pd.DataFrame) -> int:
    if not available():
        return 0
    today = date.today().isoformat()
    rows = []
    for m in METRICS:
        if m not in daily:
            continue
        try:
            fr = forecast_series(daily[m])
        except Exception as e:  # noqa: BLE001
            log.warning("forecast %s failed: %s", m, e)
            continue
        if fr is None:
            continue
        lo, hi = (0, 100) if m in ("readiness", "stress_avg") else (0, np.inf)
        for d, r in fr.iterrows():
            rows.append({"made_on": today, "metric": m, "target_date": d.strftime("%Y-%m-%d"),
                         "q10": float(np.clip(r.q10, lo, hi)), "q50": float(np.clip(r.q50, lo, hi)),
                         "q90": float(np.clip(r.q90, lo, hi))})
    db.execute("DELETE FROM forecast WHERE made_on=?", (today,))
    db.upsert("forecast", rows)
    return len(rows)


def cached_forecast(metric: str) -> pd.DataFrame | None:
    rows = db.query("SELECT * FROM forecast WHERE metric=? AND made_on=(SELECT MAX(made_on) FROM forecast "
                    "WHERE metric=?) ORDER BY target_date", (metric, metric))
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df["target_date"])
    return df[["q10", "q50", "q90"]]
