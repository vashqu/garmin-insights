"""Builds the analysis bundle used by every page: one calendar-indexed daily frame with all derived
metrics + activities, strength sets and load. Cached per data version (bumped after sync/import/journal edits)."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from garmin_insights import config
from garmin_insights.analytics import baselines as bl
from garmin_insights.analytics import load as ld
from garmin_insights.analytics import muscle as mu
from garmin_insights.analytics import recovery as rec
from garmin_insights.analytics import sleep as sl
from garmin_insights.analytics.metrics import M
from garmin_insights.storage import db

log = logging.getLogger(__name__)

READINESS_WEIGHTS = {"hrv": 0.35, "rhr": 0.20, "sleep": 0.15, "sri": 0.10, "stress": 0.10, "bb": 0.10}


@dataclass
class Bundle:
    daily: pd.DataFrame
    acts: pd.DataFrame
    sets: pd.DataFrame
    msets: pd.DataFrame
    emap: pd.DataFrame
    sleeps: pd.DataFrame
    journal: pd.DataFrame
    profile: dict = field(default_factory=dict)
    flags: dict = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return self.daily.empty

    def day(self, d) -> pd.Series:
        d = pd.Timestamp(d).normalize()
        if d in self.daily.index:
            return self.daily.loc[d]
        return pd.Series(dtype=object)

    def latest_date(self) -> pd.Timestamp | None:
        if self.daily.empty:
            return None
        have = self.daily.index[self.daily["has_data"]]
        return have.max() if len(have) else self.daily.index.max()


def data_version() -> int:
    return int(db.get_state("data_version", 0) or 0)


def bump_version() -> None:
    db.set_state("data_version", data_version() + 1)
    _cache.clear()


_cache: dict[int, Bundle] = {}
_cache_lock = threading.Lock()


def get_bundle() -> Bundle:
    v = data_version()
    with _cache_lock:
        if v not in _cache:
            _cache.clear()
            _cache[v] = build()
        return _cache[v]


def _profile(settings: config.Settings) -> dict:
    p = db.get_profile()
    sex = settings.sex or p.get("sex") or ""
    age = None
    if settings.birth_year:
        age = date.today().year - settings.birth_year
    elif p.get("birth_date"):
        age = date.today().year - int(str(p["birth_date"])[:4])
    else:
        fa = db.query("SELECT chrono_age FROM fitness_age WHERE chrono_age IS NOT NULL ORDER BY date DESC LIMIT 1")
        age = fa[0]["chrono_age"] if fa else p.get("chrono_age")
    max_hr = settings.max_hr_override or p.get("max_hr") or (round(208 - 0.7 * age) if age else 190)
    return {"sex": sex or "male", "sex_set": bool(sex), "age": age, "max_hr": max_hr,
            "rhr_profile": settings.rhr_override or p.get("rhr_zones"), "raw": p}


def build() -> Bundle:
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pd.errors.PerformanceWarning)
        return _build()


def _build() -> Bundle:
    settings = config.Settings.load()
    prof = _profile(settings)
    daily = db.query_df("SELECT * FROM daily WHERE has_wellness=1 OR has_wellness IS NULL")
    sleeps = db.query_df("SELECT * FROM sleep")
    hrv = db.query_df("SELECT * FROM hrv")
    acts = db.query_df("SELECT * FROM activity ORDER BY start_local")
    sets = db.query_df("SELECT * FROM strength_set ORDER BY workout_start")
    emap = db.query_df("SELECT * FROM exercise_map")
    journal = db.query_df("SELECT * FROM journal")
    vo2 = db.query_df("SELECT date, value_precise AS vo2max FROM vo2max")
    fa = db.query_df("SELECT date, fitness_age, chrono_age FROM fitness_age")

    all_dates = [x for x in (daily.get("date"), sleeps.get("date"), acts.get("date"), sets.get("date")) if x is not None]
    all_dates = pd.to_datetime(pd.concat(all_dates)) if all_dates else pd.Series(dtype="datetime64[ns]")
    if all_dates.empty:
        empty = pd.DataFrame(index=pd.DatetimeIndex([], name="date"))
        return Bundle(empty, acts, sets, pd.DataFrame(), emap, sleeps, journal, prof, {})
    garmin_dates = pd.to_datetime(daily["date"]) if not daily.empty else all_dates
    start, end = garmin_dates.min(), max(all_dates.max(), pd.Timestamp(date.today()))
    D = bl.to_calendar(daily, start=start, end=end)
    D["has_data"] = D["steps"].notna() | D["rhr"].notna()

    # --- sleep
    S = bl.to_calendar(sleeps, start=start, end=end) if not sleeps.empty else pd.DataFrame(index=D.index)
    for c in ("duration_s", "deep_s", "light_s", "rem_s", "awake_s", "score", "resp_avg", "sleep_stress",
              "skin_temp_dev", "spo2_avg", "start_local", "end_local"):
        D["sl_" + c] = S[c] if c in S else np.nan
    D["has_data"] |= D["sl_duration_s"].notna()
    D["sleep_h"] = D["sl_duration_s"] / 3600
    D["sleep_score"] = D["sl_score"]
    D["resp_sleep"] = D["sl_resp_avg"]
    D["skin_temp"] = D["sl_skin_temp_dev"]
    D["spo2"] = D["spo2_avg"].fillna(D["sl_spo2_avg"]) if "spo2_avg" in D else D["sl_spo2_avg"]
    D["sleep_need_h"] = sl.sleep_need(D["sleep_h"])
    D["sleep_debt7"] = sl.sleep_debt(D["sleep_h"], D["sleep_need_h"])
    if not sleeps.empty:
        D["sri7"] = sl.sri_series(sleeps, D.index, 7)
        D["sri30"] = sl.sri_series(sleeps, D.index, 30) if len(sleeps) >= 20 else np.nan
        t = sl.timing_sd(D["sl_start_local"], D["sl_end_local"])
        for c in t:
            D[c] = t[c]
    else:
        D["sri7"] = D["sri30"] = D["bedtime_sd7"] = D["wake_sd7"] = np.nan
        D["bedtime_min"] = D["wake_min"] = np.nan

    D = D.copy()
    # --- HRV
    if not hrv.empty:
        H = bl.to_calendar(hrv, start=start, end=end)
        hf = rec.hrv_frame(H["rmssd_night"])
        for c in hf:
            D["hrv_" + c if not c.startswith("hrv") else c] = hf[c]
        D["hrv_night"] = H["rmssd_night"]
        D["hrv_ln7"] = hf["ln7"]
        D["hrv_garmin_status"] = H["status"]
        hrv_avail = bool(H["rmssd_night"].notna().any())
    else:
        hrv_avail = False
        for c in ("hrv_night", "hrv_ln7", "hrv_z", "hrv_swc_lo", "hrv_swc_hi", "hrv_cv7", "hrv_base_mu"):
            D[c] = np.nan
        D["hrv_state"] = "unavailable"

    # --- baselines + statuses
    status_keys = ["rhr", "sleep_h", "sleep_score", "sri7", "stress_avg", "bb_max", "resp_sleep", "spo2",
                   "skin_temp", "steps", "sleep_debt7", "bedtime_sd7"]
    if hrv_avail:
        status_keys.append("hrv_night")
    for k in status_keys:
        m = M[k]
        b = bl.rolling_baseline(D[k].astype(float), min_scale=m.min_scale)
        D[k + "_med"], D[k + "_scale"], D[k + "_z"] = b["median"], b["scale"], b["z"]
        D[k + "_status"] = bl.status_series(b, m.direction)
    if hrv_avail:
        D["hrv_ln7_status"] = D["hrv_state"].map({"below": "red", "within": "green", "above": "green",
                                                  "calibrating": "calibrating"}).fillna("nodata")
        # amber when within band but trending down >0.25 SD below mean
        D.loc[(D["hrv_state"] == "within") & (D["hrv_z"] < -0.25), "hrv_ln7_status"] = "amber"
    # sleep duration status vs need instead of vs own median
    short = D["sleep_need_h"] - D["sleep_h"]
    D["sleep_h_status"] = np.select([D["sleep_h"].isna(), short <= 0.25, short <= 1.0],
                                    ["nodata", "green", "amber"], default="red")

    D = D.copy()
    # --- illness watch
    resp_delta = D["resp_sleep"] - D["resp_sleep_med"]
    temp = D["skin_temp"] - D["skin_temp_med"].fillna(0) if D["skin_temp"].notna().any() else None
    iw = rec.illness_watch(D["rhr_z"], resp_delta, temp, D["hrv_state"] if hrv_avail else None)
    for c in iw:
        D[c] = iw[c]

    # --- activities & load
    if not acts.empty:
        acts["date"] = pd.to_datetime(acts["date"])
        rhr_by = D["rhr"].ffill().bfill()
        acts["trimp"] = ld.activity_loads(acts, rhr_by, prof["max_hr"], prof["sex"],
                                          rhr_default=prof.get("rhr_profile") or 60)
        acts["end_local"] = pd.to_datetime(acts["start_local"]) + pd.to_timedelta(acts["duration_s"].fillna(0), "s")
        daily_load = acts.groupby("date")["trimp"].sum().reindex(D.index).fillna(0)
        by_sport = acts.pivot_table(index="date", columns="sport_group", values="duration_s", aggfunc="sum") / 60
        for c in by_sport:
            D["min_" + c] = by_sport[c].reindex(D.index).fillna(0)
        last_end = acts.groupby("date")["end_local"].max().reindex(D.index)
        D["last_workout_end_h"] = last_end.dt.hour + last_end.dt.minute / 60
    else:
        daily_load = pd.Series(0.0, index=D.index)
    for c in ("min_run", "min_strength", "min_swim", "min_stretch", "min_walk", "min_breathwork"):
        if c not in D:
            D[c] = 0.0
    L = ld.load_frame(daily_load)
    for c in L:
        D[c] = L[c]
    D["form_z"] = (D["form"] - D["form"].expanding(21).mean()) / D["form"].expanding(21).std()

    # --- strength
    if not sets.empty:
        ms = mu.muscle_sets(sets, emap)
        sdays = mu.strength_days(sets, acts)
    else:
        ms, sdays = pd.DataFrame(), mu.strength_days(sets, acts)
    D["strength_day"] = sdays.reindex(D.index).fillna(0)
    if not ms.empty:
        hard = ms.groupby("date")["weight"].sum().reindex(D.index).fillna(0)
        D["hard_sets"] = hard
    else:
        D["hard_sets"] = 0.0

    D = D.copy()
    # --- longevity series
    im = D["im_moderate"].fillna(0) + 2 * D["im_vigorous"].fillna(0)
    D["im_week"] = im.rolling(7, min_periods=1).sum()
    D["steps_7d"] = D["steps"].rolling(7, min_periods=3).mean()
    D["strength_days_7d"] = D["strength_day"].rolling(7, min_periods=1).sum()
    D["stretch_min_7d"] = D["min_stretch"].rolling(7, min_periods=1).sum()
    if not vo2.empty:
        D["vo2max"] = bl.to_calendar(vo2, start=start, end=end)["vo2max"].ffill()
    else:
        D["vo2max"] = np.nan
    if not fa.empty:
        F = bl.to_calendar(fa, start=start, end=end)
        D["fitness_age"] = F["fitness_age"].ffill()
    else:
        D["fitness_age"] = np.nan

    D = D.copy()
    # --- readiness
    comps = {
        "hrv": D["hrv_z"] if hrv_avail else pd.Series(np.nan, index=D.index),
        "rhr": -D["rhr_z"],
        "sleep": ((D["sleep_h"] - D["sleep_need_h"]) / D["sleep_h_scale"].clip(lower=0.33)),
        "sri": D["sri7_z"],
        "stress": -D["stress_avg_z"].shift(1),
        "bb": D["bb_max_z"],
    }
    C = pd.DataFrame(comps).clip(-2.5, 2.5)
    W = pd.DataFrame({k: np.where(C[k].notna(), w, 0.0) for k, w in READINESS_WEIGHTS.items()}, index=D.index)
    wsum = W.sum(axis=1)
    wz = (C.fillna(0) * W).sum(axis=1) / wsum.replace(0, np.nan)
    score = 50 + 20 * wz
    mods = pd.Series(0.0, index=D.index)
    mods = mods - np.where(D["illness_level"] == "likely", 10, np.where(D["illness_level"] == "watch", 5, 0))
    mods = mods - np.where((D["form_z"] < -2) | (D["monotony"] > 2), 5, 0)
    D["readiness"] = (score + mods).clip(0, 100).where(wsum >= 0.45).round(0)
    for k in READINESS_WEIGHTS:
        D["rc_" + k] = (C[k] * W[k] / wsum.replace(0, np.nan) * 20).where(C[k].notna())
    D["rc_mods"] = mods
    rb = bl.rolling_baseline(D["readiness"], min_scale=5)
    D["readiness_status"] = np.select([D["readiness"].isna(), D["readiness"] >= 60, D["readiness"] >= 40],
                                      ["calibrating", "green", "amber"], default="red")
    D["readiness_med"] = rb["median"]

    D = D.copy()
    flags = {"hrv_available": hrv_avail,
             "spo2_available": bool(D["spo2"].notna().any()),
             "skin_temp_available": bool(D["skin_temp"].notna().any()),
             "sex_set": prof["sex_set"],
             "history_days": int(D["has_data"].sum())}
    return Bundle(D, acts, sets, ms, emap, sleeps, journal, prof, flags)
