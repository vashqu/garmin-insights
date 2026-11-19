"""Personal associations: journal tags / behaviours vs next-night outcomes.

On-vs-off comparison (median difference, bootstrap 90% CI) and lagged Spearman correlation. Results are only
surfaced with enough data (n_on ≥ 10, n_off ≥ 10) and a CI excluding 0 — always worded as 'associated with'."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

OUTCOMES = {
    "sleep_score": ("Sleep score (that night)", 1),
    "sleep_h": ("Sleep duration (that night)", 1),
    "rhr": ("Resting HR (next day)", 1),
    "bb_max": ("Body Battery high (next day)", 1),
    "hrv_night": ("HRV (that night)", 1),
    "stress_avg": ("Avg stress (next day)", 1),
}

INPUTS = {
    "min_strength": "Strength minutes",
    "min_run": "Running minutes",
    "min_stretch": "Stretching minutes",
    "load": "Training load (TRIMP)",
    "steps": "Steps",
    "stress_avg": "Avg stress",
    "last_workout_end_h": "Last workout end (hour)",
}


def bootstrap_diff(on: np.ndarray, off: np.ndarray, n: int = 2000, seed: int = 0) -> tuple[float, float, float]:
    rng = np.random.default_rng(seed)
    d = np.median(on) - np.median(off)
    boots = [np.median(rng.choice(on, len(on))) - np.median(rng.choice(off, len(off))) for _ in range(n)]
    lo, hi = np.percentile(boots, [5, 95])
    return float(d), float(lo), float(hi)


def tag_effects(D: pd.DataFrame, journal: pd.DataFrame, min_n: int = 10) -> pd.DataFrame:
    """For each journal tag (any value > 0 = 'on'), compare the next-night/next-day outcome."""
    if journal.empty:
        return pd.DataFrame()
    j = journal.copy()
    j["date"] = pd.to_datetime(j["date"])
    rows = []
    logged_days = set(j["date"])
    for tag, g in j.groupby("tag"):
        on_days = set(g.loc[g["value"].fillna(0) > 0, "date"])
        for oc, (label, lag) in OUTCOMES.items():
            if oc not in D:
                continue
            y = D[oc].shift(-lag)
            idx = [d for d in D.index if d in logged_days]
            on = y.loc[[d for d in idx if d in on_days]].dropna().values
            off = y.loc[[d for d in idx if d not in on_days]].dropna().values
            if len(on) < 3 or len(off) < 3:
                continue
            diff, lo, hi = bootstrap_diff(on, off)
            rows.append({"tag": tag, "outcome": oc, "outcome_label": label, "n_on": len(on), "n_off": len(off),
                         "median_on": float(np.median(on)), "median_off": float(np.median(off)),
                         "diff": diff, "ci_lo": lo, "ci_hi": hi,
                         "significant": bool(len(on) >= min_n and len(off) >= min_n and (lo > 0 or hi < 0))})
    return pd.DataFrame(rows)


def lagged_spearman(x: pd.Series, y: pd.Series, lag: int = 1) -> dict:
    yy = y.shift(-lag)
    m = pd.concat([x, yy], axis=1).dropna()
    if len(m) < 10:
        return {"rho": None, "p": None, "n": len(m)}
    rho, p = stats.spearmanr(m.iloc[:, 0], m.iloc[:, 1])
    return {"rho": float(rho), "p": float(p), "n": len(m)}


def top_associations(D: pd.DataFrame, min_rho: float = 0.3, min_n: int = 20) -> pd.DataFrame:
    rows = []
    for xi, xl in INPUTS.items():
        if xi not in D or D[xi].nunique() < 3:
            continue
        for oc, (ol, lag) in OUTCOMES.items():
            if oc not in D or oc == xi:
                continue
            r = lagged_spearman(D[xi], D[oc], lag)
            if r["rho"] is not None and abs(r["rho"]) >= min_rho and r["n"] >= min_n and r["p"] < 0.05:
                rows.append({"input": xl, "outcome": ol, **r})
    return pd.DataFrame(rows).sort_values("rho", key=abs, ascending=False) if rows else pd.DataFrame()
