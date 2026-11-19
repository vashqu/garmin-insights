"""Read-only tools exposed to the chat model. Each returns small JSON-able dicts."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from garmin_insights.analytics import muscle as mu
from garmin_insights.analytics.frame import get_bundle

METRICS = {
    "readiness": "app readiness score 0-100", "rhr": "resting heart rate bpm", "sleep_h": "sleep hours",
    "sleep_score": "Garmin sleep score", "sri7": "sleep regularity index (7 nights)",
    "sleep_debt7": "sleep debt hours (7 nights)", "stress_avg": "average stress 0-100",
    "bb_max": "Body Battery daily high", "bb_min": "Body Battery daily low", "steps": "steps",
    "resp_sleep": "sleep respiration brpm", "hrv_night": "overnight HRV ms", "load": "training load TRIMP",
    "fitness": "fitness (42-day load)", "fatigue": "fatigue (7-day load)", "form": "form = fitness - fatigue",
    "vo2max": "VO2max", "im_week": "intensity minutes last 7 days", "hard_sets": "strength hard sets per day",
    "cal_total": "total calories", "cal_active": "active calories", "floors_up": "floors climbed",
    "fitness_age": "Garmin fitness age",
}


def metric_names() -> list[str]:
    return list(METRICS)


def _clean(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, (np.floating, float)):
        return round(float(v), 2)
    if isinstance(v, np.integer):
        return int(v)
    return v


def _range(start_date: str | None, end_date: str | None, default_days: int = 30):
    end = pd.Timestamp(end_date) if end_date else pd.Timestamp(date.today())
    start = pd.Timestamp(start_date) if start_date else end - timedelta(days=default_days - 1)
    return start.normalize(), end.normalize()


def get_metric_series(metric: str, start_date: str = "", end_date: str = "") -> dict:
    """Get daily values and summary statistics of one metric over a date range.

    Args:
        metric: metric name, e.g. 'rhr', 'sleep_h', 'readiness', 'steps', 'stress_avg'
        start_date: first date YYYY-MM-DD (default 30 days ago)
        end_date: last date YYYY-MM-DD (default today)
    """
    b = get_bundle()
    if metric not in b.daily:
        return {"error": f"unknown metric {metric}", "available": metric_names()}
    s, e = _range(start_date or None, end_date or None)
    x = b.daily.loc[s:e, metric].dropna()
    if x.empty:
        return {"metric": metric, "n": 0, "note": "no data in range"}
    vals = {d.strftime("%Y-%m-%d"): _clean(v) for d, v in x.tail(45).items()}
    return {"metric": metric, "description": METRICS.get(metric, ""), "start": s.date().isoformat(),
            "end": e.date().isoformat(), "n": int(len(x)), "mean": _clean(x.mean()), "median": _clean(x.median()),
            "min": _clean(x.min()), "max": _clean(x.max()), "latest": _clean(x.iloc[-1]), "values": vals}


def compare_periods(metric: str, a_start: str, a_end: str, b_start: str, b_end: str) -> dict:
    """Compare the mean of a metric between two date ranges (A vs B).

    Args:
        metric: metric name
        a_start: period A start YYYY-MM-DD
        a_end: period A end YYYY-MM-DD
        b_start: period B start YYYY-MM-DD
        b_end: period B end YYYY-MM-DD
    """
    b = get_bundle()
    if metric not in b.daily:
        return {"error": f"unknown metric {metric}"}
    A = b.daily.loc[pd.Timestamp(a_start):pd.Timestamp(a_end), metric].dropna()
    B = b.daily.loc[pd.Timestamp(b_start):pd.Timestamp(b_end), metric].dropna()
    return {"metric": metric, "A_mean": _clean(A.mean()), "A_n": len(A), "B_mean": _clean(B.mean()), "B_n": len(B),
            "difference_A_minus_B": _clean(A.mean() - B.mean()) if len(A) and len(B) else None}


def list_activities(start_date: str = "", end_date: str = "", sport: str = "") -> dict:
    """List workouts (Garmin activities) in a date range.

    Args:
        start_date: first date YYYY-MM-DD (default 30 days ago)
        end_date: last date YYYY-MM-DD (default today)
        sport: optional filter: run, strength, swim, stretch, walk, breathwork
    """
    b = get_bundle()
    if b.acts.empty:
        return {"activities": []}
    s, e = _range(start_date or None, end_date or None)
    a = b.acts[(b.acts["date"] >= s) & (b.acts["date"] <= e)]
    if sport:
        a = a[a["sport_group"] == sport]
    out = [{"date": r["date"].strftime("%Y-%m-%d"), "type": r["type_key"], "minutes": _clean((r["duration_s"] or 0) / 60),
            "km": _clean((r["distance_m"] or 0) / 1000), "avg_hr": _clean(r["avg_hr"]), "trimp": _clean(r["trimp"])}
           for _, r in a.tail(40).iterrows()]
    return {"count": int(len(a)), "total_minutes": _clean(a["duration_s"].sum() / 60), "activities": out}


def strength_summary(start_date: str = "", end_date: str = "") -> dict:
    """Summarise strength training from the Strong log: hard sets per muscle, best estimated 1RM per exercise.

    Args:
        start_date: first date YYYY-MM-DD (default 28 days ago)
        end_date: last date YYYY-MM-DD (default today)
    """
    b = get_bundle()
    if b.sets.empty:
        return {"note": "no Strong data imported"}
    s, e = _range(start_date or None, end_date or None, 28)
    ms = b.msets[(b.msets["date"] >= s) & (b.msets["date"] <= e)] if not b.msets.empty else pd.DataFrame()
    per_muscle = ms.groupby("muscle")["weight"].sum().round(1).to_dict() if not ms.empty else {}
    weeks = max(1.0, ((e - s).days + 1) / 7)
    e1 = mu.e1rm_history(b.sets)
    e1 = e1[(e1["date"] >= s) & (e1["date"] <= e)]
    best = e1.groupby("exercise")["e1rm"].max().round(1).to_dict() if not e1.empty else {}
    sets = b.sets[(pd.to_datetime(b.sets["date"]) >= s) & (pd.to_datetime(b.sets["date"]) <= e)]
    return {"workouts": int(sets["workout_start"].nunique()), "hard_sets_per_muscle_total": per_muscle,
            "hard_sets_per_muscle_per_week": {k: round(v / weeks, 1) for k, v in per_muscle.items()},
            "best_e1rm_kg": best}


def get_day(day: str) -> dict:
    """Get all key metrics and statuses for one date.

    Args:
        day: date YYYY-MM-DD
    """
    b = get_bundle()
    r = b.day(day)
    if r.empty:
        return {"error": "no data for that date"}
    keys = list(METRICS) + [k + "_status" for k in ("rhr", "sleep_h", "sri7", "stress_avg", "bb_max")] + \
        ["illness_level", "readiness_status"]
    return {k: _clean(r.get(k)) for k in keys if k in r}


def journal_effects() -> dict:
    """Get personal associations between journal tags (alcohol, late caffeine, etc.) and next-night outcomes."""
    from garmin_insights.analytics.correlations import tag_effects

    b = get_bundle()
    t = tag_effects(b.daily, b.journal)
    if t.empty:
        return {"note": "not enough journal entries yet"}
    t = t.round(2)
    return {"effects": t.to_dict("records")[:30]}


TOOLS = {f.__name__: f for f in (get_metric_series, compare_periods, list_activities, strength_summary, get_day,
                                 journal_effects)}
