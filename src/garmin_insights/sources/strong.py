"""Strong app CSV importer.

Strong exports one row per set (plus "Rest Timer" rows), semicolon- or comma-delimited, e.g.:
"Workout #";"Date";"Workout Name";"Duration (sec)";"Exercise Name";"Set Order";"Weight (kg)";"Reps";"RPE";
"Distance (meters)";"Seconds";"Notes";"Workout Notes"
Older exports use "Duration" like "45m" and "Weight"/"Weight Unit" columns — both are handled.
Set Order may be numeric (working set) or W (warm-up), D (drop set), F (failure).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
from datetime import datetime, timedelta
from importlib import resources
from pathlib import Path

from garmin_insights.storage import db

log = logging.getLogger(__name__)
LB = 0.45359237


def _f(x: str | None) -> float | None:
    if x is None:
        return None
    x = str(x).strip().replace(",", ".")
    if x == "":
        return None
    try:
        return float(x)
    except ValueError:
        return None


def _duration_s(x: str | None) -> float | None:
    v = _f(x)
    if v is not None:
        return v
    if not x:
        return None
    h = re.search(r"(\d+)\s*h", x)
    m = re.search(r"(\d+)\s*m", x)
    s = re.search(r"(\d+)\s*s", x)
    tot = (int(h.group(1)) * 3600 if h else 0) + (int(m.group(1)) * 60 if m else 0) + (int(s.group(1)) if s else 0)
    return float(tot) or None


def _parse_date(x: str) -> datetime | None:
    x = (x or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(x, fmt)
        except ValueError:
            continue
    return None


def parse_csv(text: str, default_unit: str = "kg") -> list[dict]:
    text = text.lstrip("﻿")
    first = text.splitlines()[0] if text else ""
    delim = ";" if first.count(";") > first.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    norm = {k: k.strip().lower() for k in (reader.fieldnames or [])}

    def col(row: dict, *names: str) -> str | None:
        for k, n in norm.items():
            for want in names:
                if n == want or n.startswith(want):
                    return row.get(k)
        return None

    weight_col_unit = None
    for _k, n in norm.items():
        if n.startswith("weight"):
            if "(kg)" in n:
                weight_col_unit = "kg"
            elif "(lb" in n:
                weight_col_unit = "lb"
    rows, counters = [], {}
    for r in reader:
        so = (col(r, "set order") or "").strip()
        if not so or so.lower().startswith("rest"):
            continue
        start = _parse_date(col(r, "date") or "")
        ex = (col(r, "exercise name") or "").strip()
        if not start or not ex:
            continue
        set_type = "working"
        if so.upper() == "W":
            set_type = "warmup"
        elif so.upper() == "D":
            set_type = "drop"
        elif so.upper() == "F":
            set_type = "failure"
        key = (start.isoformat(), ex)
        counters[key] = counters.get(key, 0) + 1
        unit = weight_col_unit or (col(r, "weight unit") or default_unit).strip().lower() or default_unit
        w = _f(col(r, "weight"))
        if w is not None and unit.startswith("lb"):
            w = w * LB
        rows.append({
            "workout_start": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "exercise": ex,
            "set_idx": counters[key],
            "workout_no": int(_f(col(r, "workout #")) or 0) or None,
            "workout_name": (col(r, "workout name") or "").strip(),
            "workout_duration_s": _duration_s(col(r, "duration")),
            "set_type": set_type,
            "weight_kg": w,
            "reps": _f(col(r, "reps")),
            "rpe": _f(col(r, "rpe")),
            "distance_m": _f(col(r, "distance")),
            "seconds": _f(col(r, "seconds")),
            "notes": (col(r, "notes") or "").strip() or None,
            "date": start.strftime("%Y-%m-%d"),
            "source": "strong",
        })
    return rows


def match_activities(rows: list[dict], tolerance_min: int = 30) -> list[dict]:
    """Attach the overlapping Garmin strength activity (same window ± tolerance) to each set."""
    acts = db.query("SELECT activity_id, start_local, duration_s FROM activity WHERE sport_group='strength'")
    windows = []
    for a in acts:
        try:
            s = datetime.fromisoformat(a["start_local"])
        except (TypeError, ValueError):
            continue
        windows.append((s, s + timedelta(seconds=a["duration_s"] or 0), a["activity_id"]))
    tol = timedelta(minutes=tolerance_min)
    cache: dict[str, int | None] = {}
    for r in rows:
        ws = r["workout_start"]
        if ws not in cache:
            s = datetime.fromisoformat(ws)
            e = s + timedelta(seconds=r.get("workout_duration_s") or 3600)
            best, best_overlap = None, timedelta(0)
            for a_s, a_e, aid in windows:
                overlap = min(e, a_e + tol) - max(s, a_s - tol)
                if overlap > best_overlap:
                    best, best_overlap = aid, overlap
            cache[ws] = best
        r["matched_activity_id"] = cache[ws]
    return rows


def import_text(text: str, default_unit: str = "kg") -> dict:
    rows = parse_csv(text, default_unit)
    rows = match_activities(rows)
    n = db.upsert("strength_set", rows)
    ensure_exercise_map()
    workouts = len({r["workout_start"] for r in rows})
    matched = len({r["workout_start"] for r in rows if r.get("matched_activity_id")})
    return {"sets": n, "workouts": workouts, "matched_workouts": matched}


def import_folder(folder: str, default_unit: str = "kg") -> dict:
    p = Path(folder).expanduser()
    if not p.is_dir():
        return {"status": "missing_folder", "folder": str(p)}
    seen = db.get_state("strong_imported_hashes", []) or []
    out = {"files": 0, "sets": 0}
    for f in sorted(p.glob("*.csv"), key=lambda x: x.stat().st_mtime):
        try:
            data = f.read_bytes()
        except OSError as e:  # Google Drive placeholder not yet downloaded
            log.warning("cannot read %s: %s", f, e)
            continue
        h = hashlib.sha256(data).hexdigest()
        if h in seen:
            continue
        res = import_text(data.decode("utf-8", errors="replace"), default_unit)
        out["files"] += 1
        out["sets"] += res["sets"]
        seen.append(h)
    db.set_state("strong_imported_hashes", seen[-50:])
    return out


def rematch_all() -> None:
    rows = db.query("SELECT * FROM strength_set")
    if rows:
        db.upsert("strength_set", match_activities(rows))


# ------------------------------------------------------------------ exercise → muscles

def default_exercise_map() -> dict[str, tuple[list[str], list[str]]]:
    raw = json.loads(resources.files("garmin_insights.data").joinpath("exercise_muscles.json").read_text())
    return {k: (v[0], v[1]) for k, v in raw.items() if not k.startswith("_")}


def muscle_list() -> list[str]:
    raw = json.loads(resources.files("garmin_insights.data").joinpath("exercise_muscles.json").read_text())
    return raw["_muscles"]


def lookup_muscles(exercise: str, mapping: dict) -> tuple[list[str], list[str]] | None:
    if exercise in mapping:
        return mapping[exercise]
    low = {k.lower(): v for k, v in mapping.items()}
    if exercise.lower() in low:
        return low[exercise.lower()]
    base = re.sub(r"\s*\(.*\)\s*$", "", exercise).strip().lower()
    if base in low:
        return low[base]
    for k, v in low.items():
        if re.sub(r"\s*\(.*\)\s*$", "", k).strip() == base:
            return v
    return None


def ensure_exercise_map() -> list[str]:
    """Fill exercise_map for every logged exercise we can resolve. Returns unmapped exercise names."""
    defaults = default_exercise_map()
    existing = {r["exercise"] for r in db.query("SELECT exercise FROM exercise_map")}
    unmapped, rows = [], []
    for r in db.query("SELECT DISTINCT exercise FROM strength_set"):
        ex = r["exercise"]
        if ex in existing:
            continue
        m = lookup_muscles(ex, defaults)
        if m is None:
            unmapped.append(ex)
            continue
        rows.append({"exercise": ex, "primary_muscles": ",".join(m[0]), "secondary_muscles": ",".join(m[1]),
                     "user_defined": 0})
    db.upsert("exercise_map", rows)
    return unmapped


def unmapped_exercises() -> list[str]:
    return [r["exercise"] for r in db.query(
        "SELECT DISTINCT s.exercise FROM strength_set s LEFT JOIN exercise_map m ON m.exercise=s.exercise "
        "WHERE m.exercise IS NULL ORDER BY s.exercise")]
