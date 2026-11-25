"""Brave discovery: roots, names, last used, login hint, running guess, end-to-end scan."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from fanout import brave


def make_root(tmp_path: Path, local_state: dict | None = None) -> Path:
    """Build a fake Linux Brave data folder under a fake home and return its root."""
    root = tmp_path / ".config" / "BraveSoftware" / "Brave-Browser"
    root.mkdir(parents=True)
    if local_state is not None:
        (root / "Local State").write_text(json.dumps(local_state), encoding="utf-8")
    return root


LOCAL_STATE = {
    "profile": {
        "last_used": "Profile 1",
        "info_cache": {
            "Default": {"name": "Personal", "active_time": 1_700_000_000.0},
            "Profile 1": {"name": "", "gaia_name": "Work Account", "user_name": "me@work.example"},
            "Profile 2": {"user_name": "third@example.com"},
        },
    }
}


def test_candidate_roots_per_os(tmp_path: Path) -> None:
    home = tmp_path / "home"
    local = tmp_path / "Local"
    win = brave.candidate_roots("win32", {"LOCALAPPDATA": str(local)}, home)
    assert win[0] == local / "BraveSoftware" / "Brave-Browser" / "User Data"
    assert [p.parent.name for p in win] == ["Brave-Browser", "Brave-Browser-Beta", "Brave-Browser-Nightly"]
    mac = brave.candidate_roots("darwin", {}, home)
    assert mac[0] == home / "Library" / "Application Support" / "BraveSoftware" / "Brave-Browser"
    linux = {str(p.relative_to(home)) for p in brave.candidate_roots("linux", {}, home)}
    assert ".config/BraveSoftware/Brave-Browser" in linux
    assert ".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser" in linux
    assert "snap/brave/current/.config/BraveSoftware/Brave-Browser" in linux


def test_profile_folders_order_and_filtering(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    for name in ("Profile 10", "Profile 2", "Default", "Profile 1", "Guest Profile", "System Profile", "Crashpad"):
        (root / name).mkdir()
    (root / "Profile 3").write_text("a file, not a folder")
    assert brave.profile_folders(root) == ["Default", "Profile 1", "Profile 2", "Profile 10"]


def test_profile_name_priority(tmp_path: Path) -> None:
    root = make_root(tmp_path, LOCAL_STATE)
    assert brave.profile_name(root, "Default", LOCAL_STATE) == "Personal"
    assert brave.profile_name(root, "Profile 1", LOCAL_STATE) == "Work Account"
    assert brave.profile_name(root, "Profile 2", LOCAL_STATE) == "third@example.com"


def test_profile_name_falls_back_to_preferences_then_folder(tmp_path: Path) -> None:
    root = make_root(tmp_path, {})
    (root / "Profile 4").mkdir()
    (root / "Profile 4" / "Preferences").write_text(json.dumps({"profile": {"name": "From Prefs"}}))
    (root / "Profile 5").mkdir()
    assert brave.profile_name(root, "Profile 4", {}) == "From Prefs"
    assert brave.profile_name(root, "Profile 5", {}) == "Profile 5"


def test_profile_name_survives_damaged_files(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    (root / "Local State").write_text("{not json")
    (root / "Default").mkdir()
    (root / "Default" / "Preferences").write_text("[]")
    assert brave.profile_name(root, "Default", brave._read_json(root / "Local State")) == "Default"


def test_last_used_prefers_active_time_then_mtime(tmp_path: Path) -> None:
    root = make_root(tmp_path, LOCAL_STATE)
    (root / "Default").mkdir()
    (root / "Profile 1").mkdir()
    assert brave.profile_last_used(root, "Default", LOCAL_STATE) == datetime.fromtimestamp(1_700_000_000.0)
    fallback = brave.profile_last_used(root, "Profile 1", LOCAL_STATE)
    assert fallback is not None
    assert abs(fallback.timestamp() - (root / "Profile 1").stat().st_mtime) < 1
    assert brave.profile_last_used(root, "Missing", {}) is None


def test_login_hint(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    (root / "Default" / "Network").mkdir(parents=True)
    assert brave.login_probably_saved(root, "Default") is False
    (root / "Default" / "Network" / "Cookies").write_bytes(b"")
    assert brave.login_probably_saved(root, "Default") is False
    (root / "Default" / "Network" / "Cookies").write_bytes(b"SQLite format 3")
    assert brave.login_probably_saved(root, "Default") is True


def test_running_by_profile_directory_flag(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    cmds = [["/usr/bin/brave", "--profile-directory=Profile 2"]]
    assert brave.running_folders(root, cmds, {}) == {"Profile 2"}


def test_running_ignores_other_user_data_dirs(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    cmds = [["brave", f"--user-data-dir={tmp_path / 'FanOut' / 'sessions' / 'account_1'}"]]
    assert brave.running_folders(root, cmds, {}) == set()
    cmds = [["brave", f"--user-data-dir={root}", "--profile-directory=Default"]]
    assert brave.running_folders(root, cmds, {}) == {"Default"}


def test_running_without_flag_uses_last_used_profile(tmp_path: Path) -> None:
    root = make_root(tmp_path, LOCAL_STATE)
    assert brave.running_folders(root, [["brave"]], LOCAL_STATE) == {"Profile 1"}
    assert brave.running_folders(root, [["brave"]], {}) == {"Default"}


@pytest.mark.skipif(os.name == "nt", reason="SingletonLock is a symlink on Linux/macOS")
def test_stale_and_live_singleton_lock(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    lock = root / "SingletonLock"
    lock.symlink_to("somehost-999999999")
    assert brave.lock_looks_live(root) is False
    assert brave.running_folders(root, [], {}) == set()
    lock.unlink()
    lock.symlink_to(f"somehost-{os.getpid()}")
    assert brave.lock_looks_live(root) is True
    assert brave.running_folders(root, [], {}) == {"Default"}


def test_find_brave_exe(tmp_path: Path) -> None:
    program_files = tmp_path / "PF"
    exe = program_files / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    assert brave.find_brave_exe("win32", {"PROGRAMFILES": str(program_files)}, tmp_path) == exe
    assert brave.find_brave_exe("linux", {}, tmp_path, which=lambda name: None) is None
    found = brave.find_brave_exe("linux", {}, tmp_path, which=lambda n: "/opt/x/brave" if n == "brave" else None)
    assert found == Path("/opt/x/brave")


def test_scan_end_to_end(tmp_path: Path) -> None:
    root = make_root(tmp_path, LOCAL_STATE)
    for folder in ("Default", "Profile 1", "Profile 2"):
        (root / folder).mkdir()
    (root / "Default" / "Network").mkdir()
    (root / "Default" / "Network" / "Cookies").write_bytes(b"SQLite format 3")
    result = brave.scan("linux", {}, tmp_path, cmdlines=[["brave", "--profile-directory=Profile 1"]])
    assert result.roots == [root]
    assert [(p.folder, p.name) for p in result.profiles] == [
        ("Default", "Personal"),
        ("Profile 1", "Work Account"),
        ("Profile 2", "third@example.com"),
    ]
    assert [p.appears_running for p in result.profiles] == [False, True, False]
    assert [p.login_saved for p in result.profiles] == [True, False, False]


def test_scan_with_no_brave(tmp_path: Path) -> None:
    result = brave.scan("linux", {}, tmp_path, cmdlines=[])
    assert result.roots == [] and result.profiles == []


def test_profiles_as_text_uses_guess_wording(tmp_path: Path) -> None:
    root = make_root(tmp_path, LOCAL_STATE)
    (root / "Default").mkdir()
    result = brave.scan("linux", {}, tmp_path, cmdlines=[["brave", "--profile-directory=Default"]])
    text = brave.profiles_as_text(result.profiles)
    assert "appears to be running" in text
    assert "Personal" in text and "Default" in text
