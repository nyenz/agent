"""Tests for the installer/repair tool (bootstrap.py) and the launcher scripts."""

from __future__ import annotations

import importlib.metadata as md
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import bootstrap

REPO = Path(bootstrap.__file__).resolve().parent


def test_requirements_are_all_pinned_and_parse() -> None:
    lines = bootstrap.read_requirements(REPO / "requirements.txt")
    assert lines, "requirements.txt must not be empty"
    for line in lines:
        name, version = bootstrap.split_requirement(line)
        assert version, f"{name} must be pinned with =="
    assert {bootstrap.split_requirement(x)[0] for x in lines} >= {"playwright", "psutil", "pyperclip"}


def test_split_requirement_and_comments(tmp_path: Path) -> None:
    f = tmp_path / "r.txt"
    f.write_text("# top\n\nPlay_Wright==1.2.3  # why\n-r other.txt\nfoo\n", encoding="utf-8")
    assert bootstrap.read_requirements(f) == ["Play_Wright==1.2.3", "foo"]
    assert bootstrap.split_requirement("Play_Wright==1.2.3") == ("play-wright", "1.2.3")
    assert bootstrap.split_requirement("foo") == ("foo", None)


def test_state_round_trip_and_bad_file(tmp_path: Path) -> None:
    assert bootstrap.load_state(tmp_path) == {}
    bootstrap.save_state({"a": 1}, tmp_path)
    assert bootstrap.load_state(tmp_path) == {"a": 1}
    assert not list(tmp_path.glob(".state-*")), "no temporary file may be left behind"
    (tmp_path / bootstrap.STATE_NAME).write_text("{not json", encoding="utf-8")
    assert bootstrap.load_state(tmp_path) == {}


def test_package_problems_names_missing_and_wrong_versions() -> None:
    have = md.version("psutil")
    py = Path(sys.executable)
    assert bootstrap.package_problems(py, [f"psutil=={have}"]) == []
    assert bootstrap.package_problems(py, ["psutil==0.0.1"]) == ["psutil"]
    assert bootstrap.package_problems(py, ["definitely-not-installed-xyz==1.0"]) == ["definitely-not-installed-xyz"]
    assert bootstrap.package_problems(Path("/no/such/python"), ["psutil==1"]) == ["psutil"]


def test_shortcut_text_for_each_system() -> None:
    root = Path("/home/o'brien/FanOut")
    entry = bootstrap.desktop_entry(root)
    assert "Exec=" in entry and "run.command" in entry and "Terminal=false" in entry
    assert bootstrap.mac_launcher(root).startswith("#!/bin/bash")
    script = bootstrap.windows_shortcut_script(root)
    assert "o''brien" in script, "a quote in the folder name must be doubled for PowerShell"
    assert "GetFolderPath('Desktop')" in script and "run.bat" in script


def test_launcher_scripts_exist_and_are_well_formed() -> None:
    for name in ("INSTALL.bat", "run.bat"):
        raw = (REPO / name).read_bytes()
        assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""), f"{name} needs Windows line endings"
        raw.decode("ascii")  # no fancy characters that old consoles garble
    install = (REPO / "INSTALL.bat").read_text(encoding="ascii")
    for needle in ("winget install -e --id Python.Python.3.12", "PrependPath=1", "bootstrap.py install", ":refreshpath"):
        assert needle in install
    assert "bootstrap.py check" in (REPO / "run.bat").read_text(encoding="ascii")
    for name in ("install.command", "run.command"):
        path = REPO / name
        assert path.read_text(encoding="utf-8").startswith("#!/bin/bash")
        if sys.platform != "win32":
            assert path.stat().st_mode & 0o111, f"{name} must be executable so double-click works"
            if shutil.which("bash"):
                assert subprocess.run(["bash", "-n", str(path)], check=False).returncode == 0


def test_every_launch_path_runs_the_check_before_the_app() -> None:
    assert (REPO / "run.command").read_text().index("bootstrap.py check") < (REPO / "run.command").read_text().index("app.py")
    bat = (REPO / "run.bat").read_text()
    assert bat.index("bootstrap.py check") < bat.index("app.py")


# ----- a real install and a real repair, offline (system packages are visible inside the new .venv) -----------------


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "FanOut"
    root.mkdir()
    shutil.copy(REPO / "bootstrap.py", root / "bootstrap.py")
    (root / "requirements.txt").write_text(f"psutil=={md.version('psutil')}\n", encoding="utf-8")
    monkeypatch.setenv("FANOUT_VENV_SYSTEM_SITE", "1")
    monkeypatch.setattr(bootstrap, "tk_works", lambda _py: True)  # this sandbox has no Tkinter
    return root


def test_install_builds_the_venv_and_writes_state(fake_home: Path) -> None:
    ok, message = bootstrap.install_all(fake_home, browser=False, shortcut=False, quiet=True)
    assert ok, message
    assert bootstrap.venv_works(fake_home)
    state = bootstrap.load_state(fake_home)
    assert state["requirements_hash"] == bootstrap.file_hash(fake_home / "requirements.txt")
    assert bootstrap.needs_repair(fake_home) == ""
    assert bootstrap.check(fake_home) == 0, "a healthy install must pass the launch check silently"


def test_a_broken_venv_is_noticed_and_rebuilt(fake_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert bootstrap.install_all(fake_home, browser=False, shortcut=False, quiet=True)[0]
    bootstrap.venv_python(fake_home).unlink()  # simulate a damaged install
    assert "broken" in bootstrap.needs_repair(fake_home)
    assert bootstrap.check(fake_home) == 0
    assert "Fixing FanOut's setup" in capsys.readouterr().out
    assert bootstrap.venv_works(fake_home) and bootstrap.needs_repair(fake_home) == ""


def test_a_changed_package_list_triggers_a_repair(fake_home: Path) -> None:
    assert bootstrap.install_all(fake_home, browser=False, shortcut=False, quiet=True)[0]
    (fake_home / "requirements.txt").write_text(f"psutil=={md.version('psutil')}\n# bumped\n", encoding="utf-8")
    assert "package list changed" in bootstrap.needs_repair(fake_home)


def test_a_failed_package_install_gives_a_plain_message(fake_home: Path) -> None:
    (fake_home / "requirements.txt").write_text("psutil==0.0.1\n", encoding="utf-8")
    ok, message = bootstrap.install_all(fake_home, browser=False, shortcut=False, quiet=True)
    assert not ok and "internet connection" in message and "Traceback" not in message


def test_missing_tkinter_stops_with_the_fix(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bootstrap, "tk_works", lambda _py: False)
    ok, message = bootstrap.install_all(fake_home, browser=False, shortcut=False, quiet=True)
    assert not ok and "Tkinter" in message and ("install" in message.lower())
