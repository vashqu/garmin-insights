"""Registry of user-facing metrics: label, unit, direction, evidence level and plain-language 'why'."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str
    direction: str  # up | down | two
    fmt: str = "{:.0f}"
    evidence: str = "moderate"  # strong | moderate | heuristic
    why: str = ""
    min_scale: float = 0.0
    group: str = "recovery"


M = {m.key: m for m in [
    Metric("hrv_ln7", "HRV (7-day lnRMSSD)", "ln ms", "up", "{:.2f}", "strong",
           "7-day rolling average of log-transformed overnight RMSSD compared with your smallest worthwhile change "
           "(±0.5 SD of your 60-day baseline). Recommended by the AIS HRV best-practice guide (Plews/Buchheit); "
           "single nights are too noisy to act on.", 0.03),
    Metric("hrv_night", "HRV last night", "ms", "up", "{:.0f}", "strong", "Overnight average RMSSD.", 2),
    Metric("rhr", "Resting HR", "bpm", "down", "{:.0f}", "strong",
           "Resting heart rate vs your own 60-day baseline. Sustained elevations precede illness in wearable "
           "studies (Mishra 2020; Alavi 2022) and lower long-term RHR tracks better aerobic fitness.", 1.0),
    Metric("sleep_h", "Sleep duration", "h", "up", "{:.1f}", "strong",
           "Total sleep vs your personal need (your 60-day median, bounded to the 7–9 h adult range). "
           "Athlete sleep needs are individual (Walsh et al., BJSM 2021 consensus).", 0.25, "sleep"),
    Metric("sleep_score", "Sleep score", "", "up", "{:.0f}", "moderate",
           "Garmin's sleep score (duration, stages, stress, awakenings).", 3, "sleep"),
    Metric("sri7", "Sleep regularity (7d)", "SRI", "up", "{:.0f}", "strong",
           "Sleep Regularity Index: probability of being in the same sleep/wake state 24 h apart (−100…100). "
           "In UK Biobank, regularity predicted mortality more strongly than duration (Windred et al., SLEEP 2024).",
           3, "sleep"),
    Metric("sleep_debt7", "Sleep debt (7d)", "h", "down", "{:.1f}", "moderate",
           "Sum of nightly shortfalls vs your sleep need over the last 7 nights.", 0.5, "sleep"),
    Metric("bedtime_sd7", "Bedtime variability", "min", "down", "{:.0f}", "moderate",
           "Standard deviation of bedtime over 7 nights. Consistent timing supports circadian health.", 10, "sleep"),
    Metric("stress_avg", "Avg stress", "", "down", "{:.0f}", "moderate",
           "Garmin all-day stress (HRV-derived, 0–100).", 3, "stress"),
    Metric("bb_max", "Body Battery high", "", "up", "{:.0f}", "heuristic",
           "Highest Body Battery of the day (usually the morning value) — Garmin's proprietary energy estimate.",
           5, "stress"),
    Metric("resp_sleep", "Sleep respiration", "brpm", "two", "{:.1f}", "moderate",
           "Average breathing rate during sleep. A rise of ~1+ breaths/min above your norm can accompany "
           "illness or strain.", 0.4),
    Metric("spo2", "SpO2", "%", "up", "{:.0f}", "moderate", "Average blood-oxygen saturation.", 1),
    Metric("skin_temp", "Skin temp deviation", "°C", "two", "{:+.1f}", "moderate",
           "Overnight skin temperature vs your baseline.", 0.2),
    Metric("steps", "Steps", "", "up", "{:,.0f}", "strong",
           "Mortality benefit plateaus at ~8–10k steps/day under 60 and 6–8k at 60+ (Paluch et al., "
           "Lancet Public Health 2022).", 800, "activity"),
    Metric("readiness", "Readiness", "/100", "up", "{:.0f}", "heuristic",
           "The app's own composite of HRV, resting HR, sleep, regularity, stress and Body Battery vs YOUR norms. "
           "Inputs are evidence-based; the weighting is a transparent heuristic.", 5),
]}


def get(key: str) -> Metric:
    return M[key]


def fmt(key: str, v) -> str:
    if v is None:
        return "–"
    try:
        import math

        if isinstance(v, float) and math.isnan(v):
            return "–"
        return M[key].fmt.format(v) if key in M else f"{v}"
    except (ValueError, TypeError):
        return str(v)
