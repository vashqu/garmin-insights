"""Synthetic but realistic data (≈400 days) for tests and demos — never touches real data unless asked."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np

from garmin_insights.storage import db, journal

EXERCISES = {
    "Push": ["Bench Press (Barbell)", "Incline Bench Press (Barbell)", "Shoulder Press (Machine)",
             "Lateral Raise (Dumbbell)", "Triceps Extension"],
    "Pull": ["Pull Up", "Seated Row (Cable)", "T Bar Row", "Face Pull (Cable)", "Bicep Curl (Dumbbell)"],
    "Legs": ["Squat (Barbell)", "Romanian Deadlift (Barbell)", "Leg Extension (Machine)", "Lying Leg Curl (Machine)",
             "Seated Calf Raise (Machine)"],
}


def generate(days: int = 400, seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    end = date.today()
    start = end - timedelta(days=days - 1)
    daily, sleep, hrv, intr, acts, sets, jr = [], [], [], [], [], [], []
    rhr_base, fitness = 56.0, 0.0
    ill_days = {start + timedelta(days=int(x)) for x in rng.choice(range(30, days - 5), 3, replace=False)}
    ill = 0
    aid = 9_000_000
    split = 0
    for i in range(days):
        d = start + timedelta(days=i)
        if d in ill_days:
            ill = 4
        alcohol = rng.random() < 0.12
        late_caf = rng.random() < 0.2
        trained = rng.random() < 0.55
        rhr = rhr_base - 0.004 * i + rng.normal(0, 1.4) + (5 if ill > 0 else 0) + (2 if alcohol else 0)
        ill = max(0, ill - 1)
        bed = datetime.combine(d - timedelta(days=1), datetime.min.time()) + timedelta(
            hours=23, minutes=float(rng.normal(15, 35) + (40 if late_caf else 0)))
        dur = float(np.clip(rng.normal(7.3, 0.6) - (0.5 if alcohol else 0) - (0.3 if late_caf else 0), 4.5, 9.5))
        wake = bed + timedelta(hours=dur + 0.3)
        score = float(np.clip(70 + (dur - 7) * 12 - (8 if alcohol else 0) + rng.normal(0, 5), 30, 99))
        rmssd = float(np.exp(np.log(55) + rng.normal(0, 0.12) - (0.25 if alcohol else 0) - (0.3 if ill else 0)))
        stress = float(np.clip(rng.normal(32, 7) + (6 if alcohol else 0), 10, 80))
        steps = int(np.clip(rng.normal(8500, 2500), 1500, 25000))
        bbmax = float(np.clip(70 + (dur - 7) * 8 - (10 if alcohol else 0) + rng.normal(0, 6), 20, 100))
        daily.append({"date": d.isoformat(), "has_wellness": 1, "steps": steps, "step_goal": 8000,
                      "distance_m": steps * 0.78, "floors_up": int(rng.integers(0, 20)),
                      "cal_total": 2400 + steps * 0.04, "cal_active": steps * 0.04, "cal_bmr": 2300,
                      "im_moderate": int(rng.integers(0, 40)), "im_vigorous": int(rng.integers(0, 25)), "im_goal": 150,
                      "rhr": round(rhr), "hr_min": round(rhr) - 4, "hr_max": 150, "stress_avg": round(stress),
                      "stress_rest_s": 30000, "stress_low_s": 25000, "stress_med_s": 9000, "stress_high_s": 2000,
                      "bb_min": max(5, bbmax - 60), "bb_max": round(bbmax), "bb_charged": 60, "bb_drained": 62,
                      "resp_avg": 14, "source": "demo"})
        sleep.append({"date": d.isoformat(), "start_local": bed.strftime("%Y-%m-%dT%H:%M:%S"),
                      "end_local": wake.strftime("%Y-%m-%dT%H:%M:%S"), "duration_s": dur * 3600,
                      "deep_s": dur * 3600 * 0.18, "light_s": dur * 3600 * 0.55, "rem_s": dur * 3600 * 0.22,
                      "awake_s": 900, "score": round(score), "resp_avg": float(13.5 + (1.4 if ill else 0) + rng.normal(0, .3)),
                      "sleep_stress": 15, "source": "demo"})
        hrv.append({"date": d.isoformat(), "rmssd_night": round(rmssd, 1), "source": "demo"})
        if i >= days - 3:
            for h in range(0, 24 * 60, 10):
                ts = int(datetime.combine(d, datetime.min.time()).timestamp() * 1000) + h * 60000
                intr.append({"date": d.isoformat(), "metric": "hr", "ts": ts, "value": float(rhr + 20 + rng.normal(0, 8))})
                intr.append({"date": d.isoformat(), "metric": "bb", "ts": ts, "value": float(max(5, bbmax - h / 30))})
        if trained and not ill:
            kind = ["Push", "Pull", "Legs"][split % 3] if rng.random() < 0.6 else "run"
            st = datetime.combine(d, datetime.min.time()) + timedelta(hours=18)
            aid += 1
            if kind == "run":
                dist = float(rng.normal(7000, 2000))
                dur_s = dist / 2.9
                acts.append({"activity_id": aid, "start_local": st.isoformat(), "date": d.isoformat(),
                             "type_key": "running", "sport_group": "run", "duration_s": dur_s, "moving_s": dur_s,
                             "distance_m": dist, "avg_hr": 150, "max_hr": 172, "avg_speed": 2.9, "aerobic_te": 3.1,
                             "anaerobic_te": 1.0, "hr_z2": dur_s * 0.5, "hr_z3": dur_s * 0.5, "source": "demo"})
            else:
                split += 1
                acts.append({"activity_id": aid, "start_local": st.isoformat(), "date": d.isoformat(),
                             "type_key": "strength_training", "sport_group": "strength", "duration_s": 3300,
                             "avg_hr": 108, "max_hr": 150, "source": "demo"})
                for ex in EXERCISES[kind]:
                    w = 20 + 2 * (EXERCISES[kind].index(ex) + 1) * 5 + i * 0.03
                    for k in range(1, 4):
                        sets.append({"workout_start": st.isoformat(), "exercise": ex, "set_idx": k,
                                     "workout_name": f"{kind} day", "workout_duration_s": 3000, "set_type": "working",
                                     "weight_kg": round(w, 1) if ex != "Pull Up" else 0, "reps": int(rng.integers(6, 12)),
                                     "date": d.isoformat(), "matched_activity_id": aid, "source": "demo"})
        if rng.random() < 0.15:
            acts.append({"activity_id": aid + 500000, "start_local": (datetime.combine(d, datetime.min.time())
                                                                     + timedelta(hours=21)).isoformat(),
                         "date": d.isoformat(), "type_key": "yoga", "sport_group": "stretch", "duration_s": 900,
                         "avg_hr": 80, "source": "demo"})
        if rng.random() < 0.7:
            if alcohol:
                jr.append({"date": (d - timedelta(days=1)).isoformat(), "tag": "alcohol", "value": float(rng.integers(1, 4))})
            if late_caf:
                jr.append({"date": (d - timedelta(days=1)).isoformat(), "tag": "late_caffeine", "value": 1.0})
            if rng.random() < 0.5:
                jr.append({"date": (d - timedelta(days=1)).isoformat(), "tag": "protein_target", "value": 1.0})
        fitness += 0
    db.upsert("daily", daily)
    db.upsert("sleep", sleep)
    db.upsert("hrv", hrv)
    db.upsert("intraday", intr)
    db.upsert("activity", acts)
    db.upsert("strength_set", sets)
    db.upsert("journal", jr)
    db.upsert("vo2max", [{"date": (start + timedelta(days=k)).isoformat(), "value": 46, "value_precise": 45 + k / 200,
                          "source": "demo"} for k in range(0, days, 7)])
    db.set_profile(chrono_age=30, max_hr=190)
    from garmin_insights.sources.strong import ensure_exercise_map

    ensure_exercise_map()
    journal.ensure_defaults()
    db.set_state("last_sync_at", datetime.now().isoformat(timespec="seconds"))
    return {"days": days, "activities": len(acts), "sets": len(sets), "journal": len(jr)}
