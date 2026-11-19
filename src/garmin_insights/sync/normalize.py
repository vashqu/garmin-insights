"""Convert Garmin payloads (official Personal API shapes, which mirror Garmin Connect's) into table rows.

All functions are pure: payload in, list of row dicts out. Timestamps for intraday series are stored as
*local wall-clock* epoch milliseconds (i.e. naive local time encoded as if UTC) which makes plotting trivial.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from garmin_insights.storage.db import now_iso

SPORT_GROUPS = {
    "run": {"running", "treadmill_running", "trail_running", "track_running", "indoor_running", "virtual_run",
            "street_running", "ultra_run"},
    "strength": {"strength_training", "indoor_cardio", "hiit", "functional_strength_training"},
    "swim": {"lap_swimming", "open_water_swimming", "swimming"},
    "stretch": {"yoga", "pilates", "stretching", "mobility", "flexibility_training"},
    "breathwork": {"breathwork", "meditation", "mindfulness"},
    "walk": {"walking", "hiking", "casual_walking", "speed_walking"},
    "bike": {"cycling", "road_biking", "indoor_cycling", "mountain_biking", "virtual_ride", "gravel_cycling"},
}


def sport_group(type_key: str | None) -> str:
    t = (type_key or "").lower()
    for g, keys in SPORT_GROUPS.items():
        if t in keys:
            return g
    if "run" in t:
        return "run"
    if "swim" in t:
        return "swim"
    if "yoga" in t or "stretch" in t or "pilates" in t:
        return "stretch"
    if "breath" in t:
        return "breathwork"
    return "other"


def _g(d: Any, *path, default=None):
    for p in path:
        if not isinstance(d, dict):
            return default
        d = d.get(p)
    return default if d is None else d


def _local_str_from_local_ms(ms: int | float | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _gmt_str_from_ms(ms: int | float | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.replace("Z", "").split(".")[0]
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _offset_ms(start_local: str | None, start_gmt: str | None) -> int:
    a, b = _parse_ts(start_local), _parse_ts(start_gmt)
    if a and b:
        return int((a - b).total_seconds() * 1000)
    return 0


# ------------------------------------------------------------------ daily

def daily_summaries(items: list[dict], source: str = "official") -> list[dict]:
    rows = []
    for d in items or []:
        st = d.get("stress") or {}
        ms = lambda k, st=st: (st.get(k) / 1000.0) if st.get(k) is not None else None  # noqa: E731
        rows.append({
            "date": d.get("calendarDate"),
            "has_wellness": int(bool(d.get("includesWellnessData"))),
            "steps": _g(d, "movement", "steps", "value"),
            "step_goal": _g(d, "movement", "steps", "goal"),
            "distance_m": _g(d, "movement", "steps", "distanceInMeters"),
            "floors_up": _g(d, "movement", "floorsAscended", "value"),
            "cal_total": _g(d, "calories", "burnedTotal"),
            "cal_active": _g(d, "calories", "burnedActive"),
            "cal_bmr": _g(d, "calories", "burnedResting"),
            "im_moderate": _g(d, "intensityMinutes", "moderate"),
            "im_vigorous": _g(d, "intensityMinutes", "vigorous"),
            "im_goal": _g(d, "intensityMinutes", "goal"),
            "rhr": _g(d, "heartRate", "restingValue"),
            "hr_min": _g(d, "heartRate", "minValue"),
            "hr_max": _g(d, "heartRate", "maxValue"),
            "stress_avg": st.get("avgLevel") if (st.get("avgLevel") or -1) >= 0 else None,
            "stress_max": st.get("maxLevel") if (st.get("maxLevel") or -1) >= 0 else None,
            "stress_rest_s": ms("restDurationInMillis"),
            "stress_low_s": ms("lowStressDurationInMillis"),
            "stress_med_s": ms("mediumStressDurationInMillis"),
            "stress_high_s": ms("highStressDurationInMillis"),
            "stress_activity_s": ms("activityDurationInMillis"),
            "bb_min": _g(d, "bodyBattery", "minValue"),
            "bb_max": _g(d, "bodyBattery", "maxValue"),
            "bb_charged": _g(d, "bodyBattery", "chargedValue"),
            "bb_drained": _g(d, "bodyBattery", "drainedValue"),
            "bb_latest": _g(d, "bodyBattery", "latestValue"),
            "resp_avg": _g(d, "respiration", "avgValue"),
            "resp_min": _g(d, "respiration", "minValue"),
            "resp_max": _g(d, "respiration", "maxValue"),
            "spo2_avg": _g(d, "pulseOx", "avgValue"),
            "spo2_low": _g(d, "pulseOx", "minValue"),
            "source": source, "fetched_at": now_iso(),
        })
    return [r for r in rows if r["date"]]


def user_summary(d: dict, source: str = "official") -> dict:
    """Legacy-shaped single-day summary (also what garminconnect.get_user_summary returns)."""
    if not d:
        return {}
    s = lambda k: (d.get(k) or 0) if d.get(k) is not None else None  # noqa: E731
    return {
        "date": d.get("calendarDate"),
        "has_wellness": int(bool(d.get("includesWellnessData", True))),
        "steps": d.get("totalSteps"), "step_goal": d.get("dailyStepGoal"),
        "distance_m": d.get("totalDistanceMeters"), "floors_up": d.get("floorsAscended"),
        "cal_total": d.get("totalKilocalories"), "cal_active": d.get("activeKilocalories"),
        "cal_bmr": d.get("bmrKilocalories"),
        "im_moderate": d.get("moderateIntensityMinutes"), "im_vigorous": d.get("vigorousIntensityMinutes"),
        "im_goal": d.get("intensityMinutesGoal"),
        "rhr": d.get("restingHeartRate"), "hr_min": d.get("minHeartRate"), "hr_max": d.get("maxHeartRate"),
        "rhr_7d_garmin": d.get("lastSevenDaysAvgRestingHeartRate"),
        "stress_avg": d.get("averageStressLevel") if (d.get("averageStressLevel") or -1) >= 0 else None,
        "stress_max": d.get("maxStressLevel") if (d.get("maxStressLevel") or -1) >= 0 else None,
        "stress_rest_s": s("restStressDuration"), "stress_low_s": s("lowStressDuration"),
        "stress_med_s": s("mediumStressDuration"), "stress_high_s": s("highStressDuration"),
        "stress_activity_s": s("activityStressDuration"),
        "bb_min": d.get("bodyBatteryLowestValue"), "bb_max": d.get("bodyBatteryHighestValue"),
        "bb_charged": d.get("bodyBatteryChargedValue"), "bb_drained": d.get("bodyBatteryDrainedValue"),
        "resp_avg": d.get("avgWakingRespirationValue"), "resp_min": d.get("lowestRespirationValue"),
        "resp_max": d.get("highestRespirationValue"),
        "spo2_avg": d.get("averageSpo2"), "spo2_low": d.get("lowestSpo2"),
        "source": source, "fetched_at": now_iso(),
    }


# ------------------------------------------------------------------ sleep

def sleep(items: list[dict] | dict, source: str = "official") -> list[dict]:
    if isinstance(items, dict):
        items = [items.get("dailySleepDTO", items)]
    rows = []
    for s in items or []:
        if not s or not s.get("calendarDate") or not s.get("sleepTimeSeconds"):
            continue
        scores = s.get("sleepScores") or {}
        overall = scores.get("overall") or {}
        rows.append({
            "date": s["calendarDate"],
            "start_local": _local_str_from_local_ms(s.get("sleepStartTimestampLocal")),
            "end_local": _local_str_from_local_ms(s.get("sleepEndTimestampLocal")),
            "start_gmt": _gmt_str_from_ms(s.get("sleepStartTimestampGMT")),
            "end_gmt": _gmt_str_from_ms(s.get("sleepEndTimestampGMT")),
            "duration_s": s.get("sleepTimeSeconds"),
            "deep_s": s.get("deepSleepSeconds"), "light_s": s.get("lightSleepSeconds"),
            "rem_s": s.get("remSleepSeconds"), "awake_s": s.get("awakeSleepSeconds"),
            "nap_s": s.get("napTimeSeconds"),
            "score": overall.get("value"), "score_qualifier": overall.get("qualifierKey"),
            "awake_count": s.get("awakeCount"),
            "resp_avg": s.get("averageRespirationValue"), "resp_low": s.get("lowestRespirationValue"),
            "resp_high": s.get("highestRespirationValue"),
            "sleep_stress": s.get("avgSleepStress"),
            "spo2_avg": s.get("averageSpO2Value"),
            "skin_temp_dev": s.get("avgSkinTempDeviationC") or s.get("skinTempDeviation"),
            "feedback": s.get("sleepScoreFeedback"),
            "source": source, "fetched_at": now_iso(),
        })
    return rows


# ------------------------------------------------------------------ hrv

def hrv_summaries(payload: dict | list | None, source: str = "official") -> list[dict]:
    if not payload:
        return []
    items = payload.get("hrvSummaries") if isinstance(payload, dict) else payload
    if isinstance(payload, dict) and "hrvSummary" in payload:
        items = [payload["hrvSummary"]]
    rows = []
    for h in items or []:
        b = h.get("baseline") or {}
        rows.append({
            "date": h.get("calendarDate"),
            "rmssd_night": h.get("lastNightAvg"),
            "weekly_avg": h.get("weeklyAvg"),
            "baseline_low": b.get("balancedLow"), "baseline_high": b.get("balancedUpper"),
            "status": h.get("status"),
            "source": source, "fetched_at": now_iso(),
        })
    return [r for r in rows if r["date"] and r["rmssd_night"]]


def hrv_intraday(payload: dict | None) -> list[dict]:
    if not payload:
        return []
    day = (payload.get("hrvSummary") or {}).get("calendarDate")
    rows = []
    for r in payload.get("hrvReadings") or []:
        t = _parse_ts(r.get("readingTimeLocal"))
        if t and day and r.get("hrvValue") is not None:
            rows.append({"date": day, "metric": "hrv",
                         "ts": int(t.replace(tzinfo=UTC).timestamp() * 1000), "value": r["hrvValue"]})
    return rows


# ------------------------------------------------------------------ intraday

def heart_rate_intraday(payload: dict | None) -> list[dict]:
    if not payload or not payload.get("heartRateValues"):
        return []
    day = payload.get("calendarDate")
    off = _offset_ms(payload.get("startTimestampLocal"), payload.get("startTimestampGMT"))
    return [{"date": day, "metric": "hr", "ts": int(ts) + off, "value": v}
            for ts, v in payload["heartRateValues"] if v is not None]


def body_battery(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Returns (intraday rows, daily partial rows with charged/drained)."""
    intr, daily = [], []
    for d in items or []:
        day = d.get("date")
        off = _offset_ms(d.get("startTimestampLocal"), d.get("startTimestampGMT"))
        for pair in d.get("bodyBatteryValuesArray") or []:
            if len(pair) >= 2 and pair[1] is not None:
                intr.append({"date": day, "metric": "bb", "ts": int(pair[0]) + off, "value": pair[1]})
        daily.append({"date": day, "bb_charged": d.get("charged"), "bb_drained": d.get("drained")})
    return intr, daily


def stress_intraday(payload: dict | None) -> list[dict]:
    """garminconnect get_stress_data shape: stressValuesArray [[ts_gmt, level], ...]"""
    if not payload:
        return []
    day = payload.get("calendarDate")
    off = _offset_ms(payload.get("startTimestampLocal"), payload.get("startTimestampGMT"))
    return [{"date": day, "metric": "stress", "ts": int(ts) + off, "value": v}
            for ts, v in payload.get("stressValuesArray") or [] if v is not None and v >= 0]


def spo2_day(payload: dict | None) -> tuple[list[dict], dict | None]:
    if not payload:
        return [], None
    day = payload.get("calendarDate")
    off = _offset_ms(payload.get("startTimestampLocal"), payload.get("startTimestampGMT"))
    intr = [{"date": day, "metric": "spo2", "ts": int(r[0]) + off, "value": r[1]}
            for r in payload.get("spO2HourlyAverages") or [] if len(r) > 1 and r[1] is not None]
    daily = None
    if payload.get("averageSpO2") is not None:
        daily = {"date": day, "spo2_avg": payload.get("averageSpO2"), "spo2_low": payload.get("lowestSpO2")}
    return intr, daily


# ------------------------------------------------------------------ fitness

def vo2max(items: list[dict], source: str = "official") -> list[dict]:
    rows = []
    for it in items or []:
        g = it.get("generic") or {}
        if g.get("calendarDate") and (g.get("vo2MaxPreciseValue") or g.get("vo2MaxValue")):
            rows.append({"date": g["calendarDate"], "value": g.get("vo2MaxValue"),
                         "value_precise": g.get("vo2MaxPreciseValue") or g.get("vo2MaxValue"),
                         "sport": "running", "source": source})
    return rows


def fitness_age(payload: dict | None, day: str, source: str = "official") -> list[dict]:
    if not payload or payload.get("fitnessAge") is None:
        return []
    bmi = _g(payload, "components", "bmi", "value")
    d = (payload.get("lastUpdated") or day)[:10]
    return [{"date": d, "fitness_age": payload.get("fitnessAge"), "chrono_age": payload.get("chronologicalAge"),
             "achievable": payload.get("achievableFitnessAge"), "bmi": bmi, "source": source}]


# ------------------------------------------------------------------ activities

def activity(a: dict, source: str = "official") -> dict:
    tk = _g(a, "activityType", "typeKey")
    start_local = (a.get("startTimeLocal") or "").replace(" ", "T")
    return {
        "activity_id": a.get("activityId"),
        "start_local": start_local,
        "start_gmt": (a.get("startTimeGMT") or "").replace(" ", "T"),
        "date": start_local[:10],
        "type_key": tk, "sport_group": sport_group(tk), "name": a.get("activityName"),
        "duration_s": a.get("duration"), "moving_s": a.get("movingDuration"),
        "distance_m": a.get("distance"),
        "avg_hr": a.get("averageHR"), "max_hr": a.get("maxHR"), "calories": a.get("calories"),
        "aerobic_te": a.get("aerobicTrainingEffect"), "anaerobic_te": a.get("anaerobicTrainingEffect"),
        "garmin_load": a.get("activityTrainingLoad"),
        "avg_speed": a.get("averageSpeed"),
        "avg_cadence": a.get("averageRunningCadenceInStepsPerMinute") or a.get("averageSwimCadenceInStrokesPerMinute"),
        "elev_gain": a.get("elevationGain"), "avg_power": a.get("avgPower"),
        "hr_z1": a.get("hrTimeInZone_1"), "hr_z2": a.get("hrTimeInZone_2"), "hr_z3": a.get("hrTimeInZone_3"),
        "hr_z4": a.get("hrTimeInZone_4"), "hr_z5": a.get("hrTimeInZone_5"),
        "total_sets": a.get("totalSets"), "total_reps": a.get("totalReps"),
        "pool_length_m": (a.get("poolLength") / 100.0 if (a.get("unitOfPoolLength") or {}).get("unitKey") == "centimeter"
                          else a.get("poolLength")),
        "is_pr": int(bool(a.get("pr") or a.get("isPR"))),
        "source": source, "fetched_at": now_iso(),
    }


def activity_extras(a: dict) -> dict:
    """Fields kept in the raw cache but useful for analytics (stress before/after, best splits)."""
    return {k: a.get(k) for k in ("avgStress", "startStress", "endStress", "differenceStress",
                                  "avgRespirationRate", "fastestSplit_1000", "fastestSplit_1609",
                                  "fastestSplit_5000", "fastestSplit_10000", "fastestSplit_21098",
                                  "fastestSplit_42195") if a.get(k) is not None}
