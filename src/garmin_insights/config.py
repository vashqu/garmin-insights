"""Paths, settings and secrets.

Settings live in ``<data_dir>/settings.toml``. Secrets (Garmin Personal API key, Garmin password) are read
from the macOS Keychain first, then from environment variables / a ``.env`` file (project root or data dir).
"""

from __future__ import annotations

import glob
import logging
import os
import tomllib
from dataclasses import asdict, dataclass, field, fields
from logging.handlers import RotatingFileHandler
from pathlib import Path

import tomli_w
from dotenv import load_dotenv

APP_NAME = "Garmin Insights"
KEYRING_SERVICE = "garmin-insights"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    d = Path(os.environ.get("GI_DATA_DIR", Path.home() / ".garmin_insights"))
    d.mkdir(parents=True, exist_ok=True)
    for sub in ("raw", "samples", "tokens", "logs", "cache"):
        (d / sub).mkdir(exist_ok=True)
    try:
        (d / "tokens").chmod(0o700)
    except OSError:
        pass
    return d


def db_path() -> Path:
    return data_dir() / "garmin.db"


def _default_strong_folder() -> str:
    hits = sorted(glob.glob(str(Path.home() / "Library/CloudStorage/GoogleDrive-*/My Drive/GarminInsights/strong")))
    if hits:
        return hits[0]
    return str(Path.home() / "GarminInsights" / "strong")


@dataclass
class Settings:
    backfill_days: int = 365
    strong_folder: str = field(default_factory=_default_strong_folder)
    strong_weight_unit: str = "kg"  # unit used inside the Strong CSV ("kg" or "lb")
    llm_briefing_model: str = "qwen3:4b-instruct"
    llm_chat_model: str = "qwen3:4b-instruct"
    llm_enabled: bool = True
    forecast_enabled: bool = True
    sex: str = ""  # "male" | "female" | "" (unset → male TRIMP coefficients, flagged in UI)
    birth_year: int = 0  # 0 → derived from Garmin fitness-age payload
    max_hr_override: int = 0
    rhr_override: int = 0
    css_test_400_s: float = 0.0  # optional CSS test results (seconds)
    css_test_200_s: float = 0.0
    use_unofficial: bool = True  # fallback library, only used after login
    garmin_email: str = ""
    request_delay_s: float = 0.6
    theme: str = "auto"

    @classmethod
    def load(cls) -> Settings:
        p = data_dir() / "settings.toml"
        if not p.exists():
            s = cls()
            s.save()
            return s
        with p.open("rb") as f:
            raw = tomllib.load(f)
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> None:
        p = data_dir() / "settings.toml"
        p.write_text(tomli_w.dumps(asdict(self)))


# ---------------------------------------------------------------- secrets

def _load_env_files() -> None:
    for p in (PROJECT_ROOT / ".env", data_dir() / ".env"):
        if p.exists():
            load_dotenv(p, override=False)


def get_secret(name: str) -> str | None:
    """Keychain first, then environment/.env."""
    try:
        import keyring

        v = keyring.get_password(KEYRING_SERVICE, name)
        if v:
            return v
    except Exception:  # keyring backend unavailable
        pass
    _load_env_files()
    return os.environ.get(name) or None


def set_secret(name: str, value: str) -> None:
    import keyring

    keyring.set_password(KEYRING_SERVICE, name, value)


def delete_secret(name: str) -> None:
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, name)
    except Exception:
        pass


# ---------------------------------------------------------------- logging

_logging_ready = False


def setup_logging(level: int = logging.INFO) -> None:
    global _logging_ready
    if _logging_ready:
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    fh = RotatingFileHandler(data_dir() / "logs" / "app.log", maxBytes=2_000_000, backupCount=3)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    for noisy in ("httpx", "urllib3", "werkzeug", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _logging_ready = True
