"""Parse Garmin .FIT activity files into per-second samples (Parquet) and swim lengths."""

from __future__ import annotations

import io
import logging
from pathlib import Path

import fitdecode
import pandas as pd

from garmin_insights import config

log = logging.getLogger(__name__)

RECORD_FIELDS = {
    "heart_rate": "hr",
    "enhanced_speed": "speed",
    "speed": "speed",
    "cadence": "cadence",
    "fractional_cadence": "cadence_frac",
    "enhanced_altitude": "alt",
    "altitude": "alt",
    "power": "power",
    "distance": "dist",
    "temperature": "temp",
}


def parse_fit(data: bytes) -> dict:
    records, lengths, session = [], [], {}
    with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.DISABLED) as fit:
        for frame in fit:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue
            if frame.name == "record":
                row = {"ts": frame.get_value("timestamp", fallback=None)}
                for f, k in RECORD_FIELDS.items():
                    if k in row and row[k] is not None:
                        continue
                    v = frame.get_value(f, fallback=None)
                    if v is not None:
                        row[k] = v
                if row["ts"] is not None:
                    records.append(row)
            elif frame.name == "length":
                lengths.append({
                    "time_s": frame.get_value("total_timer_time", fallback=None)
                    or frame.get_value("total_elapsed_time", fallback=None),
                    "strokes": frame.get_value("total_strokes", fallback=None),
                    "stroke": str(frame.get_value("swim_stroke", fallback="") or ""),
                    "length_type": str(frame.get_value("length_type", fallback="") or ""),
                })
            elif frame.name == "session":
                for f in ("sport", "sub_sport", "pool_length", "total_distance", "num_active_lengths"):
                    v = frame.get_value(f, fallback=None)
                    if v is not None:
                        session[f] = v
    df = pd.DataFrame(records)
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_localize(None)
        df = df.sort_values("ts").drop_duplicates("ts")
        df["t"] = (df["ts"] - df["ts"].iloc[0]).dt.total_seconds()
        if "cadence_frac" in df:
            df["cadence"] = df["cadence"].fillna(0) + df.pop("cadence_frac").fillna(0)
        for c in ("hr", "speed", "cadence", "alt", "power", "dist", "temp"):
            if c in df:
                df[c] = pd.to_numeric(df[c], errors="coerce")
    return {"samples": df, "lengths": lengths, "session": session}


def samples_path(activity_id: int) -> Path:
    return config.data_dir() / "samples" / f"{activity_id}.parquet"


def save_samples(activity_id: int, df: pd.DataFrame, sport_group: str) -> bool:
    if df is None or df.empty:
        return False
    df = df.copy()
    if sport_group == "run" and "cadence" in df:
        df["cadence"] = df["cadence"] * 2  # FIT running cadence is strides/min (one leg) → steps/min
    df.to_parquet(samples_path(activity_id), index=False)
    return True


def load_samples(activity_id: int) -> pd.DataFrame:
    p = samples_path(activity_id)
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


def swim_length_rows(activity_id: int, lengths: list[dict], pool_length_m: float | None) -> list[dict]:
    rows = []
    for i, ln in enumerate(lengths):
        if ln.get("length_type") and ln["length_type"] != "active":
            continue
        t, s = ln.get("time_s"), ln.get("strokes")
        rows.append({
            "activity_id": activity_id, "idx": i, "stroke": ln.get("stroke"),
            "distance_m": pool_length_m, "time_s": t, "strokes": s,
            "swolf": (t + s) if (t is not None and s is not None) else None,
            "length_type": ln.get("length_type"),
        })
    return rows
