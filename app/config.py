"""Loads config/*.yaml and .env. Everything tunable lives in those files, not in code."""
import contextvars
import datetime as dt
import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
DATA = ROOT / "data"


def _load_env():
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env()


def _yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@lru_cache
def settings() -> dict:
    return _yaml(CONFIG / "settings.yaml")


@lru_cache
def rules() -> dict:
    return _yaml(CONFIG / "rules.yaml")


@lru_cache
def drugs() -> dict:
    return _yaml(CONFIG / "drugs.yaml")


@lru_cache
def redflags() -> dict:
    return _yaml(CONFIG / "redflags.yaml")


@lru_cache
def text_th() -> dict:
    return _yaml(CONFIG / "text_th.yaml")


@lru_cache
def text_en() -> dict:
    return _yaml(CONFIG / "text_en.yaml")


@lru_cache
def doctor_th() -> dict:
    path = CONFIG / "doctor_th.yaml"
    return (_yaml(path) or {}) if path.exists() else {}


# Language of the current request: set per request from a cookie (app/main.py). Patient app defaults to Thai,
# doctor app to English. Patient wording: text_th.yaml / text_en.yaml (same keys). Doctor wording: English in the
# templates, Thai in doctor_th.yaml keyed by the English text; anything missing stays English.
LANGS = ("th", "en")
LANG = contextvars.ContextVar("lang", default="th")


def text() -> dict:
    """Patient wording in the current language. A key missing from text_en.yaml falls back to Thai."""
    return text_th() if LANG.get() == "th" else {**text_th(), **text_en()}


def tr(s: str) -> str:
    """Doctor wording: s is the English text; returns the Thai from doctor_th.yaml when Thai is chosen."""
    return doctor_th().get(s, s) if LANG.get() == "th" else s


@lru_cache
def guidelines() -> dict:
    return _yaml(DATA / "seed" / "guidelines.yaml")


def reload():
    """Clear cached config so edits to YAML files apply without restarting."""
    for fn in (settings, rules, drugs, redflags, text_th, text_en, doctor_th, guidelines):
        fn.cache_clear()


def today() -> dt.date:
    fixed = settings()["app"].get("demo_today")
    return dt.date.fromisoformat(fixed) if fixed else dt.date.today()


def now() -> dt.datetime:
    """Real clock time on the app's 'today' date."""
    t = dt.datetime.now()
    return dt.datetime.combine(today(), t.time().replace(microsecond=0))


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)
