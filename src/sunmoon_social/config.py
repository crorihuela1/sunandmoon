"""Load and validate the YAML configs that drive the engine."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG_DIR = Path(os.environ.get("SUNMOON_CONFIG_DIR", Path(__file__).resolve().parents[2] / "config"))
STATE_DIR = Path(os.environ.get("SUNMOON_STATE_DIR", Path(__file__).resolve().parents[2] / "state"))
QUEUE_DIR = Path(os.environ.get("SUNMOON_QUEUE_DIR", Path(__file__).resolve().parents[2] / "queue"))

ACTIVE = "active"
STATUSES = ("planned", "applied", "keys_received", "vetted", "active", "paused")


@lru_cache(maxsize=None)
def _load(name: str) -> dict:
    """Cached for the life of the process: a single CLI run reads these files
    many times (once per calendar event, for the property timezone) and they do
    not change underneath it."""
    path = CONFIG_DIR / name
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_apis() -> dict:
    data = _load("apis.yaml")
    for group in ("platforms", "aggregators"):
        for key, cfg in (data.get(group) or {}).items():
            status = cfg.get("status")
            if status not in STATUSES:
                raise ValueError(f"{group}.{key}: invalid status {status!r} (expected one of {STATUSES})")
    return data


def load_brand() -> dict:
    return _load("brand.yaml")


def load_calendars() -> dict:
    return _load("calendars.yaml")


def load_notifications() -> dict:
    return _load("notifications.yaml")


def active_platforms(apis: dict | None = None) -> dict:
    """Only platforms the engine is allowed to publish to."""
    apis = apis or load_apis()
    out = {}
    for group in ("platforms", "aggregators"):
        for key, cfg in (apis.get(group) or {}).items():
            if cfg.get("status") == ACTIVE:
                out[key] = cfg
    return out


def missing_secrets(cfg: dict) -> list[str]:
    return [name for name in cfg.get("secrets", []) if not os.environ.get(name)]
