import base64
import json
import time

from garmin_insights.sources import strong
from garmin_insights.sync import normalize

STRONG_CSV = '''"Workout #";"Date";"Workout Name";"Duration (sec)";"Exercise Name";"Set Order";"Weight (kg)";"Reps";"RPE";"Distance (meters)";"Seconds";"Notes";"Workout Notes"
"1";"2025-03-04 18:30:00";"Upper A ";"3000";"Pull Up";"1";"0.0";"8";"";"";"";"";""
"1";"2025-03-04 18:30:00";"Upper A ";"3000";"Pull Up";"Rest Timer";"";"";"";"";"90.0";"";""
"1";"2025-03-04 18:30:00";"Upper A ";"3000";"Seated Row (Cable)";"W";"25.0";"12";"";"";"";"";""
"1";"2025-03-04 18:30:00";"Upper A ";"3000";"Seated Row (Cable)";"1";"60.0";"10";"8";"";"";"";""
'''

LEGACY_CSV = '''Date,Workout Name,Duration,Exercise Name,Set Order,Weight,Weight Unit,Reps,RPE,Distance,Seconds,Notes
2025-01-02 10:00:00,Legs,45m,Squat (Barbell),1,100,lbs,5,,,0,
'''


def test_strong_parse_semicolon_skips_rest_and_marks_warmup():
    rows = strong.parse_csv(STRONG_CSV)
    assert len(rows) == 3
    assert [r["set_type"] for r in rows] == ["working", "warmup", "working"]
    row = rows[-1]
    assert row["weight_kg"] == 60.0 and row["reps"] == 10 and row["rpe"] == 8
    assert row["workout_duration_s"] == 3000 and row["date"] == "2025-03-04"
    assert rows[1]["set_idx"] == 1 and rows[2]["set_idx"] == 2


def test_strong_parse_legacy_lbs_and_duration():
    rows = strong.parse_csv(LEGACY_CSV)
    assert rows[0]["weight_kg"] == 100 * strong.LB
    assert rows[0]["workout_duration_s"] == 45 * 60


def test_strong_import_and_match(tmp_data):
    from garmin_insights.storage import db

    db.upsert("activity", [{"activity_id": 1, "start_local": "2025-03-04T18:25:00", "date": "2025-03-04",
                            "duration_s": 2800, "sport_group": "strength"}])
    res = strong.import_text(STRONG_CSV)
    assert res == {"sets": 3, "workouts": 1, "matched_workouts": 1}
    assert strong.unmapped_exercises() == []


def test_lookup_muscles_strips_equipment():
    m = strong.default_exercise_map()
    assert strong.lookup_muscles("Lat Pulldown (Machine)", m) is not None
    assert strong.lookup_muscles("Totally Unknown Move", m) is None


def test_normalize_daily_summary():
    item = {"calendarDate": "2025-03-05", "includesWellnessData": True,
            "movement": {"steps": {"goal": 10000, "value": 9123, "distanceInMeters": 7300.0},
                         "floorsAscended": {"value": 3}},
            "calories": {"burnedResting": 2100, "burnedActive": 420, "burnedTotal": 2520},
            "heartRate": {"minValue": 48, "maxValue": 140, "restingValue": 51},
            "intensityMinutes": {"goal": 150, "moderate": 10, "vigorous": 5},
            "stress": {"avgLevel": 27, "maxLevel": 75, "restDurationInMillis": 30000000},
            "bodyBattery": {"minValue": 30, "maxValue": 90}, "respiration": {"avgValue": 13}, "pulseOx": {}}
    r = normalize.daily_summaries([item])[0]
    assert r["steps"] == 9123 and r["rhr"] == 51 and r["stress_rest_s"] == 30000 and r["bb_max"] == 90
    assert r["spo2_avg"] is None


def test_normalize_sleep_local_times():
    s = {"calendarDate": "2025-03-05", "sleepTimeSeconds": 26400, "sleepStartTimestampLocal": 1741131000000,
         "sleepEndTimestampLocal": 1741158600000, "deepSleepSeconds": 5400, "sleepScores": {"overall": {"value": 80}}}
    r = normalize.sleep([s])[0]
    assert r["start_local"] == "2025-03-04T23:30:00" and r["score"] == 80


def test_normalize_intraday_offset():
    p = {"calendarDate": "2025-03-05", "startTimestampLocal": "2025-03-05T00:00:00.0",
         "startTimestampGMT": "2025-03-04T22:00:00.0", "heartRateValues": [[1741125600000, 60], [1741125720000, None]]}
    rows = normalize.heart_rate_intraday(p)
    assert len(rows) == 1 and rows[0]["ts"] == 1741125600000 + 2 * 3600 * 1000


def test_activity_sport_group():
    a = {"activityId": 5, "activityType": {"typeKey": "lap_swimming"}, "startTimeLocal": "2026-01-01 07:00:00",
         "poolLength": 2500, "unitOfPoolLength": {"unitKey": "centimeter"}}
    r = normalize.activity(a)
    assert r["sport_group"] == "swim" and r["pool_length_m"] == 25 and r["date"] == "2026-01-01"


def test_official_token_cached_and_auth_error(tmp_data, monkeypatch):
    import httpx

    from garmin_insights.sources import official

    exp = time.time() + 86400
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    tok = f"h.{payload}.s"
    calls = {"n": 0}

    def handler(request):
        if request.url.path.endswith("/token"):
            calls["n"] += 1
            return httpx.Response(200, text=tok)
        return httpx.Response(200, json={"data": []})

    c = official.OfficialClient(api_key="k", delay_s=0)
    c._http = httpx.Client(transport=httpx.MockTransport(handler))
    assert c.daily_summaries(__import__("datetime").date(2026, 1, 1), __import__("datetime").date(2026, 1, 2)) == []
    c.daily_summaries(__import__("datetime").date(2026, 1, 1), __import__("datetime").date(2026, 1, 2))
    assert calls["n"] == 1

    bad = official.OfficialClient(api_key="k", delay_s=0)
    bad._token_file = tmp_data / "none.json"
    bad._http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(406)))
    import pytest

    with pytest.raises(official.AuthError):
        bad.token()
    with pytest.raises(official.AuthError):  # cached failure, no second request storm
        bad.token()


def test_auto_sync_respects_min_interval(tmp_data, monkeypatch):
    from datetime import datetime, timedelta

    from garmin_insights.storage import db
    from garmin_insights.sync import engine

    started = []
    monkeypatch.setattr(engine.threading, "Thread", lambda **kw: type("T", (), {"start": lambda s: started.append(1)})())
    db.set_state("last_sync_at", (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds"))
    assert engine.run_sync_background(auto=True) is False and not started
    db.set_state("last_sync_at", (datetime.now() - timedelta(hours=3)).isoformat(timespec="seconds"))
    assert engine.run_sync_background(auto=True) is True and started
    engine._lock.release()


def test_due_markers(tmp_data):
    from garmin_insights.sync import engine

    assert engine._due("x", 24)
    engine._mark("x")
    assert not engine._due("x", 24) and engine._due("x", 0)


def test_rate_limited_aborts(tmp_data, monkeypatch):
    import httpx
    import pytest

    from garmin_insights.sources import official

    c = official.OfficialClient(api_key="k", delay_s=0)
    c.token = lambda force=False: "t"
    c._http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429)))
    monkeypatch.setattr(official.time, "sleep", lambda s: None)
    with pytest.raises(official.RateLimited):
        c.request("/x")
    assert c.total_calls == 3
