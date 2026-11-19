"""Journal tags: built-in Lifestyle + Diet sets, custom tags, per-day values."""

from __future__ import annotations

import re

from garmin_insights.storage import db

DEFAULT_TAGS = [
    # tag, label, kind, category
    ("alcohol", "Alcohol (drinks)", "count", "Lifestyle"),
    ("late_caffeine", "Caffeine after 14:00", "bool", "Lifestyle"),
    ("late_meal", "Late meal (<3 h before bed)", "bool", "Lifestyle"),
    ("late_screen", "Screens in bed", "bool", "Lifestyle"),
    ("sauna_cold", "Sauna / cold exposure", "bool", "Lifestyle"),
    ("protein_target", "Hit protein target", "bool", "Diet"),
    ("high_sugar", "High-sugar day", "bool", "Diet"),
    ("processed_food", "Heavy / processed food", "bool", "Diet"),
    ("low_carb", "Low-carb day", "bool", "Diet"),
    ("fasting", "Fasting / skipped meals", "bool", "Diet"),
    ("hydration_good", "Well hydrated", "bool", "Diet"),
]


def ensure_defaults() -> None:
    have = {r["tag"] for r in db.query("SELECT tag FROM journal_tag_def")}
    rows = [{"tag": t, "label": lab, "kind": k, "category": c, "active": 1, "sort": i}
            for i, (t, lab, k, c) in enumerate(DEFAULT_TAGS) if t not in have]
    if rows:
        db.upsert("journal_tag_def", rows)


def tags(active_only: bool = True) -> list[dict]:
    ensure_defaults()
    q = "SELECT * FROM journal_tag_def" + (" WHERE active=1" if active_only else "") + " ORDER BY category, sort, label"
    return db.query(q)


def add_custom(label: str, kind: str = "bool") -> str:
    tag = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40] or "custom"
    db.upsert("journal_tag_def", [{"tag": tag, "label": label, "kind": kind, "category": "Custom", "active": 1,
                                   "sort": 500}])
    return tag


def day_values(day: str) -> dict[str, float]:
    return {r["tag"]: r["value"] for r in db.query("SELECT tag, value FROM journal WHERE date=?", (day,))}


def set_value(day: str, tag: str, value: float | None) -> None:
    if value is None or value == 0:
        db.execute("DELETE FROM journal WHERE date=? AND tag=?", (day, tag))
    else:
        db.upsert("journal", [{"date": day, "tag": tag, "value": float(value)}])
    db.set_state("journal_dirty", True)
