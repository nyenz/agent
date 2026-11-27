"""Sessions: tiling, banner, login import, window opening (fake Playwright), worker thread."""

from __future__ import annotations

import asyncio
import json
import queue
import shutil
from pathlib import Path
from typing import Any

import pytest

from fanout import sessions
from fanout.brave import BraveProfile
from fanout.sessions import SessionManager, SlotSpec
from fanout.worker import Worker


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("FANOUT_HOME", str(tmp_path / "FanOutHome"))
    return tmp_path / "FanOutHome"


# ----- pure helpers -------------------------------------------------------------------------


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 6])
def test_tiles_fill_screen_without_overlap(count: int) -> None:
    rects = [sessions.tile_geometry(i, count, 1920, 1080) for i in range(count)]
    for x, y, w, h in rects:
        assert x >= 0 and y >= 0 and x + w <= 1920 and y + h <= 1080 - 48 + 1
    for i, a in enumerate(rects):
        for b in rects[i + 1 :]:
            separate = a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0] or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1]
            assert separate, (a, b)


def test_three_windows_are_side_by_side_columns() -> None:
    assert [sessions.tile_geometry(i, 3, 1800, 1000)[:2] for i in range(3)] == [(0, 0), (600, 0), (1200, 0)]


def test_banner_has_numbered_text_and_colour() -> None:
    script = sessions.banner_script(2)
    assert json.dumps("ACCOUNT 2 - log into your chat site here") in script
    assert json.dumps(sessions.account_color(2)) in script
    assert sessions.account_color(1) != sessions.account_color(2)
    assert sessions.account_color(7) == sessions.account_color(1)


def test_origin_keeps_no_path_or_query() -> None:
    assert sessions.origin_of("https://chat.example.com/c/abc123?x=1") == "https://chat.example.com"
    assert sessions.origin_of("data:text/html,hi") is None
    assert sessions.origin_of("about:blank") is None


def test_start_page_is_a_data_url() -> None:
    assert sessions.start_page_url(1).startswith("data:text/html")


# ----- login import -------------------------------------------------------------------------


def make_brave_profile(tmp_path: Path) -> Path:
    root = tmp_path / "Brave"
    profile = root / "Default"
    (profile / "Network").mkdir(parents=True)
    (profile / "Network" / "Cookies").write_bytes(b"cookies")
    (profile / "Network" / "Cookies-journal").write_bytes(b"journal")
    (profile / "Preferences").write_text("{}")
    (root / "Local State").write_text("{}")
    for forbidden in ("Login Data", "Login Data-journal", "History", "Web Data", "Bookmarks"):
        (profile / forbidden).write_bytes(b"private")
    (profile / "Extensions").mkdir()
    (profile / "Extensions" / "ext.js").write_text("x")
    (profile / "Cache").mkdir()
    (profile / "Cache" / "blob").write_bytes(b"cache")
    return profile


def test_import_copies_only_the_allowed_files(tmp_path: Path) -> None:
    src = make_brave_profile(tmp_path)
    dest = tmp_path / "session"
    assert sessions.import_logins(src, dest) is True
    copied = sorted(str(p.relative_to(dest)).replace("\\", "/") for p in dest.rglob("*") if p.is_file())
    assert copied == ["Default/Network/Cookies", "Default/Network/Cookies-journal", "Default/Preferences", "Local State"]


def test_import_never_overwrites_an_existing_login(tmp_path: Path) -> None:
    src = make_brave_profile(tmp_path)
    dest = tmp_path / "session"
    (dest / "Default" / "Network").mkdir(parents=True)
    (dest / "Default" / "Network" / "Cookies").write_bytes(b"mine")
    assert sessions.import_logins(src, dest) is True
    assert (dest / "Default" / "Network" / "Cookies").read_bytes() == b"mine"
    assert not (dest / "Local State").exists()


def test_import_without_cookies_reports_failure(tmp_path: Path) -> None:
    src = make_brave_profile(tmp_path)
    (src / "Network" / "Cookies").unlink()
    assert sessions.import_logins(src, tmp_path / "session") is False


def test_import_failure_leaves_nothing_behind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = make_brave_profile(tmp_path)
    real_copy = shutil.copyfile
    calls: list[int] = []

    def flaky(a: Any, b: Any) -> Any:
        calls.append(1)
        if len(calls) == 3:
            raise PermissionError("locked")
        return real_copy(a, b)

    monkeypatch.setattr(sessions.shutil, "copyfile", flaky)
    dest = tmp_path / "session"
    assert sessions.import_logins(src, dest) is False
    assert [p for p in dest.rglob("*") if p.is_file()] == []


def test_no_saved_login_database_is_named_in_the_import_list() -> None:
    assert all("Login Data" not in name and "History" not in name for name in sessions.IMPORT_FILES)


# ----- planning -----------------------------------------------------------------------------


def profile(root: Path, folder: str) -> BraveProfile:
    return BraveProfile(folder, folder, root / folder, root, "Brave", None, False, False)


def test_app_managed_specs_use_private_folders(isolated_home: Path) -> None:
    specs = sessions.app_managed_specs(3, [Path("/a"), None])
    assert [s.number for s in specs] == [1, 2, 3]
    assert specs[0].user_data_dir == isolated_home / "sessions" / "account_1"
    assert [s.import_from for s in specs] == [Path("/a"), None, None]


def test_reuse_plan_opens_first_profile_per_root_in_place(tmp_path: Path) -> None:
    root_a, root_b = tmp_path / "A", tmp_path / "B"
    specs = sessions.plan_reuse_profiles(
        [profile(root_a, "Default"), profile(root_a, "Profile 1"), profile(root_b, "Default")], tmp_path / "tmp"
    )
    assert specs[0].user_data_dir == root_a and specs[0].profile_directory == "Default"
    assert specs[0].fallback is not None and specs[0].fallback.temporary
    assert specs[1].temporary and specs[1].user_data_dir == tmp_path / "tmp" / "clone_2"
    assert specs[2].user_data_dir == root_b


# ----- fake Playwright ----------------------------------------------------------------------


class FakeSession:
    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method == "Browser.getWindowForTarget":
            return {"windowId": 1, "bounds": {"left": 10, "top": 20, "width": 600, "height": 500}}
        return {}

    async def detach(self) -> None:
        return None


class FakePage:
    def __init__(self) -> None:
        self.url = "about:blank"
        self.ready = False
        self.visited: list[str] = []

    async def goto(self, url: str, **_: Any) -> None:
        self.visited.append(url)
        if not url.startswith("data:"):
            self.url = url

    async def evaluate(self, script: str) -> bool:
        return self.ready if "textarea" in script else False


class FakeContext:
    def __init__(self, user_dir: str, options: dict[str, Any]) -> None:
        self.user_dir, self.options = user_dir, options
        self.pages = [FakePage()]
        self.scripts: list[str] = []
        self.closed = False
        self.handlers: dict[str, Any] = {}

    def on(self, event: str, handler: Any) -> None:
        self.handlers[event] = handler

    async def add_init_script(self, script: str) -> None:
        self.scripts.append(script)

    async def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page

    async def new_cdp_session(self, page: Any) -> FakeSession:
        return FakeSession()

    async def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self, fail_dirs: set[str] | None = None) -> None:
        self.launched: list[FakeContext] = []
        self.fail_dirs = fail_dirs or set()

    async def launch_persistent_context(self, user_dir: str, **options: Any) -> FakeContext:
        if user_dir in self.fail_dirs:
            raise RuntimeError("Failed to launch: simulated")
        ctx = FakeContext(user_dir, options)
        self.launched.append(ctx)
        return ctx


class FakePlaywright:
    def __init__(self, fail_dirs: set[str] | None = None) -> None:
        self.chromium = FakeChromium(fail_dirs)
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


def collector() -> tuple[list[tuple[str, dict[str, Any]]], Any]:
    events: list[tuple[str, dict[str, Any]]] = []
    return events, lambda kind, **data: events.append((kind, data))


def manager_with(pw: FakePlaywright, emit: Any, poll: float = 0.01) -> SessionManager:
    async def factory() -> FakePlaywright:
        return pw

    return SessionManager(emit, playwright_factory=factory, poll_seconds=poll)


def kinds(events: list[tuple[str, dict[str, Any]]], kind: str) -> list[dict[str, Any]]:
    return [data for k, data in events if k == kind]


# ----- window opening -----------------------------------------------------------------------


def test_three_windows_open_tiled_with_banners() -> None:
    pw, (events, emit) = FakePlaywright(), collector()

    async def scenario() -> None:
        mgr = manager_with(pw, emit)
        await mgr.open_all(sessions.app_managed_specs(3), Path("/opt/brave"), (1800, 1000))
        await mgr.close_all()

    asyncio.run(scenario())
    contexts = pw.chromium.launched
    assert len(contexts) == 3
    assert [kind for kind, _ in events].count("slot_open") == 3
    for i, ctx in enumerate(contexts):
        x, y, w, h = sessions.tile_geometry(i, 3, 1800, 1000)
        assert f"--window-position={x},{y}" in ctx.options["args"]
        assert f"--window-size={w},{h}" in ctx.options["args"]
        assert ctx.options["headless"] is False and Path(ctx.options["executable_path"]) == Path("/opt/brave")
        assert f"ACCOUNT {i + 1} - log into your chat site here" in ctx.scripts[0]
        assert Path(ctx.user_dir).name == f"account_{i + 1}"
        assert ctx.closed
    flags = " ".join(a for c in contexts for a in c.options["args"])
    assert "AutomationControlled" not in flags and "webdriver" not in flags.lower()


def test_bundled_chromium_passes_no_executable() -> None:
    pw, (_events, emit) = FakePlaywright(), collector()

    async def scenario() -> None:
        mgr = manager_with(pw, emit)
        await mgr.open_all(sessions.app_managed_specs(1), None, (1000, 800))
        await mgr.close_all()

    asyncio.run(scenario())
    assert "executable_path" not in pw.chromium.launched[0].options


def test_one_failing_window_does_not_stop_the_others() -> None:
    specs = sessions.app_managed_specs(3)
    pw, (events, emit) = FakePlaywright({str(specs[1].user_data_dir)}), collector()

    async def scenario() -> None:
        mgr = manager_with(pw, emit)
        await mgr.open_all(specs, None, (1800, 1000))
        await mgr.close_all()

    asyncio.run(scenario())
    assert sorted(d["slot"] for d in kinds(events, "slot_open")) == [1, 3]
    errors = kinds(events, "slot_error")
    assert [e["slot"] for e in errors] == [2]
    assert "Traceback" not in errors[0]["message"] and "What" not in errors[0]["message"]
    assert errors[0]["fix"]


def test_ready_is_reported_once_per_change() -> None:
    pw, (events, emit) = FakePlaywright(), collector()

    async def scenario() -> None:
        mgr = manager_with(pw, emit)
        await mgr.open_all(sessions.app_managed_specs(2), None, (1800, 1000))
        await asyncio.sleep(0.05)
        page = pw.chromium.launched[0].pages[0]
        page.url, page.ready = "https://chat.example.com/", True
        await asyncio.sleep(0.1)
        await mgr.close_all()

    asyncio.run(scenario())
    states = [d for d in kinds(events, "slot_state") if d["slot"] == 1]
    assert [s["ready"] for s in states] == [False, True]
    assert states[-1]["host"] == "chat.example.com"
    assert all(d["ready"] is False for d in kinds(events, "slot_state") if d["slot"] == 2)


def test_import_failure_is_a_gentle_note_not_an_error(tmp_path: Path) -> None:
    specs = [SlotSpec(1, tmp_path / "s1", import_from=tmp_path / "nowhere" / "Default")]
    pw, (events, emit) = FakePlaywright(), collector()

    async def scenario() -> None:
        mgr = manager_with(pw, emit)
        await mgr.open_all(specs, None, (1000, 800))
        await mgr.close_all()

    asyncio.run(scenario())
    notes = kinds(events, "slot_note")
    assert notes and "just log in once, it'll be remembered" in notes[0]["message"]
    assert kinds(events, "slot_open") and not kinds(events, "slot_error")


def test_reuse_mode_falls_back_to_a_private_copy(tmp_path: Path) -> None:
    src = make_brave_profile(tmp_path)
    real_root = tmp_path / "Brave"
    clone = SlotSpec(1, tmp_path / "tmp" / "clone_1", import_from=src, temporary=True)
    spec = SlotSpec(1, real_root, profile_directory="Default", fallback=clone)
    pw, (events, emit) = FakePlaywright({str(real_root)}), collector()

    async def scenario() -> None:
        mgr = manager_with(pw, emit)
        await mgr.open_all([spec], None, (1000, 800))
        assert (clone.user_data_dir / "Default" / "Network" / "Cookies").exists()
        await mgr.close_all()

    asyncio.run(scenario())
    assert kinds(events, "slot_open") and not kinds(events, "slot_error")
    assert any("private copy" in d["message"] for d in kinds(events, "slot_note"))
    assert not clone.user_data_dir.exists()  # temporary copy removed on close


def test_reuse_passes_profile_directory_flag(tmp_path: Path) -> None:
    spec = SlotSpec(1, tmp_path / "Brave", profile_directory="Profile 2")
    pw, (_events, emit) = FakePlaywright(), collector()

    async def scenario() -> None:
        mgr = manager_with(pw, emit)
        await mgr.open_all([spec], None, (1000, 800))
        await mgr.close_all()

    asyncio.run(scenario())
    assert "--profile-directory=Profile 2" in pw.chromium.launched[0].options["args"]


def test_snapshot_reports_bounds_and_origin_only() -> None:
    pw, (_events, emit) = FakePlaywright(), collector()

    async def scenario() -> dict[str, Any]:
        mgr = manager_with(pw, emit)
        await mgr.open_all(sessions.app_managed_specs(1), None, (1000, 800))
        pw.chromium.launched[0].pages[0].url = "https://chat.example.com/c/secret-conversation-id?q=1"
        snap = await mgr.snapshot()
        await mgr.close_all()
        return snap

    snap = asyncio.run(scenario())
    assert snap["window_geometry"] == {"1": [10, 20, 600, 500]}
    assert snap["last_urls"] == {"1": "https://chat.example.com"}


def test_saved_geometry_and_url_are_restored() -> None:
    pw, (_events, emit) = FakePlaywright(), collector()

    async def scenario() -> None:
        mgr = manager_with(pw, emit)
        await mgr.open_all(
            sessions.app_managed_specs(1), None, (1000, 800),
            geometry={"1": [5, 6, 700, 400]}, last_urls={"1": "https://chat.example.com"},
        )
        await mgr.close_all()

    asyncio.run(scenario())
    ctx = pw.chromium.launched[0]
    assert "--window-position=5,6" in ctx.options["args"] and "--window-size=700,400" in ctx.options["args"]
    assert ctx.pages[0].visited == ["https://chat.example.com"]


def test_user_closing_a_window_is_reported() -> None:
    pw, (events, emit) = FakePlaywright(), collector()

    async def scenario() -> None:
        mgr = manager_with(pw, emit)
        await mgr.open_all(sessions.app_managed_specs(1), None, (1000, 800))
        pw.chromium.launched[0].handlers["close"]()
        await mgr.close_all()

    asyncio.run(scenario())
    assert kinds(events, "slot_closed") == [{"slot": 1}]


# ----- real Playwright, no browser needed ----------------------------------------------------


def test_real_playwright_with_a_missing_browser_gives_a_friendly_error(tmp_path: Path) -> None:
    pytest.importorskip("playwright")
    events, emit = collector()

    async def scenario() -> None:
        mgr = SessionManager(emit)
        try:
            await mgr.open_all(sessions.app_managed_specs(2), tmp_path / "no-such-browser", (1000, 800))
        finally:
            await mgr.shutdown()

    asyncio.run(scenario())
    errors = kinds(events, "slot_error")
    assert sorted(e["slot"] for e in errors) == [1, 2]
    for e in errors:
        assert "couldn't start the browser" in e["message"]
        assert "Traceback" not in e["message"] + e["fix"] and "no-such-browser" not in e["message"]
    assert not kinds(events, "slot_open")


# ----- worker thread ------------------------------------------------------------------------


def drain(events: queue.Queue[tuple[str, Any]], wanted: str, timeout: float = 3.0) -> dict[str, Any]:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            kind, data = events.get(timeout=0.1)
        except queue.Empty:
            continue
        if kind == wanted:
            return data
    raise AssertionError(f"no {wanted!r} event")


def test_worker_runs_commands_and_shuts_down_cleanly() -> None:
    pw, events = FakePlaywright(), queue.Queue()

    def factory(emit: Any) -> SessionManager:
        return manager_with(pw, emit)

    worker = Worker(events, manager_factory=factory)
    worker.start()
    worker.submit("open_windows", specs=sessions.app_managed_specs(2), exe=None, screen=(1200, 800))
    assert drain(events, "slot_open")["slot"] in (1, 2)
    drain(events, "slot_open")
    worker.submit("snapshot")
    assert "window_geometry" in drain(events, "snapshot")
    assert worker.shutdown(timeout=5) is True
    assert all(c.closed for c in pw.chromium.launched) and pw.stopped
    assert worker.final_snapshot["window_geometry"]


def test_worker_survives_a_failing_command() -> None:
    events: queue.Queue[tuple[str, Any]] = queue.Queue()

    class Broken(SessionManager):
        async def open_all(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("secret internal detail")

    worker = Worker(events, manager_factory=lambda emit: Broken(emit))
    worker.start()
    worker.submit("open_windows", specs=[], exe=None, screen=(100, 100))
    err = drain(events, "worker_error")
    assert "secret internal detail" not in err["message"] + err["fix"]
    assert err["command"] == "open_windows", "the screen needs to know what failed"
    worker.submit("snapshot")
    drain(events, "snapshot")  # the loop is still alive
    assert worker.shutdown(timeout=5)


def test_worker_never_touches_tk() -> None:
    import inspect

    source = inspect.getsource(sys_module("fanout.worker")) + inspect.getsource(sys_module("fanout.sessions"))
    assert "tkinter" not in source


def sys_module(name: str) -> Any:
    import importlib

    return importlib.import_module(name)


def test_a_helper_that_cannot_start_fails_at_once_with_plain_words() -> None:
    import time as _time

    from fanout.errors import describe
    from fanout.worker import WorkerNotRunningError

    events: queue.Queue[tuple[str, Any]] = queue.Queue()

    def broken_factory(_emit: Any) -> SessionManager:
        raise RuntimeError("boom inside the helper")

    worker = Worker(events, manager_factory=broken_factory)
    worker.start()
    started = _time.monotonic()
    with pytest.raises(WorkerNotRunningError) as caught:
        worker.submit("snapshot")
    assert _time.monotonic() - started < 3, "the window must not freeze waiting for a dead helper"
    shown = describe(caught.value).text()
    assert "browser helper stopped" in shown and "boom" not in shown
    assert worker.shutdown(timeout=1)


def test_closing_the_persons_brave_on_windows_targets_only_their_processes() -> None:
    args = sessions.taskkill_args([101, 202])
    assert args == ["taskkill", "/PID", "101", "/PID", "202"]
    assert "/F" not in args and "/IM" not in args, "never force, never close every Brave by name"
