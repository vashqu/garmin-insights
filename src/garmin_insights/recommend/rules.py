"""Deterministic recommendation rules. Each rule reads a DayContext and may emit a Recommendation with the
numbers behind it (evidence) and the evidence level of the underlying science."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from garmin_insights.analytics import muscle as mu
from garmin_insights.analytics import sports as sp
from garmin_insights.analytics.frame import Bundle

BIG_MUSCLES = ("chest", "lats", "upper_back", "quads", "hamstrings", "glutes", "side_delts", "rear_delts", "biceps",
               "triceps", "calves", "front_delts")
PRIORITY = {"health": 0, "recovery": 1, "training": 2, "muscle": 3, "sleep": 4, "stress": 5, "habit": 6}


@dataclass
class Recommendation:
    key: str
    category: str
    priority: int
    title: str
    action: str
    reason: str
    evidence: dict = field(default_factory=dict)
    evidence_level: str = "moderate"
    icon: str = "•"

    def as_dict(self) -> dict:
        return asdict(self)


def _v(row: pd.Series, k: str):
    v = row.get(k) if row is not None and len(row) else None
    if v is None:
        return None
    try:
        if isinstance(v, (float, np.floating)) and np.isnan(v):
            return None
    except TypeError:
        pass
    return v.item() if hasattr(v, "item") else v


@dataclass
class DayContext:
    date: pd.Timestamp
    row: pd.Series
    prev: pd.Series
    b: Bundle
    r7_sets: pd.Series
    fresh: pd.DataFrame
    days_since: dict
    race: dict | None = None

    @classmethod
    def build(cls, b: Bundle, d=None, race: dict | None = None) -> DayContext:
        d = pd.Timestamp(d or b.latest_date() or datetime.now()).normalize()
        row = b.day(d)
        prev = b.day(d - timedelta(days=1))
        r7 = mu.rolling7_sets(b.msets, d) if not b.msets.empty else pd.Series(dtype=float)
        now = datetime.now() if d.date() == datetime.now().date() else d.to_pydatetime() + timedelta(hours=8)
        fresh = mu.freshness(b.msets, b.sets, now) if not b.msets.empty else pd.DataFrame()
        days_since = {}
        if not b.acts.empty:
            for g in ("run", "strength", "swim", "stretch", "breathwork"):
                a = b.acts[(b.acts["sport_group"] == g) & (b.acts["date"] <= d)]
                days_since[g] = (d - a["date"].max()).days if len(a) else None
        if not b.sets.empty:
            last = pd.to_datetime(b.sets.loc[pd.to_datetime(b.sets["date"]) <= d, "date"]).max()
            if pd.notna(last):
                ds = (d - last).days
                days_since["strength"] = min(ds, days_since.get("strength") if days_since.get("strength") is not None else ds)
        return cls(d, row, prev, b, r7, fresh, days_since, race)


# ------------------------------------------------------------------ rules

def r_illness(c: DayContext):
    lvl = _v(c.row, "illness_level")
    if lvl in ("watch", "likely"):
        ev = {"resting_hr": _v(c.row, "rhr"), "rhr_baseline": _v(c.row, "rhr_med"),
              "rhr_z": round(_v(c.row, "rhr_z") or 0, 1),
              "sleep_respiration": _v(c.row, "resp_sleep"), "respiration_baseline": _v(c.row, "resp_sleep_med")}
        strong = lvl == "likely"
        return Recommendation(
            "illness", "health", 0,
            "Your body may be fighting something" if strong else "Resting HR is acutely elevated",
            "Keep today easy: rest, walking or gentle mobility only. Skip intensity and heavy lifting; recheck "
            "tomorrow. If you feel unwell, take a full rest day.",
            f"Resting HR {ev['resting_hr']} bpm vs your baseline {ev['rhr_baseline']:.0f} bpm"
            + (" with corroborating signals (respiration/temperature/HRV)." if strong else "."),
            ev, "strong" if strong else "moderate", "⚠")
    return None


def r_readiness(c: DayContext):
    r = _v(c.row, "readiness")
    if r is None:
        return None
    ev = {"readiness": round(r), "sleep_h": round(_v(c.row, "sleep_h") or 0, 1),
          "resting_hr": _v(c.row, "rhr"), "body_battery_high": _v(c.row, "bb_max")}
    fresh = [m for m in (c.fresh["muscle"][c.fresh["fresh"]] if not c.fresh.empty else []) if m in BIG_MUSCLES]
    under = [m for m, _ in mu.under_target(c.r7_sets, fresh)] if fresh else []
    focus = (under or fresh)[:3]
    focus_txt = ", ".join(mu.MUSCLE_LABELS.get(m, m) for m in focus) if focus else "the groups you trained least recently"
    if _v(c.row, "illness_level") in ("watch", "likely"):
        return None
    if r >= 60:
        return Recommendation("ready_high", "training", 2, "Good day for a hard session",
                              f"Go for quality: heavy strength for {focus_txt}, or an interval/tempo run.",
                              f"Readiness {round(r)}/100 — your recovery markers are at or above your norms.",
                              ev, "heuristic", "▲")
    if r >= 40:
        return Recommendation("ready_mid", "training", 2, "Moderate day",
                              f"Normal-volume strength for {focus_txt}; keep cardio easy (conversational pace).",
                              f"Readiness {round(r)}/100 — mixed recovery signals.", ev, "heuristic", "●")
    return Recommendation("ready_low", "recovery", 1, "Prioritise recovery today",
                          "Easy 20–30 min walk/jog or swim, mobility/stretching, and an early night. Postpone "
                          "heavy or high-intensity work.",
                          f"Readiness {round(r)}/100 — several recovery markers are below your norms.",
                          ev, "heuristic", "▼")


def r_hrv(c: DayContext):
    if not c.b.flags.get("hrv_available"):
        return None
    st = _v(c.row, "hrv_state")
    prev = _v(c.prev, "hrv_state")
    if st == "below" and prev == "below":
        return Recommendation("hrv_low", "recovery", 1, "HRV trend below your normal range",
                              "Reduce intensity until your 7-day HRV returns to your normal band.",
                              "7-day lnRMSSD has been below your smallest-worthwhile-change band for 2+ days "
                              "(HRV-guided training approach).",
                              {"hrv_7d_ln": round(_v(c.row, "hrv_ln7") or 0, 2),
                               "band_low": round(_v(c.row, "hrv_swc_lo") or 0, 2)}, "strong", "▼")
    return None


def r_muscle_targets(c: DayContext):
    if c.r7_sets is None or c.b.msets.empty:
        return None
    trained = set(c.b.msets["muscle"])
    big = [m for m in ("chest", "lats", "upper_back", "quads", "hamstrings", "glutes", "side_delts", "biceps",
                       "triceps", "calves") if m in trained]
    under = mu.under_target(c.r7_sets, big)
    if not under:
        return None
    fresh_set = set(c.fresh.loc[c.fresh["fresh"], "muscle"]) if not c.fresh.empty else set(big)
    cand = [(m, s) for m, s in under if m in fresh_set] or under
    top = cand[:3]
    names = ", ".join(f"{mu.MUSCLE_LABELS.get(m, m)} ({s:.0f})" for m, s in top)
    return Recommendation("muscle_under", "muscle", 3, "Muscles below weekly set target",
                          f"Next strength session: prioritise {names}. Aim for ≥{mu.SETS_LO} hard sets per muscle "
                          "per week.",
                          "Weekly hard sets per muscle show a graded dose-response with hypertrophy "
                          "(Schoenfeld 2017; Pelland meta-regression). Last 7 days shown in brackets.",
                          {m: round(s, 1) for m, s in top}, "strong", "◆")


def r_same_muscle(c: DayContext):
    if c.fresh.empty:
        return None
    sore = c.fresh[~c.fresh["fresh"]]
    if sore.empty:
        return None
    names = ", ".join(mu.MUSCLE_LABELS.get(m, m) for m in sore["muscle"][:4])
    return Recommendation("recent_muscle", "muscle", 3, "Recently trained muscles",
                          f"Avoid hard sets for {names} today (<{mu.FRESH_H} h since last session); train other groups.",
                          "Muscles typically need ~48 h between hard sessions to recover.",
                          {m: round(h) for m, h in zip(sore["muscle"][:4], sore["hours"][:4], strict=False)},
                          "moderate", "◇")


def r_sleep(c: DayContext):
    out = []
    debt = _v(c.row, "sleep_debt7")
    if debt is not None and debt >= 4:
        out.append(Recommendation("sleep_debt", "sleep", 4, "Sleep debt is building",
                                  "Go to bed ~30–45 min earlier for the next few nights.",
                                  f"{debt:.1f} h short of your sleep need over the last 7 nights.",
                                  {"sleep_debt_7d_h": round(debt, 1),
                                   "sleep_need_h": round(_v(c.row, "sleep_need_h") or 7, 1)}, "moderate", "☾"))
    sd = _v(c.row, "bedtime_sd7")
    sri = _v(c.row, "sri7")
    if (sd is not None and sd > 60) or (sri is not None and sri < 70):
        out.append(Recommendation("sleep_regularity", "sleep", 4, "Make sleep timing more regular",
                                  "Pick a fixed wake-up time (also on weekends) and keep bedtime within ±30 min.",
                                  "Sleep regularity predicted mortality more strongly than duration in UK Biobank "
                                  "(Windred 2024).",
                                  {"sleep_regularity_index": round(sri) if sri is not None else None,
                                   "bedtime_sd_min": round(sd) if sd is not None else None}, "strong", "☾"))
    return out


def r_stress(c: DayContext):
    st = _v(c.prev, "stress_avg")
    st_status = _v(c.prev, "stress_avg_status")
    drained = _v(c.prev, "bb_drained")
    charged = _v(c.prev, "bb_charged")
    if st_status in ("amber", "red") or (drained and charged and drained - charged > 25):
        return Recommendation("breathe", "stress", 5, "Take a slow-breathing break",
                              "5–10 min of slow breathing at ~6 breaths/min (e.g. 4 s in, 6 s out), ideally "
                              "mid-afternoon or before bed.",
                              "Slow-paced breathing near 6/min modestly lowers stress and raises HRV (meta-analyses: "
                              "Laborde 2021; Shao 2023).",
                              {"stress_yesterday": st, "body_battery_drained": drained,
                               "body_battery_charged": charged}, "moderate", "≈")
    return None


def r_stretch(c: DayContext):
    ds = c.days_since.get("stretch")
    wk = _v(c.row, "stretch_min_7d") or 0
    if (ds is None or ds >= 4) and wk < 10:
        return Recommendation("stretch", "habit", 6, "Add a short stretching session",
                              "10–15 min of static stretching for the muscles you trained this week.",
                              "≥5–10 min/week of stretching improves range of motion (2024 dose review). It does not "
                              "reliably prevent injury.",
                              {"stretch_min_7d": round(wk), "days_since_stretch": ds}, "moderate", "~")
    return None


def r_load(c: DayContext):
    mono = _v(c.row, "monotony")
    acwr = _v(c.row, "acwr_uncoupled")
    fz = _v(c.row, "form_z")
    if (mono is not None and mono > 2) or (fz is not None and fz < -2):
        return Recommendation("load_monotony", "training", 2, "Training has been monotonous / heavy",
                              "Vary intensity: make today clearly easy or take a rest day.",
                              "High monotony combined with high load is associated with overreaching "
                              "(Foster 1998) — a screening signal, not a diagnosis.",
                              {"monotony": round(mono, 2) if mono else None, "form_z": round(fz, 1) if fz else None},
                              "moderate", "≋")
    if acwr is not None and acwr > 1.5:
        return Recommendation("load_spike", "training", 2, "Load spike vs your recent weeks",
                              "Keep the next 2–3 days lighter to let your body catch up.",
                              "This week's load is >1.5× your previous 3-week average (uncoupled ACWR).",
                              {"acwr_uncoupled": round(acwr, 2)}, "heuristic", "≋")
    return None


def r_steps(c: DayContext):
    s7 = _v(c.row, "steps_7d")
    lo, hi = sp.steps_band(c.b.profile.get("age"))
    if s7 is not None and s7 < lo * 0.85:
        return Recommendation("steps", "habit", 6, "Move a little more",
                              f"Add a 20–30 min walk; target ~{lo:,}–{hi:,} steps/day.",
                              "Mortality benefit of steps plateaus around 8–10k/day under 60 (Paluch 2022).",
                              {"steps_7d_avg": round(s7), "target_low": lo}, "strong", "↗")
    return None


def r_strength_freq(c: DayContext):
    n = _v(c.row, "strength_days_7d")
    if n is not None and n < 2:
        return Recommendation("strength_freq", "habit", 6, "Strength training this week",
                              "Get at least 2 strength sessions in per week.",
                              "WHO recommends muscle-strengthening activity on ≥2 days per week.",
                              {"strength_days_7d": int(n)}, "strong", "◆")
    return None


RULES = [r_illness, r_hrv, r_readiness, r_load, r_muscle_targets, r_same_muscle, r_sleep, r_stress, r_stretch,
         r_steps, r_strength_freq]


def recommend(c: DayContext, limit: int | None = None) -> list[Recommendation]:
    out: list[Recommendation] = []
    for rule in RULES:
        try:
            r = rule(c)
        except Exception:  # noqa: BLE001 - a broken rule must never break the page
            continue
        if r is None:
            continue
        out.extend(r if isinstance(r, list) else [r])
    if c.race:
        from garmin_insights.recommend.race import race_recommendation

        rr = race_recommendation(c)
        if rr:
            out.append(rr)
    out.sort(key=lambda x: (x.priority, x.key))
    return out[:limit] if limit else out
