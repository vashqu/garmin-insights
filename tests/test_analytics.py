import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from garmin_insights.analytics import baselines as bl
from garmin_insights.analytics import load as ld
from garmin_insights.analytics import muscle as mu
from garmin_insights.analytics import recovery as rec
from garmin_insights.analytics import sleep as sl
from garmin_insights.analytics import sports as sp


def cal(values, start="2026-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="D"), dtype=float)


# ------------------------------------------------------------------ baselines
def test_rolling_baseline_excludes_today_and_needs_min_periods():
    s = cal([50.0] * 20 + [60.0])
    b = bl.rolling_baseline(s, window=60, min_periods=14, min_scale=1.0)
    assert np.isnan(b["median"].iloc[13])  # only 13 previous days
    assert b["median"].iloc[-1] == 50.0  # today's 60 not included
    assert b["z"].iloc[-1] == pytest.approx(10 / max(1.0, 0.03 * 50))


@pytest.mark.parametrize("z,direction,expected", [
    (0.2, "down", "green"), (1.0, "down", "amber"), (2.0, "down", "red"),
    (-2.0, "down", "green"), (-2.0, "up", "red"), (1.2, "two", "amber"), (float("nan"), "up", "calibrating"),
])
def test_status_from_z(z, direction, expected):
    assert bl.status_from_z(z, direction) == expected


# ------------------------------------------------------------------ HRV
def test_hrv_swc_band_and_state():
    rng = np.random.default_rng(0)
    base = np.exp(rng.normal(np.log(60), 0.05, 40))
    drop = np.exp(rng.normal(np.log(40), 0.02, 7))
    f = rec.hrv_frame(cal(np.r_[base, drop]))
    last = f.iloc[-1]
    assert last["swc_lo"] < last["base_mu"] < last["swc_hi"]
    assert last["swc_hi"] - last["swc_lo"] == pytest.approx(last["base_sd"])  # ±0.5 SD
    assert last["hrv_state"] == "below"
    assert f["hrv_state"].iloc[30] == "within" or f["hrv_state"].iloc[30] in ("above", "below")


# ------------------------------------------------------------------ illness watch
def test_illness_watch_flags_acute_rise_not_drift():
    z = cal([0.0] * 20 + [2.0, 2.2, 2.4])
    iw = rec.illness_watch(z)
    assert iw["illness_level"].iloc[-1] == "watch"
    drift = cal(list(np.linspace(0, 0.9, 40)))  # slow drift stays below threshold
    assert (rec.illness_watch(drift)["illness_level"] == "none").all()


def test_illness_likely_needs_two_corroborations():
    z = cal([0.0] * 10 + [3.0])
    resp = cal([0.0] * 10 + [1.5])
    temp = cal([0.0] * 10 + [0.7])
    iw = rec.illness_watch(z, resp, temp)
    assert iw["illness_level"].iloc[-1] == "likely"
    iw2 = rec.illness_watch(z, resp, None)
    assert iw2["illness_level"].iloc[-1] == "watch"


# ------------------------------------------------------------------ sleep
def _sleeps(n, jitter_min=0, start="2026-01-01"):
    rows = []
    for i in range(n):
        d = pd.Timestamp(start) + timedelta(days=i)
        j = timedelta(minutes=(jitter_min if i % 2 else 0))
        rows.append({"date": d, "start_local": d - timedelta(hours=1) + j, "end_local": d + timedelta(hours=7) + j})
    return pd.DataFrame(rows)


def test_sri_perfectly_regular_is_100():
    s = _sleeps(10)
    v = sl.sri_for_window(s, pd.Timestamp("2026-01-10"), 7)
    assert v == pytest.approx(100.0)


def test_sri_drops_with_irregular_timing():
    v_reg = sl.sri_for_window(_sleeps(10), pd.Timestamp("2026-01-10"), 7)
    v_irr = sl.sri_for_window(_sleeps(10, jitter_min=120), pd.Timestamp("2026-01-10"), 7)
    assert v_irr < v_reg - 10


def test_sri_missing_nights_returns_nan():
    s = _sleeps(10)
    s = s[~s["date"].isin(pd.to_datetime(["2026-01-07", "2026-01-08"]))]
    assert math.isnan(sl.sri_for_window(s, pd.Timestamp("2026-01-10"), 7, max_missing=1))


def test_sleep_need_bounded_and_debt():
    dur = cal([6.0] * 20)
    need = sl.sleep_need(dur)
    assert need.iloc[-1] == 7.0  # bounded to ≥7 h
    debt = sl.sleep_debt(dur, need)
    assert debt.iloc[-1] == pytest.approx(7.0)


# ------------------------------------------------------------------ load
def test_trimp_banister_male_female():
    t_m = ld.trimp(60, 150, 50, 190, "male")
    hrr = 100 / 140
    assert t_m == pytest.approx(60 * hrr * 0.64 * math.exp(1.92 * hrr))
    assert ld.trimp(60, 150, 50, 190, "female") > t_m
    assert ld.trimp(30, None, 50, 190, "male", "stretch") < ld.trimp(30, None, 50, 190, "male", "run")


def test_load_frame_monotony_and_uncoupled_acwr():
    L = cal([50.0] * 28 + [100.0] * 7)
    f = ld.load_frame(L)
    assert f["acwr_uncoupled"].iloc[-1] == pytest.approx(700 / 350)
    assert np.isnan(f["monotony"].iloc[-1])  # SD zero → undefined, not infinite
    assert f["fitness"].iloc[-1] < f["fatigue"].iloc[-1]  # fatigue responds faster
    assert f["form"].iloc[-1] < 0


# ------------------------------------------------------------------ muscle
def _sets():
    rows = []
    for i, (ex, typ, w, r, rpe) in enumerate([
        ("Bench Press (Barbell)", "warmup", 40, 10, None),
        ("Bench Press (Barbell)", "working", 80, 5, None),
        ("Bench Press (Barbell)", "working", 80, 5, 6.0),  # RPE < 7 → not a hard set
        ("Pull Up", "working", 0, 8, None),
    ]):
        rows.append({"workout_start": "2026-01-05T18:00:00", "exercise": ex, "set_idx": i, "set_type": typ,
                     "weight_kg": w, "reps": r, "rpe": rpe, "seconds": None, "date": "2026-01-05",
                     "workout_duration_s": 3600})
    return pd.DataFrame(rows)


def _emap():
    return pd.DataFrame([
        {"exercise": "Bench Press (Barbell)", "primary_muscles": "chest", "secondary_muscles": "triceps,front_delts"},
        {"exercise": "Pull Up", "primary_muscles": "lats", "secondary_muscles": "biceps"},
    ])


def test_fractional_hard_sets():
    ms = mu.muscle_sets(_sets(), _emap())
    tot = ms.groupby("muscle")["weight"].sum()
    assert tot["chest"] == 1.0 and tot["triceps"] == 0.5 and tot["lats"] == 1.0 and tot["biceps"] == 0.5


def test_epley_and_e1rm():
    assert mu.epley(100, 5) == pytest.approx(116.6667, rel=1e-4)
    h = mu.e1rm_history(_sets())
    assert list(h["exercise"]) == ["Bench Press (Barbell)"]
    assert h["e1rm"].iloc[0] == pytest.approx(80 * (1 + 5 / 30))


def test_freshness_hours():
    ms = mu.muscle_sets(_sets(), _emap())
    fr = mu.freshness(ms, _sets(), now=datetime(2026, 1, 6, 19, 0))
    chest = fr.set_index("muscle").loc["chest"]
    assert chest["hours"] == pytest.approx(24.0) and not chest["fresh"]


# ------------------------------------------------------------------ sports
def test_riegel():
    assert sp.riegel(1200, 5000, 10000) == pytest.approx(1200 * 2 ** 1.06)


def test_css():
    assert sp.css_from_test(360, 165) == pytest.approx(200 / 195)
    assert sp.css_from_test(300, 160) is None  # 200 pace slower than 400 pace → invalid test


def test_ef_decoupling_detects_drift():
    t = np.arange(0, 45 * 60, 1.0)
    hr = np.where(t < t.max() / 2 + 150, 150, 150 * 1.08)
    df = pd.DataFrame({"t": t, "speed": 3.0, "hr": hr})
    r = sp.ef_decoupling(df)
    assert r["decoupling"] == pytest.approx((1 - 1 / 1.08) * 100, rel=0.15)
    assert sp.ef_decoupling(df[df["t"] < 600]) == {}


def test_vo2_median_interp_and_steps_band():
    assert sp.vo2max_median_for_age(25, "male") == 48.0
    assert sp.vo2max_median_for_age(50, "female") == pytest.approx((37.6 + 18.3) / 2)
    assert sp.steps_band(30) == (8000, 10000) and sp.steps_band(65) == (6000, 8000)
