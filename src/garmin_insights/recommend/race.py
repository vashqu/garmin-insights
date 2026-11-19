"""Race mode: predicted time vs goal, readiness %, training phase, weekly targets and key sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from garmin_insights.analytics import sports as sp
from garmin_insights.storage import db

# Rough, conservative weekly-volume & long-run targets (km) for recreational runners at peak.
TARGETS = {
    5000: {"weekly_km": 25, "long_km": 8, "min_weeks": 6},
    10000: {"weekly_km": 35, "long_km": 14, "min_weeks": 8},
    21097.5: {"weekly_km": 45, "long_km": 18, "min_weeks": 10},
    42195: {"weekly_km": 60, "long_km": 30, "min_weeks": 16},
}


def _target(distance_m: float) -> dict:
    k = min(TARGETS, key=lambda d: abs(d - distance_m))
    t = dict(TARGETS[k])
    scale = distance_m / k
    t["weekly_km"] *= min(max(scale, 0.6), 1.5)
    t["long_km"] = min(t["long_km"] * scale, distance_m / 1000 * 0.75 if distance_m > 21000 else distance_m / 1000 * 1.1)
    return t


def active_race() -> dict | None:
    rows = db.query("SELECT * FROM race WHERE active=1 AND date >= ? ORDER BY date LIMIT 1",
                    (date.today().isoformat(),))
    return rows[0] if rows else None


def fmt_time(s: float | None) -> str:
    if s is None:
        return "–"
    s = int(round(s))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


@dataclass
class RacePlan:
    race: dict
    weeks_out: float
    phase: str
    predicted_s: float | None
    predicted_src: str
    garmin_pred_s: float | None
    riegel_pred_s: float | None
    readiness_pct: float
    components: dict
    weekly_km_now: float
    longest_4w_km: float
    target: dict
    week_plan: dict
    warnings: list


def phase_for(weeks_out: float, total_weeks: float) -> str:
    if weeks_out <= 0:
        return "Race week"
    if weeks_out <= 2:
        return "Taper"
    if weeks_out <= max(4, total_weeks * 0.3):
        return "Peak"
    if weeks_out <= max(8, total_weeks * 0.65):
        return "Build"
    return "Base"


def build_plan(b, race: dict, asof: date | None = None) -> RacePlan:
    asof = asof or date.today()
    rd = date.fromisoformat(race["date"])
    weeks_out = (rd - asof).days / 7
    dist = float(race["distance_m"])
    tgt = _target(dist)
    runs = b.acts[b.acts["sport_group"] == "run"] if not b.acts.empty else pd.DataFrame()
    a = pd.Timestamp(asof)
    recent4 = runs[runs["date"] > a - timedelta(days=28)] if not runs.empty else runs
    weekly_now = (recent4["distance_m"].sum() / 1000 / 4) if not runs.empty else 0.0
    longest = (recent4["distance_m"].max() / 1000) if not recent4.empty else 0.0
    weeks_with_3 = 0
    if not runs.empty:
        r = runs[runs["date"] > a - timedelta(days=28)]
        weeks_with_3 = int((r.groupby(r["date"].dt.to_period("W"))["activity_id"].count() >= 3).sum())
    # predictions
    rp = db.query("SELECT * FROM race_prediction ORDER BY date DESC LIMIT 1")
    gp = None
    if rp:
        key = {5000: "t5k", 10000: "t10k", 21097.5: "thalf", 42195: "tmarathon"}.get(
            min((5000, 10000, 21097.5, 42195), key=lambda d: abs(d - dist)))
        if rp[0].get(key):
            gp = float(rp[0][key]) * (dist / {"t5k": 5000, "t10k": 10000, "thalf": 21097.5, "tmarathon": 42195}[key]) ** 1.06
    rg = None
    if not runs.empty:
        preds = []
        for _, x in runs[(runs["date"] > a - timedelta(days=90)) & (runs["distance_m"] >= 3000)].iterrows():
            t = x.get("moving_s") or x.get("duration_s")
            if t:
                preds.append(sp.riegel(t, x["distance_m"], dist))
        rg = min(preds) if preds else None
    pred = gp or rg
    src = "Garmin race predictor" if gp else ("Riegel from your best recent run" if rg else "not enough running data")
    goal = race.get("goal_s")
    comps = {}
    comps["volume"] = min(weekly_now / tgt["weekly_km"], 1.0) if tgt["weekly_km"] else 0
    comps["long_run"] = min(longest / tgt["long_km"], 1.0) if tgt["long_km"] else 0
    comps["consistency"] = weeks_with_3 / 4
    comps["pace"] = (min(goal / pred, 1.0) if (goal and pred) else (0.7 if pred else 0.0))
    w = {"volume": 0.3, "long_run": 0.25, "consistency": 0.2, "pace": 0.25}
    pct = 100 * sum(comps[k] * w[k] for k in w)
    total_weeks = max(weeks_out, tgt["min_weeks"])
    ph = phase_for(weeks_out, total_weeks)
    # weekly prescription
    peak_km = max(tgt["weekly_km"], weekly_now)
    if ph == "Base":
        wk = min(weekly_now * 1.1 + 2, peak_km * 0.8)
        key_sessions = ["Easy runs at conversational pace (HR zone 2)", "Strides 6×20 s after one easy run"]
    elif ph == "Build":
        wk = min(weekly_now * 1.1 + 2, peak_km)
        key_sessions = ["Tempo: 20–30 min at comfortably-hard effort (~threshold)",
                        "Intervals: 5×3 min hard / 2 min easy"]
    elif ph == "Peak":
        wk = peak_km
        key_sessions = [f"Race-pace work: 3×{max(1, round(dist / 1000 / 4))} km at goal pace",
                        "Intervals: 6×800 m slightly faster than race pace"]
    elif ph == "Taper":
        wk = peak_km * (0.6 if weeks_out > 1 else 0.45)
        key_sessions = ["Keep intensity: short race-pace reps (e.g. 4×1 km)", "Reduce volume ~40–60%, keep frequency"]
    else:
        wk = peak_km * 0.35
        key_sessions = ["2–3 short easy runs + strides", "Rest the day before the race"]
    long_km = min(tgt["long_km"], max(longest + 1.5, longest * 1.1)) if ph in ("Base", "Build", "Peak") else \
        tgt["long_km"] * (0.6 if ph == "Taper" else 0)
    warnings = []
    cap = max(weekly_now * 1.3, weekly_now + 5)  # relative cap, but allow +5 km for low-volume runners
    if wk > cap:
        wk = cap
        warnings.append("Weekly distance capped (max +30% or +5 km vs your 4-week average — heuristic guardrail).")
    if weeks_out < tgt["min_weeks"] * 0.5 and comps["volume"] < 0.6:
        warnings.append("Short preparation time for this distance — consider a conservative goal.")
    plan = {"weekly_km": round(wk, 1), "long_run_km": round(long_km, 1), "key_sessions": key_sessions,
            "strength": "Keep 2 strength sessions/week" + ("; reduce leg volume" if ph in ("Peak", "Taper") else "")}
    return RacePlan(race, weeks_out, ph, pred, src, gp, rg, pct, comps, weekly_now, longest, tgt, plan, warnings)


def race_recommendation(c):
    from garmin_insights.recommend.rules import Recommendation

    if not c.race:
        return None
    p = build_plan(c.b, c.race, c.date.date())
    r = c.row.get("readiness")
    ks = p.week_plan["key_sessions"][0]
    if r is not None and r == r and r < 40:
        ks = "Move this week's key session to a day when readiness is ≥50; easy running today."
    return Recommendation(
        "race", "training", 2, f"{c.race['name']}: {p.phase} phase, {max(p.weeks_out, 0):.0f} weeks out",
        f"This week: ~{p.week_plan['weekly_km']} km, long run {p.week_plan['long_run_km']} km. Key: {ks}",
        f"Race readiness {p.readiness_pct:.0f}%. Predicted {fmt_time(p.predicted_s)} ({p.predicted_src})"
        + (f" vs goal {fmt_time(c.race.get('goal_s'))}." if c.race.get("goal_s") else "."),
        {"race_readiness_pct": round(p.readiness_pct), "weekly_km_now": round(p.weekly_km_now, 1),
         "weeks_out": round(p.weeks_out, 1)}, "moderate", "⚑")
