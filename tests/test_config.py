"""Config, logging setup and error wording."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from fanout import config, errors, logging_setup


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep every test away from real user data."""
    monkeypatch.setenv("FANOUT_HOME", str(tmp_path / "FanOutHome"))
    return tmp_path / "FanOutHome"


def test_app_data_dir_honours_override(isolated_home: Path) -> None:
    assert config.app_data_dir() == isolated_home
    assert isolated_home.is_dir()


def test_defaults_roundtrip() -> None:
    cfg = config.Config(num_accounts=4, selected_profiles=["Brave/Default"], be_gentle=True)
    config.save_config(cfg)
    assert config.load_config() == cfg
    assert not list(config.app_data_dir().glob("*.tmp"))


def test_missing_file_gives_defaults() -> None:
    assert config.load_config() == config.Config()


def test_values_are_clamped_and_bad_types_ignored() -> None:
    raw = {
        "version": 1,
        "num_accounts": 99,
        "stable_seconds": 0.1,
        "timeout_seconds": 999999,
        "min_delay_seconds": -3,
        "be_gentle": "yes",
        "selected_profiles": [f"p{i}" for i in range(10)],
        "unknown_key": 1,
    }
    config.config_path().write_text(json.dumps(raw))
    cfg = config.load_config()
    assert cfg.num_accounts == config.MAX_ACCOUNTS
    assert cfg.stable_seconds == 1.0
    assert cfg.timeout_seconds == 1800
    assert cfg.min_delay_seconds == 0.0
    assert cfg.be_gentle is False
    assert len(cfg.selected_profiles) == config.MAX_ACCOUNTS


def test_old_file_without_version_is_migrated() -> None:
    config.config_path().write_text(json.dumps({"num_accounts": 2}))
    cfg = config.load_config()
    assert cfg.num_accounts == 2
    assert cfg.version == config.CONFIG_VERSION


def test_migration_steps_run_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "CONFIG_VERSION", 2)
    monkeypatch.setitem(config.MIGRATIONS, 1, lambda raw: {**raw, "migrated": True})
    out = config.migrate({"version": 1})
    assert out == {"version": 2, "migrated": True}


def test_damaged_file_is_set_aside() -> None:
    path = config.config_path()
    path.write_text("{ definitely not json")
    assert config.load_config() == config.Config()
    assert not path.exists()
    assert path.with_suffix(".json.damaged").exists()


def test_logging_uses_rotating_file_handler() -> None:
    path = logging_setup.setup_logging()
    root = logging.getLogger()
    handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
    try:
        assert path == config.app_data_dir() / "logs" / "fanout.log"
        assert handlers and handlers[-1].maxBytes == 1_000_000 and handlers[-1].backupCount == 5
        logging.getLogger("fanout.test").error("hello log")
        handlers[-1].flush()
        assert "hello log" in path.read_text(encoding="utf-8")
    finally:
        for handler in handlers:
            root.removeHandler(handler)
            handler.close()


def test_friendly_errors_never_contain_traceback() -> None:
    for exc in (PermissionError("x"), FileNotFoundError("y"), OSError("z"), RuntimeError("boom")):
        try:
            raise exc
        except Exception as caught:  # noqa: BLE001 - the test feeds describe() every kind of error
            text = errors.describe(caught).text()
        assert "Traceback" not in text and "boom" not in text
        assert "What to try:" in text


def test_traceback_goes_to_log_only() -> None:
    logging_setup.setup_logging()
    root = logging.getLogger()
    try:
        try:
            raise RuntimeError("secret-detail")
        except RuntimeError as exc:
            shown = errors.describe(exc).text()
        for handler in root.handlers:
            handler.flush()
        logged = (config.app_data_dir() / "logs" / "fanout.log").read_text(encoding="utf-8")
        assert "Traceback" in logged and "secret-detail" in logged
        assert "secret-detail" not in shown
    finally:
        for handler in [h for h in root.handlers if isinstance(h, RotatingFileHandler)]:
            root.removeHandler(handler)
            handler.close()
