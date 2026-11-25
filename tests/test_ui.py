"""Scan-screen behaviour. Needs a display; skipped automatically when there is none."""

from __future__ import annotations

import time
import tkinter as tk
from pathlib import Path

import pytest

from fanout.brave import BraveProfile, ScanResult
from fanout.config import MAX_ACCOUNTS, Config, load_config, save_config
from fanout.ui import FanOutApp, profile_key


def fake_scan(count: int, exe: bool = True) -> ScanResult:
    profiles = [
        BraveProfile(
            folder="Default" if i == 0 else f"Profile {i}",
            name=f"Person {i}",
            path=Path("/x"),
            root=Path("/x"),
            channel="Brave",
            last_used=None,
            appears_running=False,
            login_saved=False,
        )
        for i in range(count)
    ]
    return ScanResult(roots=[Path("/x")], profiles=profiles, exe=Path("/usr/bin/brave") if exe else None)


@pytest.fixture
def root() -> tk.Tk:
    try:
        window = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    yield window
    window.destroy()


def open_app(root: tk.Tk, cfg: Config, result: ScanResult, saved: list[Config]) -> FanOutApp:
    screens: list[str] = []
    app = FanOutApp(root, cfg, save=lambda c: saved.append(c), scan_fn=lambda: result, on_screen=lambda n, d: screens.append(n))
    deadline = time.time() + 5
    while not screens and time.time() < deadline:
        root.update()
        time.sleep(0.02)
    assert screens, "no screen was shown"
    app.screens = screens  # type: ignore[attr-defined]
    return app


def test_default_three_ticked_and_max_six_enforced(root: tk.Tk) -> None:
    app = open_app(root, Config(first_run_notice_dismissed=True), fake_scan(8), [])
    assert app.screens == ["scan"]  # type: ignore[attr-defined]
    assert len(app.picked) == 3
    for p in app.result.profiles:  # type: ignore[union-attr]
        if profile_key(p) not in app.picked:
            app.toggle(profile_key(p))
    assert len(app.picked) == MAX_ACCOUNTS
    assert "up to 6" in app.status_var.get()
    app.toggle(app.picked[0])
    assert len(app.picked) == MAX_ACCOUNTS - 1


def test_save_selection_persists(root: tk.Tk, tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    app = open_app(root, Config(first_run_notice_dismissed=True), fake_scan(4), [])
    app.save = lambda c: save_config(c, path)
    app.toggle(app.picked[0])
    app.save_selection()
    cfg = load_config(path)
    assert cfg.num_accounts == 2
    assert cfg.selected_profiles == app.picked


def test_nothing_ticked_blocks_save(root: tk.Tk) -> None:
    saved: list[Config] = []
    app = open_app(root, Config(first_run_notice_dismissed=True), fake_scan(2), saved)
    for key in list(app.picked):
        app.toggle(key)
    app.save_selection()
    assert saved == []
    assert "at least one" in app.status_var.get()


def test_missing_brave_screen_then_bundled_choice(root: tk.Tk) -> None:
    saved: list[Config] = []
    app = open_app(root, Config(first_run_notice_dismissed=True), fake_scan(0, exe=False), saved)
    assert app.screens == ["brave-missing"]  # type: ignore[attr-defined]
    app.use_bundled()
    assert saved and saved[-1].use_bundled_chromium is True
    assert app.accounts_var.get() == 3


def test_notice_dismiss_is_remembered(root: tk.Tk) -> None:
    saved: list[Config] = []
    cfg = Config()
    open_app(root, cfg, fake_scan(1), saved)
    buttons = [w for w in root.winfo_children()[0].winfo_children()[0].winfo_children() if w.winfo_class() == "TButton"]
    assert len(buttons) == 1
    buttons[0].invoke()
    assert cfg.first_run_notice_dismissed is True and saved
