"""Running, swimming and longevity analytics."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

RACE_DISTANCES = {"5K": 5000, "10K": 10000, "Half marathon": 21097.5, "Marathon": 42195}

# ------------------------------------------------------------------ running


def ef_decoupling(samples: pd.DataFrame, min_minutes: float = 20, warmup_min: float = 5) -> dict:
    """Efficiency factor (m/min per bpm) and Pa:HR aerobic decoupling (%) for a steady run.

    Decoupling = (EF_first_half − EF_second_half) / EF_first_half × 100. <5% is a coaching rule of thumb for
    good aerobic endurance at that intensity (TrainingPeaks/Friel; heuristic, not validated cut-off).
    """
    if samples is None or samples.empty or not {"hr", "speed", "t"} <= set(samples.columns):
        return {}
    s = samples[(samples["speed"] > 1.0) & (samples["hr"] > 60)].copy()
    s = s[s["t"] >= warmup_min * 60]
    if s.empty or (s["t"].iloc[-1] - s["t"].iloc[0]) < min_minutes * 60:
        return {}
    mid = s["t"].iloc[0] + (s["t"].iloc[-1] - s["t"].iloc[0]) / 2
    h1, h2 = s[s["t"] <= mid], s[s["t"] > mid]
    ef = lambda x: (x["speed"].mean() * 60) / x["hr"].mean()  # noqa: E731
    ef1, ef2 = ef(h1), ef(h2)
    pace_cv = s["speed"].std() / s["speed"].mean() * 100
    return {"ef": ef(s), "decoupling": (ef1 - ef2) / ef1 * 100, "pace_cv": pace_cv,
            "steady": bool(pace_cv < 12)}


def riegel(t1_s: float, d1_m: float, d2_m: float, exponent: float = 1.06) -> float:
    """Riegel (1981): T2 = T1 · (D2/D1)^1.06."""
    return t1_s * (d2_m / d1_m) ** exponent


def riegel_predictions(runs: pd.DataFrame, asof: pd.Timestamp, days: int = 90, min_dist: float = 3000) -> dict:
    """Best (fastest) Riegel prediction for each race distance from recent runs ≥ min_dist."""
    if runs.empty:
        return {}
    r = runs[(pd.to_datetime(runs["date"]) > asof - timedelta(days=days)) & (runs["distance_m"] >= min_dist)]
    r = r[r["moving_s"].fillna(r["duration_s"]) > 0]
    if r.empty:
        return {}
    out = {}
    for name, d2 in RACE_DISTANCES.items():
        preds = [riegel(row.get("moving_s") or row["duration_s"], row["distance_m"], d2) for _, row in r.iterrows()]
        out[name] = float(min(preds))
    return out


def weekly_run_km(runs: pd.DataFrame) -> pd.Series:
    if runs.empty:
        return pd.Series(dtype=float)
    r = runs.assign(week=pd.to_datetime(runs["date"]).dt.to_period("W-SUN").dt.start_time)
    w = r.groupby("week")["distance_m"].sum() / 1000.0
    full = pd.date_range(w.index.min(), pd.Timestamp.now().to_period("W-SUN").start_time, freq="W-MON")
    return w.reindex(full, fill_value=0.0)


# ------------------------------------------------------------------ swimming

def css_from_test(t400_s: float, t200_s: float) -> float | None:
    """Critical Swim Speed (m/s) = 200 / (T400 − T200). Requires T200 pace faster than T400 pace."""
    if not t400_s or not t200_s or t400_s <= t200_s or t200_s / 200 >= t400_s / 400:
        return None
    return 200.0 / (t400_s - t200_s)


def pace_per_100(distance_m: float, seconds: float) -> float | None:
    if not distance_m or not seconds:
        return None
    return seconds / distance_m * 100


# ------------------------------------------------------------------ longevity

FRIEND_MEDIAN = {  # treadmill VO2max 50th percentile (mL/kg/min), Kaminsky et al. 2015 (FRIEND), verified anchors
    "male": {25: 48.0, 75: 24.4},
    "female": {25: 37.6, 75: 18.3},
}


def vo2max_median_for_age(age: float, sex: str) -> float | None:
    """Linear interpolation between FRIEND decade medians for 20–29 and 70–79 (approximation, labelled in UI)."""
    tab = FRIEND_MEDIAN.get(sex or "male")
    if not tab or not age:
        return None
    (a1, v1), (a2, v2) = sorted(tab.items())
    a = min(max(age, a1), a2)
    return v1 + (v2 - v1) * (a - a1) / (a2 - a1)


def steps_band(age: float | None) -> tuple[int, int]:
    """Paluch et al. 2022: mortality benefit plateaus ~8–10k (<60 y) and ~6–8k (≥60 y)."""
    return (6000, 8000) if age and age >= 60 else (8000, 10000)


WHO_MVPA = (150, 300)  # min/week moderate-equivalent (vigorous counts double)
WHO_STRENGTH_DAYS = 2


def slope_per_30d(s: pd.Series) -> float | None:
    s = s.dropna()
    if len(s) < 10:
        return None
    x = (s.index - s.index[0]).days.values.astype(float)
    b = np.polyfit(x, s.values.astype(float), 1)[0]
    return float(b * 30)
