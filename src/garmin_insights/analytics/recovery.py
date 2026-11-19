"""HRV (lnRMSSD 7-day vs SWC) and illness-watch (CuSum on resting HR + corroborating signals)."""

from __future__ import annotations

import numpy as np
import pandas as pd

# ------------------------------------------------------------------ HRV

def hrv_frame(rmssd: pd.Series, baseline_days: int = 60, min_days: int = 14) -> pd.DataFrame:
    """rmssd: calendar-indexed nightly RMSSD (ms).

    ln7     = 7-day rolling mean of ln(RMSSD) (needs ≥4 of 7 nights)
    base_mu = rolling `baseline_days` mean of daily ln values (excluding today)
    base_sd = rolling SD of daily ln values
    swc_lo/hi = base_mu ∓ 0.5·base_sd   (smallest worthwhile change; AIS guide / Plews)
    cv7     = coefficient of variation (%) of ln over last 7 nights (rising CV + falling mean → maladaptation)
    """
    ln = np.log(rmssd.where(rmssd > 0))
    ln7 = ln.rolling(7, min_periods=4).mean()
    prev = ln.shift(1)
    mu = prev.rolling(baseline_days, min_periods=min_days).mean()
    sd = prev.rolling(baseline_days, min_periods=min_days).std()
    cv7 = ln.rolling(7, min_periods=4).std() / ln7 * 100
    out = pd.DataFrame({"rmssd": rmssd, "ln": ln, "ln7": ln7, "base_mu": mu, "base_sd": sd,
                        "swc_lo": mu - 0.5 * sd, "swc_hi": mu + 0.5 * sd, "cv7": cv7})
    out["hrv_z"] = (ln7 - mu) / sd.replace(0, np.nan)
    out["hrv_state"] = np.select(
        [out["ln7"].isna() | out["base_mu"].isna(), out["ln7"] < out["swc_lo"], out["ln7"] > out["swc_hi"]],
        ["calibrating", "below", "above"], default="within")
    return out


# ------------------------------------------------------------------ illness watch

def cusum(z: pd.Series, k: float = 0.5) -> pd.Series:
    """One-sided upper CuSum of standardised residuals. NaNs hold the previous value (decayed)."""
    s, out = 0.0, []
    for v in z:
        if v is None or np.isnan(v):
            s = max(0.0, s - k)
        else:
            s = max(0.0, s + float(v) - k)
        out.append(s)
    return pd.Series(out, index=z.index)


def illness_watch(rhr_z: pd.Series, resp_delta: pd.Series | None = None, temp_dev: pd.Series | None = None,
                  hrv_state: pd.Series | None = None, k: float = 0.5, h: float = 3.5,
                  resp_thr: float = 1.0, temp_thr: float = 0.5) -> pd.DataFrame:
    """Heuristic early-warning inspired by RHR-CuSum detectors (Alavi et al., Nat Med 2022).

    level: 'none' | 'watch' (RHR CuSum above h, or a single-day RHR jump ≥2.5 SD)
           | 'likely' (watch + ≥2 corroborating signals: respiration +1 brpm, skin temp +0.5 °C, HRV below SWC)
    """
    # Windowed CuSum: only *acute* elevations over the last 4 days count; slow drifts are absorbed by the
    # rolling baseline and must not raise a persistent alarm.
    cs = (rhr_z - k).clip(lower=0).rolling(4, min_periods=1).sum().fillna(0)
    idx = rhr_z.index
    corr = pd.DataFrame(index=idx)
    corr["resp"] = (resp_delta >= resp_thr) if resp_delta is not None else False
    corr["temp"] = (temp_dev >= temp_thr) if temp_dev is not None else False
    corr["hrv"] = (hrv_state == "below") if hrv_state is not None else False
    n_corr = corr.fillna(False).astype(int).sum(axis=1)
    watch = ((cs >= h) & (rhr_z >= 1.0)) | (rhr_z >= 2.5)
    level = np.where(watch & (n_corr >= 2), "likely", np.where(watch, "watch", "none"))
    return pd.DataFrame({"rhr_cusum": cs, "illness_level": level, "illness_corroboration": n_corr,
                         "corr_resp": corr["resp"], "corr_temp": corr["temp"], "corr_hrv": corr["hrv"]}, index=idx)
