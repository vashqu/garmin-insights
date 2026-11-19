"""Sync engine: incremental, newest-first, resumable; reports progress for the UI."""

from __future__ import annotations

import logging
import threading
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from garmin_insights import config
from garmin_insights.sources.official import AuthError, RateLimited
from garmin_insights.sources.router import Router
from garmin_insights.storage import db
from garmin_insights.sync import fit, normalize

log = logging.getLogger(__name__)
CHUNK = 28


@dataclass
class Progress:
    running: bool = False
    step: str = ""
    done: int = 0
    total: int = 0
    errors: list[str] = field(default_factory=list)
    started: str | None = None
    finished: str | None = None
    message: str = ""

    def as_dict(self) -> dict:
        return dict(self.__dict__)


PROGRESS = Progress()
_lock = threading.Lock()


def _chunks(start: date, end: date, size: int = CHUNK):
    """Newest-first [a, b] chunks covering start..end."""
    b = end
    while b >= start:
        a = max(start, b - timedelta(days=size - 1))
        yield a, b
        b = a - timedelta(days=1)


def _days(start: date, end: date):
    d = end
    while d >= start:
        yield d
        d -= timedelta(days=1)


def _due(key: str, hours: float) -> bool:
    """True if the step keyed `key` hasn't run in the last `hours` (tracked in sync_state)."""
    last = db.get_state(f"last_run:{key}")
    if not last:
        return True
    return (datetime.now() - datetime.fromisoformat(last)).total_seconds() >= hours * 3600


def _mark(key: str) -> None:
    db.set_state(f"last_run:{key}", datetime.now().isoformat(timespec="seconds"))


class SyncEngine:
    def __init__(self, settings: config.Settings | None = None, progress: Progress = PROGRESS):
        self.s = settings or config.Settings.load()
        self.r = Router(self.s)
        self.p = progress

    def _step(self, name: str, total: int = 0) -> None:
        self.p.step, self.p.done, self.p.total = name, 0, total
        log.info("sync step: %s", name)

    def _err(self, where: str, e: Exception) -> None:
        msg = f"{where}: {e}"
        log.warning(msg)
        log.debug(traceback.format_exc())
        self.p.errors.append(msg)

    # -------------------------------------------------------------- range
    def date_range(self) -> tuple[date, date]:
        today = date.today()
        last = db.get_state("last_synced_day")
        if last:
            start = date.fromisoformat(last) - timedelta(days=2)
        else:
            start = today - timedelta(days=self.s.backfill_days)
        return start, today

    # -------------------------------------------------------------- run
    def run(self, full: bool = False, days: int | None = None) -> dict:
        if not self.r.official.configured and not self.r.has_unofficial:
            self.p.message = "No Garmin credentials configured (Personal API key or login)."
            return {"status": "no_credentials"}
        start, end = self.date_range()
        if full:
            start = end - timedelta(days=self.s.backfill_days)
        if days:
            start = end - timedelta(days=days)
        if self.r.official.configured:
            try:
                self.r.official.token()
            except AuthError as e:
                if not self.r.has_unofficial:
                    self.p.message = str(e)
                    self.p.errors.append(str(e))
                    return {"status": "auth_error", "error": str(e)}
        try:
            res = self._run(start, end, full)
        except (AuthError, RateLimited) as e:
            self.p.message = str(e)
            self.p.errors.append(str(e))
            res = {"status": "rate_limited" if isinstance(e, RateLimited) else "auth_error", "error": str(e)}
        self._record_calls(res)
        return res

    def _record_calls(self, res: dict) -> None:
        o = self.r.official
        entry = {"at": datetime.now().isoformat(timespec="seconds"), "total": o.total_calls,
                 "by_endpoint": dict(sorted(o.calls.items(), key=lambda kv: -kv[1])), "status": res.get("status")}
        hist = (db.get_state("api_call_log", []) or [])[-200:]
        db.set_state("api_call_log", hist + [entry])
        res["api_calls"] = o.total_calls
        log.info("sync made %s official API requests: %s", o.total_calls, entry["by_endpoint"])

    def _run(self, start: date, end: date, full: bool) -> dict:
        first_sync = db.get_state("last_synced_day") is None
        self.sync_profile(force=first_sync or full)
        self.full = first_sync or full
        wellness_days = self.sync_daily(start, end, stop_when_empty=first_sync or full)
        if wellness_days:
            start = max(start, min(wellness_days))
        self.sync_sleep(start, end)
        self.sync_hrv(start, end)
        self.sync_body_battery(start, end)
        self.sync_intraday_hr(sorted(wellness_days, reverse=True))
        self.sync_spo2(sorted(wellness_days, reverse=True))
        self.sync_fitness(start, end)
        self.sync_activities(start, end)
        self.sync_unofficial_extras(start, end)
        self.import_strong()
        db.set_state("last_synced_day", end.isoformat())
        db.set_state("last_sync_at", datetime.now().isoformat(timespec="seconds"))
        db.set_state("sources_used", self.r.used)
        self._step("Computing analytics")
        try:
            from garmin_insights.analytics import derive

            derive.recompute()
        except Exception as e:  # noqa: BLE001
            self._err("analytics", e)
        return {"status": "ok", "range": [start.isoformat(), end.isoformat()], "errors": self.p.errors}

    # -------------------------------------------------------------- steps
    def sync_profile(self, force: bool = False) -> None:
        if not force and not _due("profile", 24):
            return
        _mark("profile")
        self._step("Profile")
        o = self.r.official
        prof = {}
        if o.configured:
            try:
                ps = o.profile_settings() or {}
                prof["timezone"] = ps.get("timeZone")
                prof["measurement_system"] = ps.get("measurementSystem")
                zones = o.hr_zones() or []
                z = next((x for x in zones if x.get("sport") == "DEFAULT"), zones[0] if zones else {})
                if z:
                    prof["max_hr"] = z.get("maxHeartRateUsed")
                    prof["rhr_zones"] = z.get("restingHeartRateUsed")
                    prof["hr_zone_floors"] = [z.get(f"zone{i}Floor") for i in range(1, 6)]
                fa = o.fitness_age(date.today())
                if fa:
                    prof["chrono_age"] = fa.get("chronologicalAge")
                    db.upsert("fitness_age", normalize.fitness_age(fa, date.today().isoformat()))
            except Exception as e:  # noqa: BLE001
                self._err("profile", e)
        if self.r.has_unofficial:
            try:
                up = self.r.unofficial.call("get_user_profile") or {}
                ud = up.get("userData") or {}
                if ud.get("gender"):
                    prof["sex"] = ud["gender"].lower()
                if ud.get("birthDate"):
                    prof["birth_date"] = ud["birthDate"]
                if ud.get("weight"):
                    prof["weight_kg"] = ud["weight"] / 1000.0
                if ud.get("height"):
                    prof["height_cm"] = ud["height"]
            except Exception as e:  # noqa: BLE001
                self._err("profile (unofficial)", e)
        if prof:
            db.set_profile(**{k: v for k, v in prof.items() if v is not None})

    def sync_daily(self, start: date, end: date, stop_when_empty: bool = False) -> list[date]:
        chunks = list(_chunks(start, end))
        self._step("Daily summaries", len(chunks))
        wellness, empty_streak, seen_data = [], 0, False
        for a, b in chunks:
            items, src = self.r.fetch(
                "daily", f"{a}_{b}", lambda a=a, b=b: self.r.official.daily_summaries(a, b),
                lambda u, a=a, b=b: [x for x in (u.call("get_user_summary", d.isoformat()) for d in _days(a, b)) if x])
            rows = []
            if src == "official":
                rows = normalize.daily_summaries(items)
            elif src == "unofficial":
                rows = [normalize.user_summary(x, "unofficial") for x in items]
            rows = [r for r in rows if r.get("date")]
            db.upsert("daily", rows)
            have = [date.fromisoformat(r["date"]) for r in rows if r.get("has_wellness")]
            wellness += have
            self.p.done += 1
            if have:
                seen_data, empty_streak = True, 0
            else:
                empty_streak += 1
            if stop_when_empty and seen_data and empty_streak >= 2:
                log.info("history starts around %s; stopping backfill", min(wellness))
                db.set_state("history_start", min(wellness).isoformat())
                break
        return wellness

    def sync_sleep(self, start: date, end: date) -> None:
        chunks = list(_chunks(start, end))
        self._step("Sleep", len(chunks))
        for a, b in chunks:
            items, src = self.r.fetch("sleep", f"{a}_{b}", lambda a=a, b=b: self.r.official.sleep_range(a, b),
                                      lambda u, a=a, b=b: [u.call("get_sleep_data", d.isoformat()) for d in _days(a, b)])
            if src:
                db.upsert("sleep", normalize.sleep(items if src == "official" else
                                                   [x.get("dailySleepDTO", {}) for x in items if x], src))
            self.p.done += 1

    def sync_hrv(self, start: date, end: date) -> None:
        if db.get_state("hrv_available") is False and not getattr(self, "full", False) and not _due("hrv_probe", 24 * 7):
            return  # watch doesn't record HRV; re-check weekly
        _mark("hrv_probe")
        chunks = list(_chunks(start, end))
        self._step("HRV", len(chunks))
        found = 0
        for a, b in chunks:
            payload, src = self.r.fetch("hrv", f"{a}_{b}", lambda a=a, b=b: self.r.official.hrv_range(a, b),
                                        lambda u, a=a, b=b: [x.get("hrvSummary") for x in
                                                             (u.call("get_hrv_data", d.isoformat()) for d in _days(a, b))
                                                             if x and x.get("hrvSummary")])
            if src:
                rows = normalize.hrv_summaries(payload, src)
                found += len(rows)
                db.upsert("hrv", rows)
            self.p.done += 1
        db.set_state("hrv_available", found > 0 or bool(db.query("SELECT 1 FROM hrv LIMIT 1")))

    def sync_body_battery(self, start: date, end: date) -> None:
        chunks = list(_chunks(start, end))
        self._step("Body Battery", len(chunks))
        for a, b in chunks:
            items, src = self.r.fetch("bb", f"{a}_{b}", lambda a=a, b=b: self.r.official.body_battery(a, b))
            if src:
                intr, daily = normalize.body_battery(items)
                db.upsert("intraday", intr)
                for d in daily:
                    db.execute("UPDATE daily SET bb_charged=COALESCE(?, bb_charged), bb_drained=COALESCE(?, bb_drained) "
                               "WHERE date=?", (d["bb_charged"], d["bb_drained"], d["date"]))
            self.p.done += 1

    def sync_intraday_hr(self, days: list[date]) -> None:
        have = {r["date"] for r in db.query("SELECT DISTINCT date FROM intraday WHERE metric='hr'")}
        todo = [d for d in days if d.isoformat() not in have or d >= date.today() - timedelta(days=1)]
        self._step("Heart rate (intraday)", len(todo))
        for d in todo:
            p, src = self.r.fetch("hr", d.isoformat(), lambda d=d: self.r.official.heart_rate_day(d),
                                  lambda u, d=d: u.call("get_heart_rates", d.isoformat()),
                                  empty=lambda x: not x or not x.get("heartRateValues"))
            if src:
                db.upsert("intraday", normalize.heart_rate_intraday(p))
                if p.get("lastSevenDaysAvgRestingHeartRate"):
                    db.execute("UPDATE daily SET rhr_7d_garmin=? WHERE date=?",
                               (p["lastSevenDaysAvgRestingHeartRate"], d.isoformat()))
            self.p.done += 1

    def sync_spo2(self, days: list[date]) -> None:
        """SpO2 only if the watch records it (probe the most recent days first)."""
        if not days:
            return
        if db.get_state("spo2_available") is False and not getattr(self, "full", False) and not _due("spo2_probe", 24 * 7):
            return  # watch doesn't record SpO2; re-check weekly
        _mark("spo2_probe")
        self._step("SpO2", min(3, len(days)))
        probe_hits = 0
        for d in days[:3]:
            p, src = self.r.fetch("spo2", d.isoformat(), lambda d=d: self.r.official.spo2_day(d))
            intr, daily = normalize.spo2_day(p)
            if intr or daily:
                probe_hits += 1
            self.p.done += 1
        db.set_state("spo2_available", probe_hits > 0)
        if not probe_hits:
            return
        self._step("SpO2", len(days))
        for d in days:
            p, src = self.r.fetch("spo2", d.isoformat(), lambda d=d: self.r.official.spo2_day(d))
            intr, daily = normalize.spo2_day(p)
            db.upsert("intraday", intr)
            if daily:
                db.execute("UPDATE daily SET spo2_avg=?, spo2_low=? WHERE date=?",
                           (daily["spo2_avg"], daily["spo2_low"], daily["date"]))
            self.p.done += 1

    def sync_fitness(self, start: date, end: date) -> None:
        chunks = list(_chunks(start, end, 14))
        self._step("VO2max & fitness age", len(chunks) + 1)
        for a, b in chunks:
            items, src = self.r.fetch("vo2max", f"{a}_{b}", lambda a=a, b=b: self.r.official.vo2max_range(a, b))
            if src:
                db.upsert("vo2max", normalize.vo2max(items, src))
            self.p.done += 1
        d = end if (getattr(self, "full", False) or _due("fitness_age", 24)) else start - timedelta(days=1)
        if d >= start:
            _mark("fitness_age")
        while d >= start:  # weekly fitness-age samples
            p, src = self.r.fetch("fitness_age", d.isoformat(),
                                  lambda d=d: self.r.official.fitness_age(d))
            if src:
                db.upsert("fitness_age", normalize.fitness_age(p, d.isoformat(), src))
            d -= timedelta(days=7)
        self.p.done += 1

    def sync_activities(self, start: date, end: date) -> None:
        self._step("Activities")
        items, src = self.r.fetch("activities", f"{start}_{end}",
                                  lambda: self.r.official.activities(start, end),
                                  lambda u: u.call("get_activities_by_date", start.isoformat(), end.isoformat()))
        if not src:
            return
        rows = [normalize.activity(a, src) for a in items if a.get("activityId")]
        extras = {a["activityId"]: normalize.activity_extras(a) for a in items if a.get("activityId")}
        existing = {r["activity_id"]: r["has_samples"] for r in db.query("SELECT activity_id, has_samples FROM activity")}
        for r in rows:
            r["has_samples"] = existing.get(r["activity_id"], 0)
        db.upsert("activity", rows)
        cur = db.get_state("activity_extras", {}) or {}
        cur.update({str(k): v for k, v in extras.items()})
        db.set_state("activity_extras", cur)
        todo = [r for r in rows if not r["has_samples"]]
        self._step("Activity details (FIT)", len(todo))
        for r in todo:
            aid = r["activity_id"]
            try:
                blob = self.r.official.activity_fit(aid) if self.r.official.configured else None
                if blob is None and self.r.has_unofficial:
                    from garminconnect import Garmin  # noqa: F401

                    z = self.r.unofficial.call("download_activity", aid,
                                               dl_fmt=self.r.unofficial.api.ActivityDownloadFormat.ORIGINAL)
                    import io
                    import zipfile

                    with zipfile.ZipFile(io.BytesIO(z)) as zf:
                        blob = zf.read(next(n for n in zf.namelist() if n.lower().endswith(".fit")))
                if blob:
                    parsed = fit.parse_fit(blob)
                    ok = fit.save_samples(aid, parsed["samples"], r["sport_group"])
                    if r["sport_group"] == "swim" and parsed["lengths"]:
                        pool = parsed["session"].get("pool_length") or r.get("pool_length_m")
                        db.upsert("swim_length", fit.swim_length_rows(aid, parsed["lengths"], pool))
                        if pool:
                            db.execute("UPDATE activity SET pool_length_m=? WHERE activity_id=?", (pool, aid))
                    db.execute("UPDATE activity SET has_samples=? WHERE activity_id=?", (1 if ok else -1, aid))
            except Exception as e:  # noqa: BLE001
                self._err(f"FIT {aid}", e)
                db.execute("UPDATE activity SET has_samples=-1 WHERE activity_id=?", (aid,))
            self.p.done += 1

    def sync_unofficial_extras(self, start: date, end: date) -> None:
        if not self.r.has_unofficial:
            return
        u = self.r.unofficial
        self._step("Extras (race predictions, records, events)", 5)
        try:
            rp = u.call("get_race_predictions")
            if rp:
                items = rp if isinstance(rp, list) else [rp]
                db.upsert("race_prediction", [{
                    "date": (x.get("calendarDate") or date.today().isoformat())[:10],
                    "t5k": x.get("time5K"), "t10k": x.get("time10K"), "thalf": x.get("timeHalfMarathon"),
                    "tmarathon": x.get("timeMarathon"), "source": "unofficial"} for x in items])
        except Exception as e:  # noqa: BLE001
            self._err("race predictions", e)
        self.p.done += 1
        try:
            prs = u.call("get_personal_record") or []
            rows = []
            for x in prs if isinstance(prs, list) else prs.get("personalRecords", []):
                rows.append({"type_key": str(x.get("typeId")), "label": x.get("typeKey") or str(x.get("typeId")),
                             "value": x.get("value"), "unit": "",
                             "date": (x.get("actStartDateTimeInGMTFormatted") or x.get("prStartTimeGmtFormatted") or "")[:10],
                             "activity_id": x.get("activityId"), "source": "unofficial"})
            db.upsert("personal_record", rows)
        except Exception as e:  # noqa: BLE001
            self._err("personal records", e)
        self.p.done += 1
        recent = [end - timedelta(days=i) for i in range(min(14, (end - start).days + 1))]
        for d in recent:
            try:
                ev = u.call("get_body_battery_events", d.isoformat()) or []
                db.upsert("bb_event", [{
                    "date": d.isoformat(),
                    "start_local": (e.get("event") or {}).get("eventStartTimeGmt") or "",
                    "event_type": (e.get("event") or {}).get("eventType"),
                    "duration_s": ((e.get("event") or {}).get("durationInMilliseconds") or 0) / 1000,
                    "impact": (e.get("event") or {}).get("bodyBatteryImpact"),
                    "feedback": (e.get("event") or {}).get("shortFeedback")} for e in ev])
                st = u.call("get_stress_data", d.isoformat())
                db.upsert("intraday", normalize.stress_intraday(st))
            except Exception as e:  # noqa: BLE001
                self._err(f"events {d}", e)
                break
        self.p.done += 1
        for name, metric in (("get_running_economy_range", "running_economy"),):
            try:
                xs = u.call(name, start.isoformat(), end.isoformat()) or []
                db.upsert("running_metric", [{"date": (x.get("calendarDate") or "")[:10], "metric": metric,
                                              "value": x.get("value") or x.get("runningEconomy"),
                                              "source": "unofficial"} for x in xs if x.get("calendarDate")])
            except Exception as e:  # noqa: BLE001
                self._err(metric, e)
        self.p.done += 1
        try:
            xs = u.call("get_running_tolerance", start.isoformat(), end.isoformat()) or []
            db.upsert("running_metric", [{"date": (x.get("calendarDate") or x.get("startDate") or "")[:10],
                                          "metric": "running_tolerance",
                                          "value": x.get("value") or x.get("tolerance"), "source": "unofficial"}
                                         for x in xs])
        except Exception as e:  # noqa: BLE001
            self._err("running tolerance", e)
        self.p.done += 1

    def import_strong(self) -> None:
        self._step("Strong import")
        try:
            from garmin_insights.sources import strong

            res = strong.import_folder(self.s.strong_folder, self.s.strong_weight_unit)
            strong.rematch_all()
            log.info("strong import: %s", res)
        except Exception as e:  # noqa: BLE001
            self._err("strong", e)


AUTO_SYNC_MIN_HOURS = 2.0


def run_sync_background(auto: bool = False, **kw) -> bool:
    """Start a sync in a daemon thread if none is running. Returns False if already running.

    auto=True (app launch) skips the sync when the last one finished < AUTO_SYNC_MIN_HOURS ago, to stay well
    inside Garmin's (undocumented) rate limits. Manual 'Sync now' always runs."""
    if auto:
        last = db.get_state("last_sync_at")
        if last and (datetime.now() - datetime.fromisoformat(last)).total_seconds() < AUTO_SYNC_MIN_HOURS * 3600:
            log.info("auto-sync skipped: last sync at %s", last)
            return False
    if not _lock.acquire(blocking=False):
        return False

    def _target():
        try:
            PROGRESS.running, PROGRESS.errors = True, []
            PROGRESS.started, PROGRESS.finished = datetime.now().isoformat(timespec="seconds"), None
            PROGRESS.message = ""
            SyncEngine().run(**kw)
        except Exception as e:  # noqa: BLE001
            log.exception("sync failed")
            PROGRESS.errors.append(str(e))
        finally:
            PROGRESS.running, PROGRESS.step = False, ""
            PROGRESS.finished = datetime.now().isoformat(timespec="seconds")
            _lock.release()

    threading.Thread(target=_target, daemon=True, name="sync").start()
    return True
