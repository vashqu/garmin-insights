"""Sleep science: Sleep Regularity Index, personal sleep need, sleep debt, timing variability."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

MIN_PER_DAY = 1440


def _minute_grid(start: pd.Timestamp, days: int) -> pd.DatetimeIndex:
    return pd.date_range(start, periods=days * MIN_PER_DAY, freq="min")


def sri_for_window(sleeps: pd.DataFrame, end_date: pd.Timestamp, nights: int = 7,
                   max_missing: int = 1) -> float:
    """Sleep Regularity Index for the `nights` nights ending on `end_date` (wake-up date).

    sleeps: rows with start_local/end_local (datetime) and date (wake date).
    SRI = 200·P(state(t) == state(t+24h)) − 100, computed minute by minute (Phillips et al. 2017).
    Nights without a record are masked (unknown) rather than treated as awake.
    """
    end_date = pd.Timestamp(end_date).normalize()
    first = end_date - timedelta(days=nights - 1)
    grid_start = first - timedelta(days=1) + timedelta(hours=12)  # noon before the first night
    n_days = nights
    grid = _minute_grid(grid_start, n_days)
    state = np.zeros(len(grid), dtype=float)
    known = np.ones(len(grid), dtype=bool)
    win = sleeps[(sleeps["date"] >= first) & (sleeps["date"] <= end_date)]
    have = set(pd.to_datetime(win["date"]).dt.normalize())
    missing = 0
    for i in range(nights):
        d = first + timedelta(days=i)
        if d not in have:
            missing += 1
            # mask the night window (previous day 18:00 → day 14:00)
            a = d - timedelta(hours=6)
            b = d + timedelta(hours=14)
            ia = max(0, int((a - grid_start).total_seconds() // 60))
            ib = min(len(grid), int((b - grid_start).total_seconds() // 60))
            known[ia:ib] = False
    if missing > max_missing:
        return np.nan
    for _, r in win.iterrows():
        a, b = r["start_local"], r["end_local"]
        if pd.isna(a) or pd.isna(b):
            continue
        ia = max(0, int((a - grid_start).total_seconds() // 60))
        ib = min(len(grid), int((b - grid_start).total_seconds() // 60))
        if ib > ia:
            state[ia:ib] = 1
    s1, s2 = state[:-MIN_PER_DAY], state[MIN_PER_DAY:]
    k = known[:-MIN_PER_DAY] & known[MIN_PER_DAY:]
    if k.sum() < MIN_PER_DAY * 2:
        return np.nan
    p = float((s1[k] == s2[k]).mean())
    return 200 * p - 100


def sri_series(sleeps: pd.DataFrame, dates: pd.DatetimeIndex, nights: int = 7) -> pd.Series:
    if sleeps.empty:
        return pd.Series(np.nan, index=dates)
    sl = sleeps.copy()
    sl["date"] = pd.to_datetime(sl["date"]).dt.normalize()
    sl["start_local"] = pd.to_datetime(sl["start_local"])
    sl["end_local"] = pd.to_datetime(sl["end_local"])
    max_missing = 1 if nights <= 7 else nights // 5
    return pd.Series([sri_for_window(sl, d, nights, max_missing) for d in dates], index=dates)


def sleep_need(duration_h: pd.Series, window: int = 60, lo: float = 7.0, hi: float = 9.0) -> pd.Series:
    """Personal need = rolling median of your sleep, bounded to the 7–9 h adult recommendation."""
    med = duration_h.shift(1).rolling(window, min_periods=7).median()
    return med.clip(lower=lo, upper=hi).fillna(lo + 0.5)


def sleep_debt(duration_h: pd.Series, need: pd.Series, nights: int = 7) -> pd.Series:
    short = (need - duration_h).clip(lower=0)
    return short.rolling(nights, min_periods=3).sum()


def _clock_minutes(ts: pd.Series, pivot_hour: int) -> pd.Series:
    t = pd.to_datetime(ts)
    m = t.dt.hour * 60 + t.dt.minute
    return (m - pivot_hour * 60) % MIN_PER_DAY


def timing_sd(start_local: pd.Series, end_local: pd.Series, nights: int = 7) -> pd.DataFrame:
    """Rolling SD (minutes) of bedtime (pivot 18:00) and wake time (pivot 00:00)."""
    bed = _clock_minutes(start_local, 18)
    wake = _clock_minutes(end_local, 0)
    return pd.DataFrame({
        "bedtime_min": bed, "wake_min": wake,
        "bedtime_sd7": bed.rolling(nights, min_periods=4).std(),
        "wake_sd7": wake.rolling(nights, min_periods=4).std(),
    })
