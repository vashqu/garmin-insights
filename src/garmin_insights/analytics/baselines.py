"""Personal baselines (robust rolling median / MAD) and green/amber/red status."""

from __future__ import annotations

import numpy as np
import pandas as pd

MAD_K = 1.4826
GREEN, AMBER, RED, CALIBRATING, NODATA = "green", "amber", "red", "calibrating", "nodata"


def _mad(x: np.ndarray) -> float:
    x = x[~np.isnan(x)]
    if len(x) == 0:
        return np.nan
    return float(np.median(np.abs(x - np.median(x)))) * MAD_K


def rolling_baseline(s: pd.Series, window: int = 60, min_periods: int = 14, min_scale: float = 0.0) -> pd.DataFrame:
    """Baseline for each day computed from the *previous* `window` days (today excluded)."""
    prev = s.shift(1)
    med = prev.rolling(window, min_periods=min_periods).median()
    mad = prev.rolling(window, min_periods=min_periods).apply(_mad, raw=True)
    n = prev.rolling(window, min_periods=1).count()
    floor = np.maximum(min_scale, 0.03 * med.abs().fillna(0))
    scale = np.maximum(mad.fillna(0), floor)
    scale = scale.where(med.notna())
    z = (s - med) / scale.replace(0, np.nan)
    return pd.DataFrame({"value": s, "median": med, "scale": scale, "z": z, "n": n})


def bad_z(z: float, direction: str) -> float:
    """Positive = worse. direction: 'up' (higher is better), 'down' (lower is better), 'two' (either way bad)."""
    if z is None or (isinstance(z, float) and np.isnan(z)):
        return np.nan
    if direction == "up":
        return -z
    if direction == "down":
        return z
    return abs(z)


def status_from_z(z: float, direction: str, green: float = 0.75, red: float = 1.5) -> str:
    b = bad_z(z, direction)
    if b is None or np.isnan(b):
        return CALIBRATING
    if b < green:
        return GREEN
    if b < red:
        return AMBER
    return RED


def status_series(bl: pd.DataFrame, direction: str) -> pd.Series:
    out = []
    for v, z in zip(bl["value"], bl["z"], strict=False):
        if pd.isna(v):
            out.append(NODATA)
        else:
            out.append(status_from_z(z, direction))
    return pd.Series(out, index=bl.index)


def to_calendar(df: pd.DataFrame, date_col: str = "date", start=None, end=None) -> pd.DataFrame:
    """Index by a continuous daily DatetimeIndex (missing days → NaN)."""
    if df.empty:
        idx = pd.DatetimeIndex([], name="date")
        return pd.DataFrame(index=idx)
    d = df.copy()
    d[date_col] = pd.to_datetime(d[date_col])
    d = d.drop_duplicates(date_col, keep="last").set_index(date_col).sort_index()
    start = pd.Timestamp(start) if start is not None else d.index.min()
    end = pd.Timestamp(end) if end is not None else d.index.max()
    idx = pd.date_range(start, end, freq="D", name="date")
    return d.reindex(idx)
