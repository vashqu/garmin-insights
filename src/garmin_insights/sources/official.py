"""Garmin Connect **Personal API** client (official, pull-only, personal use).

Auth: the personal API key is exchanged for a 24h bearer token (no refresh token). Endpoints come from Garmin's
published Personal API OpenAPI spec (developer.garmin.com/gc-developer-program/personal/swagger-docs/).
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import time
import zipfile
from datetime import date

import httpx

from garmin_insights import config

log = logging.getLogger(__name__)

BASE = "https://personal.connectapi.garmin.com"
TOKEN_URL = BASE + "/personalapitoken-service/personal-api/personal-api-key/token"
SECRET_NAME = "GARMIN_API_KEY"


class OfficialAPIError(RuntimeError):
    pass


class NotConfigured(OfficialAPIError):
    pass


class RateLimited(OfficialAPIError):
    """Repeated HTTP 429 — abort the sync instead of hammering Garmin."""


class AuthError(OfficialAPIError):
    """Token cannot be obtained — abort instead of hammering the token endpoint."""


def _jwt_exp(token: str) -> float:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:
        return time.time() + 3600


class OfficialClient:
    name = "official"

    def __init__(self, api_key: str | None = None, delay_s: float = 0.3):
        self.api_key = api_key or config.get_secret(SECRET_NAME)
        self.delay_s = delay_s
        self._token: str | None = None
        self._exp: float = 0
        self._token_file = config.data_dir() / "tokens" / "official_token.json"
        self._http = httpx.Client(timeout=60, headers={"Accept": "application/json"})
        self._last_call = 0.0
        self.calls: dict[str, int] = {}  # endpoint → HTTP requests made by this client (incl. retries)
        self.consecutive_429 = 0
        self._auth_failed: str | None = None

    # ------------------------------------------------------------- auth
    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _load_cached_token(self) -> None:
        if self._token or not self._token_file.exists():
            return
        try:
            d = json.loads(self._token_file.read_text())
            if d.get("exp", 0) > time.time() + 300:
                self._token, self._exp = d["token"], d["exp"]
        except Exception:
            pass

    def token(self, force: bool = False) -> str:
        if not self.api_key:
            raise NotConfigured("No Garmin Personal API key configured")
        if not force:
            self._load_cached_token()
            if self._token and self._exp > time.time() + 300:
                return self._token
        if self._auth_failed:
            raise AuthError(self._auth_failed)
        self._count("token")
        r = self._http.post(TOKEN_URL, headers={"Authorization": f"ApiKey {self.api_key}", "Accept": "*/*"})
        if r.status_code != 200:
            self._auth_failed = f"Garmin Personal API token request failed: HTTP {r.status_code}"
            if r.status_code in (401, 403):
                self._auth_failed += " (API key invalid or expired — create a new one in Garmin Connect)"
            elif r.status_code == 429:
                self._auth_failed += " (rate limited — try again in a few minutes)"
            raise AuthError(self._auth_failed)
        tok = r.text.strip().strip('"')
        try:  # some deployments may return JSON
            j = json.loads(r.text)
            if isinstance(j, dict):
                tok = j.get("access_token") or j.get("accessToken") or tok
        except ValueError:
            pass
        self._token, self._exp = tok, _jwt_exp(tok)
        self._token_file.write_text(json.dumps({"token": tok, "exp": self._exp}))
        self._token_file.chmod(0o600)
        return tok

    # ------------------------------------------------------------- http
    def _count(self, path: str) -> None:
        key = re.sub(r"/\d{4}-\d{2}-\d{2}|/\d{6,}", "/{x}", path.split("?")[0])
        self.calls[key] = self.calls.get(key, 0) + 1

    @property
    def total_calls(self) -> int:
        return sum(self.calls.values())

    def _throttle(self) -> None:
        wait = self.delay_s - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def request(self, path: str, params: dict | None = None, raw: bool = False):
        for attempt in range(4):
            self._throttle()
            self._count(path)
            hdr = {"Authorization": f"Bearer {self.token()}", "Accept": "*/*" if raw else "application/json"}
            r = self._http.get(BASE + path, params=params, headers=hdr)
            if r.status_code == 401 and attempt == 0:
                self.token(force=True)
                continue
            if r.status_code == 429:
                self.consecutive_429 += 1
                if self.consecutive_429 >= 3:
                    raise RateLimited("Garmin rate limit hit (HTTP 429 ×3) — stopping this sync; try later")
                time.sleep(2 ** attempt * 5)
                continue
            self.consecutive_429 = 0
            if r.status_code in (500, 502, 503, 504):
                time.sleep(2 ** attempt * 2)
                continue
            if r.status_code == 204 or not r.content:
                return None
            if r.status_code >= 400:
                raise OfficialAPIError(f"GET {path} -> HTTP {r.status_code}")
            return r.content if raw else r.json()
        raise OfficialAPIError(f"GET {path} failed after retries")

    # ------------------------------------------------------------- endpoints
    def daily_summaries(self, start: date, end: date):
        j = self.request("/usersummary-service/v2/dailysummaries",
                         {"startDate": start.isoformat(), "endDate": end.isoformat()})
        return (j or {}).get("data", [])

    def user_summary(self, day: date):
        return self.request("/usersummary-service/usersummary/daily", {"calendarDate": day.isoformat()})

    def sleep_range(self, start: date, end: date):
        return self.request("/sleep-service/sleep/dailySleepSummariesByDate",
                            {"startDate": start.isoformat(), "endDate": end.isoformat()}) or []

    def sleep_day(self, day: date):
        return self.request("/sleep-service/sleep/dailySleep", {"date": day.isoformat()})

    def hrv_range(self, start: date, end: date):
        return self.request(f"/hrv-service/hrv/daily/{start.isoformat()}/{end.isoformat()}")

    def hrv_day(self, day: date):
        return self.request(f"/hrv-service/hrv/{day.isoformat()}")

    def body_battery(self, start: date, end: date):
        return self.request("/wellness-service/wellness/bodyBattery/reports/daily",
                            {"startDate": start.isoformat(), "endDate": end.isoformat()}) or []

    def heart_rate_day(self, day: date):
        return self.request("/wellness-service/wellness/dailyHeartRate", {"date": day.isoformat()})

    def spo2_day(self, day: date):
        return self.request(f"/wellness-service/wellness/daily/spo2acclimation/{day.isoformat()}")

    def stress_range(self, start: date, end: date):
        return self.request(f"/usersummary-service/stats/stress/daily/{start.isoformat()}/{end.isoformat()}") or []

    def vo2max_range(self, start: date, end: date):
        return self.request(f"/metrics-service/metrics/maxmet/daily/{start.isoformat()}/{end.isoformat()}") or []

    def fitness_age(self, day: date):
        return self.request(f"/fitnessage-service/fitnessage/v2/{day.isoformat()}")

    def profile_settings(self):
        return self.request("/userprofile-service/userprofile/settings")

    def hr_zones(self):
        return self.request("/biometric-service/heartRateZones/v2") or []

    def activities(self, start: date, end: date, page: int = 100):
        out, s = [], 0
        while True:
            batch = self.request("/activitylist-service/activities/search/activities",
                                 {"start": s, "limit": page, "startDate": start.isoformat(),
                                  "endDate": end.isoformat()}) or []
            out.extend(batch)
            if len(batch) < page:
                return out
            s += page

    def activity(self, activity_id: int):
        return self.request(f"/activity-service/activity/{activity_id}")

    def activity_fit(self, activity_id: int) -> bytes | None:
        """Returns raw .fit bytes (unzipped) or None."""
        blob = self.request(f"/download-service/files/activity/{activity_id}", raw=True)
        if not blob:
            return None
        if blob[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                for n in z.namelist():
                    if n.lower().endswith(".fit"):
                        return z.read(n)
            return None
        return blob
