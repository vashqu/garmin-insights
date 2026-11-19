"""Training load: Banister TRIMP, fitness/fatigue/form (EWMA), Foster monotony & strain, uncoupled ACWR."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

# Fallback relative intensity (HRR fraction) when an activity has no HR
DEFAULT_HRR = {"strength": 0.45, "stretch": 0.20, "breathwork": 0.05, "walk": 0.35, "run": 0.65,
               "swim": 0.60, "bike": 0.55, "other": 0.40}


def trimp(duration_min: float, avg_hr: float | None, rhr: float, max_hr: float, sex: str = "male",
          sport_group: str = "other") -> float:
    """Banister (1991) TRIMP = t · HRr · a·e^(b·HRr); male a=0.64 b=1.92, female a=0.86 b=1.67."""
    if not duration_min or duration_min <= 0:
        return 0.0
    if avg_hr and max_hr and max_hr > rhr:
        hrr = (avg_hr - rhr) / (max_hr - rhr)
    else:
        hrr = DEFAULT_HRR.get(sport_group, 0.4)
    hrr = min(max(hrr, 0.0), 1.0)
    a, b = (0.86, 1.67) if sex == "female" else (0.64, 1.92)
    return float(duration_min * hrr * a * math.exp(b * hrr))


def ewma(x: pd.Series, tau: float) -> pd.Series:
    """Exponentially weighted load with time constant tau days (as in Banister / PMC models)."""
    alpha = 1 - math.exp(-1 / tau)
    out, v = [], 0.0
    for val in x.fillna(0):
        v = v + alpha * (val - v)
        out.append(v)
    return pd.Series(out, index=x.index)


def load_frame(daily_load: pd.Series) -> pd.DataFrame:
    """daily_load: calendar-indexed sum of TRIMP per day (0 on rest days)."""
    L = daily_load.fillna(0)
    fitness = ewma(L, 42)
    fatigue = ewma(L, 7)
    form = fitness - fatigue
    m7 = L.rolling(7, min_periods=7).mean()
    sd7 = L.rolling(7, min_periods=7).std()
    monotony = m7 / sd7.where(sd7 > 1e-6)
    monotony = monotony.where(m7 > 0)
    sum7 = L.rolling(7, min_periods=7).sum()
    strain = sum7 * monotony
    chronic_excl = L.shift(7).rolling(21, min_periods=14).sum() / 3.0  # weekly avg of days 8–28
    acwr = sum7 / chronic_excl.where(chronic_excl > 0)
    return pd.DataFrame({"load": L, "fitness": fitness, "fatigue": fatigue, "form": form,
                         "monotony": monotony, "strain": strain, "load7": sum7, "acwr_uncoupled": acwr})


def activity_loads(acts: pd.DataFrame, rhr_by_date: pd.Series, max_hr: float, sex: str,
                   rhr_default: float = 60) -> pd.Series:
    """Per-activity TRIMP (returns series aligned to acts.index)."""
    out = []
    for _, a in acts.iterrows():
        d = pd.Timestamp(a["date"])
        rhr = rhr_by_date.get(d, np.nan) if rhr_by_date is not None else np.nan
        rhr = rhr if rhr and not np.isnan(rhr) else rhr_default
        dur = (a.get("moving_s") or a.get("duration_s") or 0) / 60.0
        hr = a.get("avg_hr")
        hr = None if hr is None or (isinstance(hr, float) and np.isnan(hr)) else hr
        out.append(trimp(dur, hr, rhr, max_hr, sex, a.get("sport_group") or "other"))
    return pd.Series(out, index=acts.index)
