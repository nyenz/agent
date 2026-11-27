"""Settings checks and the repair helper."""

from __future__ import annotations

from pathlib import Path

from fanout import updater
from fanout.config import Config, _clean
from fanout.settings import apply_settings, parse_settings


def test_good_numbers_pass_and_comma_decimals_work() -> None:
    values, problems = parse_settings("3", "180", "5")
    assert problems == [] and values is not None
    assert (values.stable_seconds, values.timeout_seconds, values.min_delay_seconds) == (3.0, 180, 5.0)
    values, _ = parse_settings("2,5", " 240 ", "0")
    assert values is not None and values.stable_seconds == 2.5 and values.timeout_seconds == 240


def test_limits_match_the_spec() -> None:
    assert parse_settings("1", "1800", "0")[0] is not None
    assert parse_settings("30", "10", "300")[0] is not None
    for stable, timeout, delay in (("0.5", "180", "5"), ("31", "180", "5"), ("3", "9", "5"), ("3", "1801", "5"), ("3", "180", "-1")):
        values, problems = parse_settings(stable, timeout, delay)
        assert values is None and len(problems) == 1


def test_every_problem_is_listed_in_plain_words() -> None:
    values, problems = parse_settings("x", "", "99999")
    assert values is None and len(problems) == 3
    assert all("Traceback" not in p and "ValueError" not in p for p in problems)


def test_apply_settings_updates_the_config_and_survives_a_reload() -> None:
    cfg = Config()
    values, _ = parse_settings("7", "300", "12")
    assert values is not None
    apply_settings(cfg, values, be_gentle=True, check_updates=False)
    again = _clean({"stable_seconds": cfg.stable_seconds, "timeout_seconds": cfg.timeout_seconds,
                    "min_delay_seconds": cfg.min_delay_seconds, "be_gentle": cfg.be_gentle, "check_updates": cfg.check_updates,
                    "skipped_version": "1.2.3"})
    assert (again.stable_seconds, again.timeout_seconds, again.min_delay_seconds) == (7.0, 300, 12.0)
    assert again.be_gentle is True and again.check_updates is False and again.skipped_version == "1.2.3"


def test_old_config_files_still_load_with_the_new_fields() -> None:
    cfg = _clean({"num_accounts": 2})
    assert cfg.check_updates is True and cfg.skipped_version == ""


def test_repair_runs_bootstrap_repair_without_launching(tmp_path: Path) -> None:
    (tmp_path / "bootstrap.py").write_text("import sys\nprint(' '.join(sys.argv[1:]))\n")
    ok, tail = updater.repair_installation(tmp_path)
    assert ok and tail == "repair --no-launch --no-dialog --no-shortcut"
    (tmp_path / "bootstrap.py").write_text("import sys\nprint('broke')\nsys.exit(2)\n")
    assert updater.repair_installation(tmp_path) == (False, "broke")
    (tmp_path / "bootstrap.py").unlink()
    ok, tail = updater.repair_installation(tmp_path)
    assert not ok and "INSTALL" in tail
