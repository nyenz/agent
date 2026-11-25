"""Settings stored as JSON in the per-user app data folder, with versioned migrations."""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CONFIG_VERSION = 1
MAX_ACCOUNTS = 6


@dataclass
class Config:
    """User settings. Never holds passwords, cookies, prompts or answers."""

    version: int = CONFIG_VERSION
    num_accounts: int = 3
    mode: str = "app_managed"  # "app_managed" or "reuse_profiles"
    selected_profiles: list[str] = field(default_factory=list)
    use_bundled_chromium: bool = False
    min_delay_seconds: float = 5.0
    be_gentle: bool = False
    stable_seconds: float = 3.0
    timeout_seconds: int = 180
    first_run_notice_dismissed: bool = False


def app_data_dir() -> Path:
    """Return the per-user FanOut data folder (created on demand).

    FANOUT_HOME overrides the location, which keeps tests and demos away from real data.
    """
    override = os.environ.get("FANOUT_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "FanOut"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "FanOut"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "FanOut"
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_path() -> Path:
    """Return the location of config.json."""
    return app_data_dir() / "config.json"


# Each migration upgrades a raw dict from version N to N+1. Add new ones here.
MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def migrate(raw: dict[str, Any]) -> dict[str, Any]:
    """Apply migrations until the dict reaches CONFIG_VERSION."""
    version = raw.get("version", 0)
    if not isinstance(version, int) or version < 0:
        version = 0
    while version < CONFIG_VERSION:
        step = MIGRATIONS.get(version)
        if step is not None:
            raw = step(raw)
        version += 1
        raw["version"] = version
    return raw


def _clean(raw: dict[str, Any]) -> Config:
    """Build a Config from raw JSON, ignoring unknown keys and wrong-typed values."""
    defaults = Config()
    values: dict[str, Any] = {}
    for f in fields(Config):
        if f.name not in raw:
            continue
        value = raw[f.name]
        expected = type(getattr(defaults, f.name))
        if expected is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if isinstance(value, expected):
            values[f.name] = value
    cfg = Config(**values)
    cfg.num_accounts = max(1, min(MAX_ACCOUNTS, cfg.num_accounts))
    cfg.selected_profiles = [str(p) for p in cfg.selected_profiles][:MAX_ACCOUNTS]
    cfg.min_delay_seconds = max(0.0, cfg.min_delay_seconds)
    cfg.stable_seconds = max(1.0, min(30.0, cfg.stable_seconds))
    cfg.timeout_seconds = max(10, min(1800, cfg.timeout_seconds))
    return cfg


def load_config(path: Path | None = None) -> Config:
    """Load settings, falling back to defaults. A damaged file is set aside, not deleted."""
    path = path or config_path()
    if not path.exists():
        return Config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TypeError("config.json is not a JSON object")
        return _clean(migrate(raw))
    except (OSError, ValueError, TypeError) as exc:
        log.warning("Could not read %s (%s); using defaults", path, exc)
        try:
            path.replace(path.with_suffix(".json.damaged"))
        except OSError:
            log.warning("Could not set the damaged settings file aside", exc_info=True)
        return Config()


def save_config(cfg: Config, path: Path | None = None) -> None:
    """Write settings atomically (temp file, then replace)."""
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix="config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(asdict(cfg), handle, indent=2)
        os.replace(tmp_name, path)
    except OSError:
        Path(tmp_name).unlink(missing_ok=True)
        raise
