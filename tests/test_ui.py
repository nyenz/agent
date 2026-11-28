"""Scan-screen behaviour. Needs a display; skipped automatically when there is none."""

from __future__ import annotations

import gc
import time
from pathlib import Path
from typing import Any

import pytest

tk = pytest.importorskip("tkinter", reason="Tkinter is not installed here")

from fanout.brave import BraveProfile, ScanResult  # noqa: E402
from fanout.config import MAX_ACCOUNTS, Config, load_config, save_config  # noqa: E402
from fanout.ui import FanOutApp, profile_key  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")  # Tk variables freed after the window


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


_ROOTS: list[tk.Tk] = []


def _alive(window: tk.Tk) -> bool:
    try:
        return bool(window.winfo_exists())
    except tk.TclError:
        return False


@pytest.fixture(scope="module", autouse=True)
def _close_shared_root_at_the_end() -> None:
    yield
    for window in _ROOTS:
        if _alive(window):
            window.destroy()
    _ROOTS.clear()


@pytest.fixture
def root() -> tk.Tk:
    """One Tk root shared by the tests in this file (making and destroying many in one process crashes Tk on macOS).

    A test that closes the whole app destroys it, so a new one is made only then. It is emptied after each test.
    """
    if not _ROOTS or not _alive(_ROOTS[-1]):
        try:
            _ROOTS.append(tk.Tk())
        except tk.TclError:
            pytest.skip("no display available")
    window = _ROOTS[-1]
    yield window
    if _alive(window):
        for job in window.tk.splitlist(window.tk.call("after", "info")):
            window.after_cancel(job)
        for child in window.winfo_children():
            child.destroy()
        window.update_idletasks()
    gc.collect()


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


class FakeWorker:
    """Records commands instead of opening browsers."""

    def __init__(self, events: Any) -> None:
        self.events, self.commands, self.final_snapshot = events, [], {}

    def start(self) -> None:
        return None

    def submit(self, command: str, **payload: Any) -> None:
        self.commands.append((command, payload))

    def shutdown(self, timeout: float = 20.0) -> bool:
        self.commands.append(("shutdown", {}))
        return True


def open_with_worker(root: tk.Tk, cfg: Config, result: ScanResult) -> FanOutApp:
    screens: list[str] = []
    app = FanOutApp(
        root, cfg, save=lambda c: None, scan_fn=lambda: result,
        on_screen=lambda n, d: screens.append(n), worker_factory=FakeWorker,
    )
    deadline = time.time() + 5
    while not screens and time.time() < deadline:
        root.update()
        time.sleep(0.02)
    app.screens = screens  # type: ignore[attr-defined]
    return app


def test_continue_opens_one_window_per_picked_profile(root: tk.Tk) -> None:
    app = open_with_worker(root, Config(first_run_notice_dismissed=True), fake_scan(5))
    app.continue_clicked()
    command, payload = app.worker.commands[-1]  # type: ignore[union-attr]
    assert command == "open_windows"
    assert [s.number for s in payload["specs"]] == [1, 2, 3]
    assert payload["exe"] == Path("/usr/bin/brave")
    assert payload["screen"][0] > 0
    assert payload["attach"] is True, "compatibility mode is on by default"
    assert list(app.slot_vars) == [1, 2, 3]


def test_turning_compatibility_mode_off_opens_windows_the_standard_way(root: tk.Tk) -> None:
    app = open_with_worker(root, Config(first_run_notice_dismissed=True, compatibility_mode=False), fake_scan(2))
    app.continue_clicked()
    assert app.worker.commands[-1][1]["attach"] is False  # type: ignore[union-attr]


def test_bundled_chromium_means_no_executable(root: tk.Tk) -> None:
    app = open_with_worker(root, Config(first_run_notice_dismissed=True, use_bundled_chromium=True), fake_scan(2))
    app.continue_clicked()
    assert app.worker.commands[-1][1]["exe"] is None  # type: ignore[union-attr]


def test_import_logins_choice_is_passed_to_specs(root: tk.Tk) -> None:
    app = open_with_worker(root, Config(first_run_notice_dismissed=True, import_logins=True), fake_scan(2))
    app.continue_clicked()
    specs = app.worker.commands[-1][1]["specs"]  # type: ignore[union-attr]
    assert all(s.import_from is not None for s in specs)


def test_all_ready_enables_at_any_ready_count(root: tk.Tk) -> None:
    app = open_with_worker(root, Config(first_run_notice_dismissed=True), fake_scan(3))
    app.continue_clicked()
    assert str(app.continue_button["state"]) == "disabled"  # type: ignore[index]
    app.on_worker_event("slot_open", {"slot": 1})
    app.on_worker_event("slot_state", {"slot": 2, "ready": True, "host": "chat.example.com"})
    assert str(app.continue_button["state"]) == "normal"  # type: ignore[index]
    assert app.slot_vars[2].get() == "READY - chat.example.com"
    app.on_worker_event("slot_state", {"slot": 2, "ready": False, "host": "chat.example.com"})
    assert str(app.continue_button["state"]) == "disabled"  # type: ignore[index]


def test_slot_error_shows_message_and_fix_without_traceback(root: tk.Tk) -> None:
    app = open_with_worker(root, Config(first_run_notice_dismissed=True), fake_scan(2))
    app.continue_clicked()
    app.on_worker_event("slot_error", {"slot": 1, "message": "FanOut couldn't start the browser.", "fix": "Try again."})
    text = app.slot_vars[1].get()
    assert "couldn't start the browser" in text and "What to try: Try again." in text and "Traceback" not in text


def test_note_is_shown_under_the_status(root: tk.Tk) -> None:
    app = open_with_worker(root, Config(first_run_notice_dismissed=True), fake_scan(1))
    app.continue_clicked()
    app.on_worker_event("slot_note", {"slot": 1, "message": "just log in once, it'll be remembered"})
    app.on_worker_event("slot_state", {"slot": 1, "ready": False, "host": ""})
    assert "just log in once" in app.slot_vars[1].get()


def test_back_closes_windows_then_returns_to_scan(root: tk.Tk) -> None:
    app = open_with_worker(root, Config(first_run_notice_dismissed=True), fake_scan(2))
    app.continue_clicked()
    app.back_to_scan()
    assert app.worker.commands[-1][0] == "close_windows"  # type: ignore[union-attr]
    app.on_worker_event("windows_closed", {})
    assert app.tree is not None  # Scan screen is back


def test_snapshot_event_is_saved_to_config(root: tk.Tk) -> None:
    cfg = Config(first_run_notice_dismissed=True)
    app = open_with_worker(root, cfg, fake_scan(1))
    app.on_worker_event("snapshot", {"window_geometry": {"1": [1, 2, 3, 4]}, "last_urls": {"1": "https://a.example"}})
    assert cfg.window_geometry == {"1": [1, 2, 3, 4]} and cfg.last_urls == {"1": "https://a.example"}


def test_closing_the_app_shuts_the_worker_down_first(root: tk.Tk) -> None:
    app = open_with_worker(root, Config(first_run_notice_dismissed=True), fake_scan(1))
    app.continue_clicked()
    worker = app.worker
    app.worker.final_snapshot = {"window_geometry": {"1": [9, 9, 9, 9]}}  # type: ignore[union-attr]
    app.close()
    assert worker.commands[-1] == ("shutdown", {})  # type: ignore[union-attr]
    assert app.cfg.window_geometry == {"1": [9, 9, 9, 9]}
    root.destroy = lambda: None  # type: ignore[method-assign]  # fixture destroys the already-closed window


def test_reuse_mode_with_brave_running_offers_the_three_choices(root: tk.Tk, monkeypatch: pytest.MonkeyPatch) -> None:
    import fanout.ui as ui_module

    monkeypatch.setattr(ui_module, "running_brave_cmdlines", lambda: [["brave"]])
    app = open_with_worker(root, Config(first_run_notice_dismissed=True, mode="reuse_profiles"), fake_scan(2))
    asked: list[int] = []

    def fake_ask() -> str:
        asked.append(1)
        return "close"

    app._ask_brave_open = fake_ask  # type: ignore[method-assign]
    app.continue_clicked()
    assert asked == [1]
    assert app.worker.commands[-1][0] == "close_brave"  # type: ignore[union-attr]
    # Cancel does nothing at all
    app._ask_brave_open = lambda: "cancel"  # type: ignore[method-assign]
    before = len(app.worker.commands)  # type: ignore[union-attr]
    app.show_scan()
    app.continue_clicked()
    assert len(app.worker.commands) == before  # type: ignore[union-attr]
    # Managed windows instead
    app._ask_brave_open = lambda: "managed"  # type: ignore[method-assign]
    app.continue_clicked()
    assert app.worker.commands[-1][0] == "open_windows" and app.cfg.mode == "app_managed"  # type: ignore[union-attr]


def test_reuse_mode_confirms_then_plans_in_place_profiles(root: tk.Tk, monkeypatch: pytest.MonkeyPatch) -> None:
    import fanout.ui as ui_module

    monkeypatch.setattr(ui_module, "running_brave_cmdlines", list)
    monkeypatch.setattr(ui_module.messagebox, "askokcancel", lambda *a, **k: True)
    app = open_with_worker(root, Config(first_run_notice_dismissed=True, mode="reuse_profiles"), fake_scan(2))
    app.continue_clicked()
    command, payload = app.worker.commands[-1]  # type: ignore[union-attr]
    assert command == "open_windows"
    assert payload["specs"][0].profile_directory == "Default" and payload["specs"][1].temporary
    app._remove_temp_dir()


def _quiet_dialogs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    import fanout.ui as ui_module

    shown: list[str] = []
    monkeypatch.setattr(ui_module.messagebox, "showerror", lambda _t, text, **_k: shown.append(text))
    return shown


def test_a_failed_window_opening_closes_what_opened_before_going_back(root: tk.Tk, monkeypatch: pytest.MonkeyPatch) -> None:
    shown = _quiet_dialogs(monkeypatch)
    app = open_with_worker(root, Config(first_run_notice_dismissed=True), fake_scan(2))
    app.continue_clicked()
    app.on_worker_event("worker_error", {"message": "FanOut couldn't start the browser.", "fix": "Try again.", "command": "open_windows"})
    assert shown and "What to try: Try again." in shown[0]
    assert app.worker.commands[-1][0] == "close_windows"  # type: ignore[union-attr]
    assert app.after_windows_closed == app.show_scan


def test_a_failed_mapping_stays_on_the_login_screen(root: tk.Tk, monkeypatch: pytest.MonkeyPatch) -> None:
    shown = _quiet_dialogs(monkeypatch)
    app = open_with_worker(root, Config(first_run_notice_dismissed=True), fake_scan(2))
    app.continue_clicked()
    before = len(app.worker.commands)  # type: ignore[union-attr]
    app.on_worker_event("worker_error", {"message": "Mapping failed.", "fix": "Click the chat box.", "command": "map_account"})
    assert shown and list(app.slot_vars) == [1, 2], "the login rows must still be there"
    assert len(app.worker.commands) == before, "nothing may be closed or reopened"  # type: ignore[union-attr]


def test_a_failed_run_makes_send_usable_again(root: tk.Tk, monkeypatch: pytest.MonkeyPatch) -> None:
    _quiet_dialogs(monkeypatch)
    app = open_with_worker(root, Config(first_run_notice_dismissed=True), fake_scan(2))
    app.continue_clicked()
    app.on_worker_event("slot_state", {"slot": 1, "ready": True, "host": "chat.example.com"})
    app.finish_login()
    view = app.run_view
    assert view is not None
    view.broadcast.insert("1.0", "Hello")
    view.send()
    assert view.running and app.worker.commands[-1][0] == "send_all"  # type: ignore[union-attr]
    app.on_worker_event("worker_error", {"message": "Something unexpected went wrong.", "fix": "Try again.", "command": "send_all"})
    assert not view.running
    assert "disabled" not in view.send_button.state()
    assert "try again" in view.summary.get().lower()


# ----- updates that install by themselves ------------------------------------------------------


def _release(tag: str = "v9.9.9") -> Any:
    from fanout.updater import ReleaseInfo

    site = "https://x.example"
    return ReleaseInfo(tag, (9, 9, 9), "", site, f"{site}/z", f"{site}/s", f"{site}/setup", f"{site}/setup.sha")


class _Updates:
    """Stands in for the network and the installer. Records what the app asked for."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fail: bool = False) -> None:
        import fanout.ui as ui_module
        from fanout import updater
        from fanout.updater import UpdateError

        self.downloads = 0
        self.started: list[Path] = []
        self.asked: list[str] = []
        self.errors: list[str] = []
        setup = tmp_path / "Setup.exe"
        setup.write_bytes(b"MZ")

        def download(info: Any, progress: Any = None) -> Path:
            self.downloads += 1
            if fail:
                raise UpdateError("I couldn't download the update.")
            return setup

        monkeypatch.setattr(updater, "is_frozen", lambda: True)
        monkeypatch.setattr(updater, "can_self_install", lambda info: True)
        monkeypatch.setattr(updater, "download_installer", download)
        monkeypatch.setattr(updater, "start_installer", lambda path: self.started.append(path))
        monkeypatch.setattr(ui_module.messagebox, "askyesno", lambda *a, **k: self.asked.append("yes?") or True)
        monkeypatch.setattr(ui_module.messagebox, "showerror", lambda *a, **k: self.errors.append(str(a)))
        monkeypatch.setattr(ui_module.messagebox, "showinfo", lambda *a, **k: self.asked.append("info"))


def _wait(root: tk.Tk, done: Any, seconds: float = 5.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        root.update()
        if done():
            return True
        time.sleep(0.02)
    return False


def _app_for_updates(root: tk.Tk, saved: list[Config], **cfg_values: Any) -> FanOutApp:
    app = open_app(root, Config(first_run_notice_dismissed=True, **cfg_values), fake_scan(2), saved)
    app.close = lambda: None  # type: ignore[method-assign]  # the real one would destroy the shared window
    return app


def test_a_new_version_installs_by_itself_without_asking(root: tk.Tk, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _Updates(monkeypatch, tmp_path)
    saved: list[Config] = []
    app = _app_for_updates(root, saved)
    app.on_update_event("update_checked", (_release(), False))
    assert _wait(root, lambda: bool(fake.started)), "the installer was never started"
    assert fake.asked == [], "an automatic update must not ask anything"
    assert fake.errors == []
    assert app.cfg.auto_update_tried == "9.9.9" and saved, "the tried version is written down before the app closes"


def test_the_same_version_is_not_tried_twice_on_its_own(root: tk.Tk, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _Updates(monkeypatch, tmp_path)
    app = _app_for_updates(root, [], auto_update_tried="9.9.9")
    app.on_update_event("update_checked", (_release(), False))
    root.update()
    time.sleep(0.2)
    root.update()
    assert fake.downloads == 0 and app.update_bar is not None, "the green bar is still there for the person to use"


def test_nothing_happens_by_itself_when_auto_update_is_off(root: tk.Tk, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _Updates(monkeypatch, tmp_path)
    app = _app_for_updates(root, [], auto_update=False)
    app.on_update_event("update_checked", (_release(), False))
    time.sleep(0.2)
    root.update()
    assert fake.downloads == 0 and fake.started == [] and app.update_bar is not None


def test_a_button_press_still_asks_first(root: tk.Tk, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _Updates(monkeypatch, tmp_path)
    monkeypatch.setattr("fanout.ui.messagebox.askyesno", lambda *a, **k: fake.asked.append("yes?") or False)
    app = _app_for_updates(root, [], auto_update=False)
    app.show_update_banner(_release())
    app.update_now()
    assert fake.asked == ["yes?"] and fake.downloads == 0


def test_a_run_in_progress_is_never_interrupted(root: tk.Tk, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _Updates(monkeypatch, tmp_path)
    app = _app_for_updates(root, [])
    app.worker = object()  # type: ignore[assignment]  # "browser windows are open"
    try:
        app.on_update_event("update_checked", (_release(), False))
        time.sleep(0.2)
        root.update()
        assert fake.downloads == 0, "no automatic update while windows are open"
        # A run that started while the file was downloading: keep the file, do not close anything.
        setup = tmp_path / "Setup.exe"
        app.on_update_event("update_install", setup)
        assert fake.started == [] and app.ready_installer == setup
        assert "press Update now" in app.update_text.get()
    finally:
        app.worker = None


def test_a_failed_automatic_update_says_so_in_the_bar_and_tries_again_next_time(
    root: tk.Tk, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _Updates(monkeypatch, tmp_path, fail=True)
    app = _app_for_updates(root, [])
    app.on_update_event("update_checked", (_release(), False))
    assert _wait(root, lambda: app.update_bar is not None and "didn't work" in app.update_text.get())
    assert fake.errors == [], "no pop-up out of nowhere"
    assert "Press Update now" in app.update_text.get()
    assert app.cfg.auto_update_tried == "", "a download that failed is retried on the next start"
    assert app.update_automatic is False and app.working_on_update is False
