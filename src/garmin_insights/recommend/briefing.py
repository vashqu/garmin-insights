"""Assemble the morning facts, the rule-based recommendations and the briefing text (LLM or template)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from garmin_insights import config
from garmin_insights.analytics.frame import Bundle
from garmin_insights.recommend import llm
from garmin_insights.recommend.race import active_race
from garmin_insights.recommend.rules import DayContext, Recommendation, recommend

STATUS_WORD = {"green": "normal", "amber": "slightly off your norm", "red": "well off your norm",
               "calibrating": "still calibrating", "nodata": "no data"}


def _r(v, nd=1):
    if v is None or (isinstance(v, float) and v != v):
        return None
    return round(float(v), nd) if nd else int(round(float(v)))


@dataclass
class Morning:
    date: pd.Timestamp
    facts: dict
    recs: list[Recommendation]
    text: str = ""
    source: str = "template"
    extra: dict = field(default_factory=dict)


def _sleep_facts(row) -> dict:
    h, need = _r(row.get("sleep_h")), _r(row.get("sleep_need_h"))
    out = {"hours": h, "need_hours": need, "score": _r(row.get("sleep_score"), 0),
           "regularity_index_7d": _r(row.get("sri7"), 0), "debt_7d_hours": _r(row.get("sleep_debt7"))}
    if h is not None and need is not None:
        diff = round(h - need, 1)
        out["verdict"] = ("met your need" if diff >= -0.2 else f"{abs(diff)} h short of your need")
    if out["regularity_index_7d"] is not None:
        out["regularity_verdict"] = "regular" if out["regularity_index_7d"] >= 80 else "somewhat irregular"
    return out


def build_facts(c: DayContext, recs: list[Recommendation]) -> dict:
    row, prev = c.row, c.prev
    f = {
        "date": c.date.strftime("%A %d %B"),
        "readiness": {"score": _r(row.get("readiness"), 0),
                      "verdict": {"green": "good", "amber": "moderate", "red": "low"}.get(row.get("readiness_status"),
                                                                                      "unknown")},
        "sleep": _sleep_facts(row),
        "resting_hr": {"today": _r(row.get("rhr"), 0), "your_normal": _r(row.get("rhr_med"), 0),
                       "status": STATUS_WORD.get(row.get("rhr_status"), "")},
        "body_battery_high": _r(row.get("bb_max"), 0),
        "stress_yesterday": {"avg": _r(prev.get("stress_avg"), 0),
                             "status": STATUS_WORD.get(prev.get("stress_avg_status"), "")},
        "illness_signal": row.get("illness_level") if row.get("illness_level") != "none" else None,
        "recommendations": [{"title": r.title, "do": r.action} for r in recs[:3]],
    }
    if c.b.flags.get("hrv_available"):
        f["hrv"] = {"last_night_ms": _r(row.get("hrv_night"), 0), "trend": row.get("hrv_state")}
    acts = c.b.acts
    if not acts.empty:
        y = acts[acts["date"] == c.date - pd.Timedelta(days=1)]
        if len(y):
            f["yesterday_workouts"] = [f"{a['type_key'].replace('_', ' ')} {round((a['duration_s'] or 0) / 60)} min"
                                       for _, a in y.iterrows()]
    return f


def template_text(f: dict, recs: list[Recommendation]) -> str:
    parts = []
    r = f["readiness"]["score"]
    sl = f["sleep"]
    if f.get("illness_signal"):
        parts.append("Your resting heart rate is elevated versus your normal, which can be an early sign your body "
                     "is under strain — take it easy today.")
    elif r is not None:
        mood = "Recovery looks good" if r >= 60 else ("Recovery is mixed" if r >= 40 else "Recovery is low")
        parts.append(f"{mood} this morning (readiness {r}/100).")
    if sl.get("hours"):
        need = sl.get("need_hours")
        s = f"You slept {sl['hours']} h"
        if need:
            s += f" against a need of about {need} h"
        if sl.get("score"):
            s += f" (sleep score {sl['score']})"
        parts.append(s + ".")
    rh = f["resting_hr"]
    if rh.get("today") and rh.get("your_normal"):
        parts.append(f"Resting HR is {rh['today']} bpm vs your usual {rh['your_normal']}.")
    for rec in recs[:2]:
        parts.append(f"{rec.title}: {rec.action}")
    return " ".join(parts) if parts else "Not enough data yet for a briefing — sync your watch and check back."


def morning(b: Bundle, d=None, use_llm: bool = True, use_cache: bool = True) -> Morning:
    race = active_race()
    c = DayContext.build(b, d, race)
    recs = recommend(c)
    facts = build_facts(c, recs)
    text = template_text(facts, recs)
    source = "template"
    s = config.Settings.load()
    if use_llm and s.llm_enabled and not b.empty:
        t, status = llm.generate_briefing(facts, s.llm_briefing_model, use_cache)
        if t:
            text, source = t, ("llm" if status in ("llm", "cached") else status)
        else:
            source = f"template ({status})"
    return Morning(c.date, facts, recs, text, source, {"race": race})


def morning_fast(b: Bundle, d=None) -> Morning:
    """Template-only (instant) — the UI shows this first and swaps in the LLM text when ready."""
    return morning(b, d, use_llm=False)
