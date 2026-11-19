"""Strength analytics from Strong sets: fractional weekly hard sets per muscle, freshness, e1RM, PRs."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

SETS_LO, SETS_HI = 10, 20  # typical evidence-informed weekly range per muscle (Schoenfeld 2017; Pelland)
FRESH_H = 48

MUSCLE_LABELS = {
    "chest": "Chest", "lats": "Lats", "upper_back": "Upper back", "front_delts": "Front delts",
    "side_delts": "Side delts", "rear_delts": "Rear delts", "biceps": "Biceps", "triceps": "Triceps",
    "forearms": "Forearms", "quads": "Quads", "hamstrings": "Hamstrings", "glutes": "Glutes",
    "adductors": "Adductors", "abductors": "Abductors", "calves": "Calves", "abs": "Abs",
    "lower_back": "Lower back",
}


def hard_sets(sets: pd.DataFrame) -> pd.DataFrame:
    """Working/failure/drop sets with reps>0 (or timed). If RPE is logged, RPE ≥ 7 only."""
    if sets.empty:
        return sets
    s = sets[sets["set_type"].isin(["working", "failure", "drop"])].copy()
    s = s[(s["reps"].fillna(0) > 0) | (s["seconds"].fillna(0) > 0)]
    has_rpe = s["rpe"].notna()
    s = s[~has_rpe | (s["rpe"] >= 7)]
    return s


def muscle_sets(sets: pd.DataFrame, emap: pd.DataFrame) -> pd.DataFrame:
    """Explode each hard set into muscle contributions: primary = 1.0, secondary = 0.5."""
    hs = hard_sets(sets)
    if hs.empty or emap.empty:
        return pd.DataFrame(columns=["date", "workout_start", "exercise", "muscle", "weight"])
    m = emap.set_index("exercise")
    rows = []
    for _, r in hs.iterrows():
        if r["exercise"] not in m.index:
            continue
        e = m.loc[r["exercise"]]
        for mu in filter(None, (e["primary_muscles"] or "").split(",")):
            rows.append((r["date"], r["workout_start"], r["exercise"], mu, 1.0))
        for mu in filter(None, (e["secondary_muscles"] or "").split(",")):
            rows.append((r["date"], r["workout_start"], r["exercise"], mu, 0.5))
    df = pd.DataFrame(rows, columns=["date", "workout_start", "exercise", "muscle", "weight"])
    df["date"] = pd.to_datetime(df["date"])
    return df


def weekly_sets(ms: pd.DataFrame) -> pd.DataFrame:
    """ISO-week (Mon) × muscle fractional set totals."""
    if ms.empty:
        return pd.DataFrame()
    w = ms.assign(week=ms["date"].dt.to_period("W-SUN").dt.start_time)
    return w.pivot_table(index="week", columns="muscle", values="weight", aggfunc="sum", fill_value=0)


def rolling7_sets(ms: pd.DataFrame, asof: pd.Timestamp) -> pd.Series:
    if ms.empty:
        return pd.Series(dtype=float)
    a = pd.Timestamp(asof).normalize()
    win = ms[(ms["date"] > a - timedelta(days=7)) & (ms["date"] <= a)]
    return win.groupby("muscle")["weight"].sum()


def freshness(ms: pd.DataFrame, sets: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """Hours since each muscle was last trained as a *primary* mover."""
    now = now or datetime.now()
    if ms.empty:
        return pd.DataFrame(columns=["muscle", "last", "hours", "fresh"])
    dur = sets.groupby("workout_start")["workout_duration_s"].max()
    prim = ms[ms["weight"] >= 1.0]
    last = prim.groupby("muscle")["workout_start"].max()
    out = []
    for mu, ws in last.items():
        end = datetime.fromisoformat(ws) + timedelta(seconds=float(dur.get(ws, 0) or 0))
        h = (now - end).total_seconds() / 3600
        out.append({"muscle": mu, "last": end, "hours": h, "fresh": h >= FRESH_H})
    return pd.DataFrame(out).sort_values("hours")


def epley(weight: float, reps: float) -> float:
    return weight * (1 + reps / 30.0)


def e1rm_history(sets: pd.DataFrame) -> pd.DataFrame:
    """Best estimated 1RM per exercise per day (Epley; sets of 1–10 reps with load)."""
    hs = hard_sets(sets)
    if hs.empty:
        return pd.DataFrame(columns=["date", "exercise", "e1rm", "weight_kg", "reps"])
    hs = hs[(hs["weight_kg"].fillna(0) > 0) & (hs["reps"].between(1, 10))].copy()
    if hs.empty:
        return pd.DataFrame(columns=["date", "exercise", "e1rm", "weight_kg", "reps"])
    hs["e1rm"] = epley(hs["weight_kg"], hs["reps"])
    idx = hs.groupby(["date", "exercise"])["e1rm"].idxmax()
    out = hs.loc[idx, ["date", "exercise", "e1rm", "weight_kg", "reps"]].copy()
    out["date"] = pd.to_datetime(out["date"])
    out = out.sort_values("date")
    prev_best = out.groupby("exercise")["e1rm"].transform(lambda s: s.cummax().shift(1))
    out["is_pr"] = out["e1rm"] > prev_best + 1e-9
    return out


def volume_by_exercise(sets: pd.DataFrame) -> pd.DataFrame:
    hs = hard_sets(sets)
    if hs.empty:
        return pd.DataFrame()
    hs = hs.assign(volume=hs["weight_kg"].fillna(0) * hs["reps"].fillna(0),
                   date=pd.to_datetime(hs["date"]))
    return hs.groupby(["date", "exercise"]).agg(sets=("reps", "size"), reps=("reps", "sum"),
                                                volume=("volume", "sum")).reset_index()


def strength_days(sets: pd.DataFrame, acts: pd.DataFrame) -> pd.Series:
    """Calendar dates with strength training (Strong workout or Garmin strength activity)."""
    d1 = set(pd.to_datetime(sets["date"]).dt.normalize()) if not sets.empty else set()
    d2 = set(pd.to_datetime(acts.loc[acts["sport_group"] == "strength", "date"]).dt.normalize()) \
        if not acts.empty else set()
    days = sorted(d1 | d2)
    return pd.Series(1, index=pd.DatetimeIndex(days), dtype=float)


def under_target(r7: pd.Series, muscles: list[str], lo: float = SETS_LO) -> list[tuple[str, float]]:
    return sorted(((m, float(r7.get(m, 0.0))) for m in muscles if r7.get(m, 0.0) < lo), key=lambda x: x[1])


def nan_to_none(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else x
