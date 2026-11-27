"""Compatibility mode: the browser starts as a normal program and FanOut attaches afterwards.

Uses a fake browser process and a fake browser connection, so no real browser is needed, plus one
test against a real Chrome/Chromium that skips itself when none is installed.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from fanout import sessions
from fanout.sessions import SessionManager, SlotSpec

FORBIDDEN_FLAGS = ("--enable-automation", "AutomationControlled", "--headless", "webdriver", "--disable-blink-features")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("FANOUT_HOME", str(tmp_path / "FanOutHome"))
    return tmp_path / "FanOutHome"


# ----- fakes ---------------------------------------------------------------------------------


class FakeProc:
    """A browser process we started. `calls` records what was done to it, in order."""

    def __init__(self, calls: list[str], *, quits_on_close: bool = True, obeys_terminate: bool = True) -> None:
        self.pid = 4242
        self.returncode: int | None = None
        self.calls = calls
        self.quits_on_close = quits_on_close
        self.obeys_terminate = obeys_terminate
        self._done = asyncio.Event()

    def exit(self, code: int = 0) -> None:
        self.returncode = code
        self._done.set()

    async def wait(self) -> int | None:
        await self._done.wait()
        return self.returncode

    def terminate(self) -> None:
        self.calls.append("terminate")
        if self.obeys_terminate:
            self.exit(-15)

    def kill(self) -> None:
        self.calls.append("kill")
        self.exit(-9)


class FakeBrowserSession:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.browser.calls.append(method)
        if method == "Browser.close" and self.browser.proc.quits_on_close:
            self.browser.proc.exit(0)
        return {}


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
        self.evaluated: list[str] = []

    async def goto(self, url: str, **_: Any) -> None:
        self.visited.append(url)
        if not url.startswith("data:"):
            self.url = url

    async def evaluate(self, script: str) -> bool:
        self.evaluated.append(script)
        return self.ready if "textarea" in script else False


class FakeContext:
    def __init__(self, with_page: bool = True) -> None:
        self.pages = [FakePage()] if with_page else []
        self.scripts: list[str] = []
        self.closed = False

    def on(self, event: str, handler: Any) -> None:
        raise AssertionError("an attached window must listen on the browser, not the context")

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


class FakeBrowser:
    def __init__(self, proc: FakeProc, calls: list[str], context: FakeContext) -> None:
        self.proc, self.calls = proc, calls
        self.contexts = [context]
        self.handlers: dict[str, Any] = {}
        self.connected = True

    def on(self, event: str, handler: Any) -> None:
        self.handlers[event] = handler

    def is_connected(self) -> bool:
        return self.connected

    async def new_browser_cdp_session(self) -> FakeBrowserSession:
        return FakeBrowserSession(self)

    def disconnect(self) -> None:
        self.connected = False
        self.handlers["disconnected"]()


class FakeLaunchedContext(FakeContext):
    """What the classic launch returns."""

    def __init__(self, user_dir: str, options: dict[str, Any]) -> None:
        super().__init__()
        self.user_dir, self.options = user_dir, options

    def on(self, event: str, handler: Any) -> None:
        return None


class FakeChromium:
    executable_path = "/opt/bundled/chromium"

    def __init__(self, world: World) -> None:
        self.world = world
        self.classic: list[FakeLaunchedContext] = []
        self.cdp_urls: list[str] = []

    async def launch_persistent_context(self, user_dir: str, **options: Any) -> FakeLaunchedContext:
        ctx = FakeLaunchedContext(user_dir, options)
        self.classic.append(ctx)
        return ctx

    async def connect_over_cdp(self, url: str) -> FakeBrowser:
        self.cdp_urls.append(url)
        if self.world.connect_error is not None:
            raise self.world.connect_error
        port = int(url.rsplit(":", 1)[1])
        browser = FakeBrowser(self.world.by_port[port], self.world.calls, FakeContext(with_page=self.world.page_at_start))
        self.world.browsers.append(browser)
        self.world.browser_by_port[port] = browser
        return browser


class FakePlaywright:
    def __init__(self, world: World) -> None:
        self.chromium = FakeChromium(world)

    async def stop(self) -> None:
        return None


class World:
    """Everything the fakes share, so a test can see the order things happened in."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.spawned: list[list[str]] = []
        self.procs: list[FakeProc] = []
        self.by_port: dict[int, FakeProc] = {}
        self.browsers: list[FakeBrowser] = []
        self.browser_by_port: dict[int, FakeBrowser] = {}
        self.connect_error: Exception | None = None
        self.page_at_start = True
        self.proc_options: dict[str, Any] = {}
        self.exit_at_once = False
        self.at_spawn: Any = None

    async def spawn(self, *command: str, **_: Any) -> FakeProc:
        self.spawned.append(list(command))
        if self.at_spawn:
            self.at_spawn(list(command))
        proc = FakeProc(self.calls, **self.proc_options)
        if self.exit_at_once:
            proc.exit(1)
        self.procs.append(proc)
        port = next(int(a.split("=")[1]) for a in command if a.startswith("--remote-debugging-port="))
        self.by_port[port] = proc
        return proc


def collector() -> tuple[list[tuple[str, dict[str, Any]]], Any]:
    events: list[tuple[str, dict[str, Any]]] = []
    return events, lambda kind, **data: events.append((kind, data))


def kinds(events: list[tuple[str, dict[str, Any]]], kind: str) -> list[dict[str, Any]]:
    return [data for k, data in events if k == kind]


def make(world: World, emit: Any, *, answers: bool = True, wait: float = 0.5, grace: float = 0.05) -> tuple[SessionManager, FakePlaywright]:
    pw = FakePlaywright(world)

    async def factory() -> FakePlaywright:
        return pw

    mgr = SessionManager(
        emit,
        playwright_factory=factory,
        poll_seconds=0.01,
        spawn=world.spawn,
        probe=lambda port: answers,
        attach_wait_seconds=wait,
        close_grace_seconds=grace,
    )
    return mgr, pw


def run(scenario: Any) -> None:
    asyncio.run(scenario())


# ----- ports ---------------------------------------------------------------------------------


def test_free_port_is_a_real_loopback_port_and_ports_differ() -> None:
    import socket

    taken: set[int] = set()
    for _ in range(8):
        port = sessions.free_port(taken)
        assert 0 < port < 65536 and port not in taken
        taken.add(port)
        with socket.socket() as sock:  # it really was free
            sock.bind(("127.0.0.1", port))


def test_free_port_tries_again_when_two_windows_get_the_same_number(monkeypatch: pytest.MonkeyPatch) -> None:
    offered = iter([5000, 5000, 5001])
    monkeypatch.setattr(sessions, "_bind_free_port", lambda: next(offered))
    assert sessions.free_port({5000}) == 5001


def test_free_port_gives_up_with_a_plain_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from fanout.errors import AttachError

    monkeypatch.setattr(sessions, "_bind_free_port", lambda: 5000)
    with pytest.raises(AttachError):
        sessions.free_port({5000})


def test_the_port_is_only_ever_bound_to_this_computer() -> None:
    import inspect

    source = inspect.getsource(sessions)
    assert '("127.0.0.1", 0)' in source and "0.0.0.0" not in source.replace("never 0.0.0.0", "")
    assert "--remote-debugging-address" not in source


# ----- what is started -----------------------------------------------------------------------


def test_a_normal_browser_is_started_and_attached() -> None:
    world, (events, emit) = World(), collector()

    async def scenario() -> None:
        mgr, pw = make(world, emit)
        await mgr.open_all(sessions.app_managed_specs(2), Path("/opt/brave"), (1800, 1000), attach=True)
        assert len(world.spawned) == 2 and not pw.chromium.classic
        for i, command in enumerate(world.spawned):
            x, y, w, h = sessions.tile_geometry(i, 2, 1800, 1000)
            assert Path(command[0]) == Path("/opt/brave")
            port = int(next(a for a in command if a.startswith("--remote-debugging-port=")).split("=")[1])
            assert port > 0 and f"http://127.0.0.1:{port}" in pw.chromium.cdp_urls
            assert Path(next(a for a in command if a.startswith("--user-data-dir=")).split("=", 1)[1]).name == f"account_{i + 1}"
            assert f"--window-position={x},{y}" in command and f"--window-size={w},{h}" in command
            assert "--no-first-run" in command and "--no-default-browser-check" in command
        ports = {a for c in world.spawned for a in c if a.startswith("--remote-debugging-port=")}
        assert len(ports) == 2, "every window gets its own port"
        await mgr.close_all()

    run(scenario)
    assert [d["slot"] for d in kinds(events, "slot_open")] == [1, 2]
    assert not kinds(events, "slot_error") and not kinds(events, "slot_note")


def test_no_automation_or_hiding_flags_are_passed() -> None:
    world, (_events, emit) = World(), collector()

    async def scenario() -> None:
        mgr, _ = make(world, emit)
        await mgr.open_all(sessions.app_managed_specs(1), None, (1000, 800), attach=True)
        await mgr.close_all()

    run(scenario)
    joined = " ".join(world.spawned[0])
    for flag in FORBIDDEN_FLAGS:
        assert flag.lower() not in joined.lower(), flag


def test_bundled_chromium_is_found_through_playwright() -> None:
    world, (_events, emit) = World(), collector()

    async def scenario() -> None:
        mgr, _ = make(world, emit)
        await mgr.open_all(sessions.app_managed_specs(1), None, (1000, 800), attach=True)
        await mgr.close_all()

    run(scenario)
    assert Path(world.spawned[0][0]) == Path("/opt/bundled/chromium")


def test_logins_are_imported_before_the_browser_starts(tmp_path: Path) -> None:
    from test_sessions import make_brave_profile

    src = make_brave_profile(tmp_path)
    spec = SlotSpec(1, sessions.sessions_dir() / "account_1", import_from=src)
    world, (_events, emit) = World(), collector()
    seen: list[bool] = []
    world.at_spawn = lambda command: seen.append((spec.user_data_dir / "Default" / "Network" / "Cookies").exists())

    async def scenario() -> None:
        mgr, _ = make(world, emit)
        await mgr.open_all([spec], None, (1000, 800), attach=True)
        await mgr.close_all()

    run(scenario)
    assert seen == [True], "the cookie file must be in place before the browser can lock it"


def test_the_attached_window_keeps_all_the_usual_behaviour() -> None:
    world, (events, emit) = World(), collector()

    async def scenario() -> None:
        mgr, _ = make(world, emit)
        await mgr.open_all(
            sessions.app_managed_specs(1), None, (1000, 800), geometry={"1": [5, 6, 700, 400]}, last_urls={"1": "https://chat.example.com"},
            attach=True,
        )
        browser = world.browsers[0]
        page = browser.contexts[0].pages[0]
        assert "ACCOUNT 1 - log into your chat site here" in browser.contexts[0].scripts[0]
        assert any("ACCOUNT 1" in s for s in page.evaluated), "windows that are already open get the banner too"
        assert page.visited == ["https://chat.example.com"]
        assert "--window-position=5,6" in world.spawned[0]
        await asyncio.sleep(0.05)
        page.url, page.ready = "https://chat.example.com/", True
        await asyncio.sleep(0.1)
        snap = await mgr.snapshot()
        assert snap["window_geometry"] == {"1": [10, 20, 600, 500]} and snap["last_urls"] == {"1": "https://chat.example.com"}
        await mgr.close_all()

    run(scenario)
    states = [d["ready"] for d in kinds(events, "slot_state")]
    assert states[:1] == [False] and states[-1] is True


def test_a_browser_without_a_page_gets_one() -> None:
    world, (events, emit) = World(), collector()
    world.page_at_start = False

    async def scenario() -> None:
        mgr, _ = make(world, emit)
        await mgr.open_all(sessions.app_managed_specs(1), None, (1000, 800), attach=True)
        assert len(world.browsers[0].contexts[0].pages) == 1
        await mgr.close_all()

    run(scenario)
    assert kinds(events, "slot_open") and not kinds(events, "slot_error")


def test_it_never_types_or_navigates_while_the_person_logs_in() -> None:
    """Until a chat box is READY the only thing FanOut does to a window is read it."""
    world, (_events, emit) = World(), collector()

    async def scenario() -> None:
        mgr, _ = make(world, emit)
        await mgr.open_all(sessions.app_managed_specs(1), None, (1000, 800), attach=True)
        page = world.browsers[0].contexts[0].pages[0]
        visited_after_open = list(page.visited)
        await asyncio.sleep(0.15)  # the watcher polls many times
        assert page.visited == visited_after_open
        assert all("textarea" in s or "ACCOUNT" in s for s in page.evaluated)
        await mgr.close_all()

    run(scenario)


# ----- when it cannot start ------------------------------------------------------------------


def test_a_browser_that_never_answers_falls_back_to_the_classic_launch() -> None:
    world, (events, emit) = World(), collector()

    async def scenario() -> None:
        mgr, pw = make(world, emit, answers=False, wait=0.3)
        started = time.monotonic()
        await mgr.open_all(sessions.app_managed_specs(1), None, (1000, 800), attach=True)
        assert time.monotonic() - started >= 0.3
        assert len(pw.chromium.classic) == 1, "classic launch took over"
        assert world.procs[0].returncode is not None, "the browser that never answered was stopped"
        await mgr.close_all()

    run(scenario)
    assert kinds(events, "slot_open") and not kinds(events, "slot_error")
    note = kinds(events, "slot_note")[0]["message"]
    assert "compatibility mode" in note and "standard mode" in note and "Traceback" not in note and "port" not in note.lower()


def test_a_browser_that_exits_at_once_falls_back_without_waiting_the_full_time() -> None:
    world, (events, emit) = World(), collector()
    world.exit_at_once = True

    async def scenario() -> None:
        mgr, pw = make(world, emit, answers=False, wait=30)
        started = time.monotonic()
        await mgr.open_all(sessions.app_managed_specs(1), None, (1000, 800), attach=True)
        assert time.monotonic() - started < 5
        assert len(pw.chromium.classic) == 1
        await mgr.close_all()

    run(scenario)
    assert kinds(events, "slot_open") and kinds(events, "slot_note")


def test_a_failed_connection_falls_back_and_stops_the_browser_it_started() -> None:
    world, (events, emit) = World(), collector()
    world.connect_error = RuntimeError("connect refused: secret internal detail")

    async def scenario() -> None:
        mgr, pw = make(world, emit)
        await mgr.open_all(sessions.app_managed_specs(1), None, (1000, 800), attach=True)
        assert len(pw.chromium.classic) == 1
        assert world.procs[0].returncode is not None
        assert not mgr._procs and not mgr._browsers and not mgr._ports and not mgr._reserved
        await mgr.close_all()

    run(scenario)
    assert "secret internal detail" not in str(kinds(events, "slot_note"))
    assert kinds(events, "slot_open") and not kinds(events, "slot_error")


def test_if_the_classic_launch_also_fails_the_person_gets_a_plain_error() -> None:
    world, (events, emit) = World(), collector()
    world.exit_at_once = True

    async def scenario() -> None:
        mgr, pw = make(world, emit, answers=False)

        async def broken(user_dir: str, **options: Any) -> Any:
            raise RuntimeError("Failed to launch: simulated")

        pw.chromium.launch_persistent_context = broken  # type: ignore[method-assign]
        await mgr.open_all(sessions.app_managed_specs(1), None, (1000, 800), attach=True)
        await mgr.close_all()

    run(scenario)
    errors = kinds(events, "slot_error")
    assert len(errors) == 1 and "couldn't start the browser" in errors[0]["message"] and errors[0]["fix"]


def test_compatibility_mode_off_uses_the_classic_launch_only() -> None:
    world, (events, emit) = World(), collector()

    async def scenario() -> None:
        mgr, pw = make(world, emit)
        await mgr.open_all(sessions.app_managed_specs(2), None, (1000, 800), attach=False)
        assert not world.spawned and len(pw.chromium.classic) == 2
        await mgr.close_all()

    run(scenario)
    assert not kinds(events, "slot_note")


def test_a_real_brave_profile_keeps_the_classic_launch_and_says_why(tmp_path: Path) -> None:
    real = tmp_path / "Brave"
    spec = SlotSpec(1, real, profile_directory="Default")
    world, (events, emit) = World(), collector()

    async def scenario() -> None:
        mgr, pw = make(world, emit)
        await mgr.open_all([spec], None, (1000, 800), attach=True)
        assert not world.spawned and len(pw.chromium.classic) == 1
        assert "--profile-directory=Default" in pw.chromium.classic[0].options["args"]
        await mgr.close_all()

    run(scenario)
    note = kinds(events, "slot_note")[0]["message"]
    assert "real Brave profile" in note and "standard mode" in note


# ----- closing -------------------------------------------------------------------------------


def test_close_asks_the_browser_to_quit_before_ending_the_process() -> None:
    world, (events, emit) = World(), collector()

    async def scenario() -> None:
        mgr, _ = make(world, emit)
        await mgr.open_all(sessions.app_managed_specs(1), None, (1000, 800), attach=True)
        await mgr.close_all()

    run(scenario)
    assert world.calls == ["Browser.close"], "a browser that quits by itself is left alone: no terminate, no kill"
    assert kinds(events, "windows_closed")


def test_a_browser_that_ignores_the_request_is_terminated_after_it() -> None:
    world, (_events, emit) = World(), collector()
    world.proc_options = {"quits_on_close": False}

    async def scenario() -> None:
        mgr, _ = make(world, emit)
        await mgr.open_all(sessions.app_managed_specs(1), None, (1000, 800), attach=True)
        await mgr.close_all()

    run(scenario)
    assert world.calls == ["Browser.close", "terminate"]


def test_a_browser_that_ignores_terminate_is_killed_last() -> None:
    world, (_events, emit) = World(), collector()
    world.proc_options = {"quits_on_close": False, "obeys_terminate": False}

    async def scenario() -> None:
        mgr, _ = make(world, emit)
        await mgr.open_all(sessions.app_managed_specs(1), None, (1000, 800), attach=True)
        await asyncio.wait_for(mgr.close_all(), timeout=15)

    run(scenario)
    assert world.calls == ["Browser.close", "terminate", "kill"]


def test_nothing_is_left_registered_after_closing() -> None:
    world, (_events, emit) = World(), collector()

    async def scenario() -> None:
        mgr, _ = make(world, emit)
        await mgr.open_all(sessions.app_managed_specs(2), None, (1000, 800), attach=True)
        await mgr.close_all()
        assert not (mgr._procs or mgr._browsers or mgr._ports or mgr._contexts or mgr._watchers or mgr._specs)

    run(scenario)
    assert all(p.returncode is not None for p in world.procs)


def test_the_person_closing_a_window_is_reported_and_stops_its_watcher() -> None:
    world, (events, emit) = World(), collector()

    async def scenario() -> None:
        mgr, _ = make(world, emit)
        await mgr.open_all(sessions.app_managed_specs(2), None, (1000, 800), attach=True)
        first = mgr._ports[1]
        world.by_port[first].exit(0)  # the browser quits when its window is closed
        world.browser_by_port[first].disconnect()
        await asyncio.sleep(0.05)
        assert 1 not in mgr._watchers and 2 in mgr._watchers and 1 not in mgr._contexts
        await mgr.close_all()  # still closes the other window and does not trip over the closed one

    run(scenario)
    assert {"slot": 1} in kinds(events, "slot_closed")
    assert all(p.returncode is not None for p in world.procs)


def test_a_temporary_folder_is_deleted_only_after_the_browser_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = SlotSpec(1, sessions.sessions_dir() / "account_9", temporary=True)
    world, (_events, emit) = World(), collector()
    real_rmtree = sessions.shutil.rmtree

    def recording_rmtree(path: Any, *args: Any, **kwargs: Any) -> None:
        world.calls.append(f"rmtree after exit={world.procs[0].returncode is not None}")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(sessions.shutil, "rmtree", recording_rmtree)

    async def scenario() -> None:
        mgr, _ = make(world, emit)
        await mgr.open_all([spec], None, (1000, 800), attach=True)
        assert spec.user_data_dir.exists()
        await mgr.close_all()

    run(scenario)
    assert world.calls[-1] == "rmtree after exit=True"
    assert not spec.user_data_dir.exists()


def test_reopening_closes_the_old_attached_windows_first() -> None:
    world, (_events, emit) = World(), collector()

    async def scenario() -> None:
        mgr, _ = make(world, emit)
        await mgr.open_all(sessions.app_managed_specs(1), None, (1000, 800), attach=True)
        await mgr.open_all(sessions.app_managed_specs(1), None, (1000, 800), attach=True)
        assert world.procs[0].returncode is not None and world.procs[1].returncode is None
        await mgr.close_all()

    run(scenario)


def test_the_worker_passes_the_setting_through_to_the_windows() -> None:
    import queue

    from fanout.worker import Worker

    world = World()
    events: queue.Queue[tuple[str, Any]] = queue.Queue()
    holder: dict[str, SessionManager] = {}

    def factory(emit: Any) -> SessionManager:
        holder["m"], _ = make(world, emit)
        return holder["m"]

    worker = Worker(events, manager_factory=factory)
    worker.start()
    worker.submit("open_windows", specs=sessions.app_managed_specs(1), exe=None, screen=(1000, 800), attach=True)
    deadline = time.time() + 5
    seen: list[str] = []
    while time.time() < deadline and "slot_open" not in seen:
        try:
            seen.append(events.get(timeout=0.1)[0])
        except queue.Empty:
            continue
    assert "slot_open" in seen and len(world.spawned) == 1
    assert worker.shutdown(timeout=10)
    assert all(p.returncode is not None for p in world.procs)


# ----- orphan clean-up -----------------------------------------------------------------------


def _sleeper(*extra: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", *extra])


def test_reap_orphans_matches_our_exact_port_and_leaves_others_alone() -> None:
    pytest.importorskip("psutil")
    port = sessions.free_port()
    ours = _sleeper(f"--remote-debugging-port={port}", "--user-data-dir=other-folder")
    decoy_port = _sleeper(f"--remote-debugging-port={port}0")  # a different port that merely starts the same way
    decoy_plain = _sleeper("--some-other-flag")
    try:
        time.sleep(0.5)
        assert sessions.reap_orphans([], [port]) == 1
        ours.wait(timeout=10)
        assert decoy_port.poll() is None and decoy_plain.poll() is None
    finally:
        for proc in (ours, decoy_port, decoy_plain):
            proc.kill()
            proc.wait(timeout=10)


def test_reap_orphans_still_matches_our_folders(tmp_path: Path) -> None:
    pytest.importorskip("psutil")
    folder = tmp_path / "account_1"
    mine = _sleeper(f"--user-data-dir={folder}")
    try:
        time.sleep(0.5)
        assert sessions.reap_orphans([folder]) == 1
        mine.wait(timeout=10)
    finally:
        mine.kill()
        mine.wait(timeout=10)


def test_reap_orphans_with_nothing_to_look_for_stops_nothing() -> None:
    pytest.importorskip("psutil")
    assert sessions.reap_orphans([], []) == 0


# ----- the probe -----------------------------------------------------------------------------


def test_the_readiness_probe_talks_to_a_local_server_and_ignores_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200 if self.path == "/json/version" else 404)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_: Any) -> None:
            return None

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")  # a proxy that would break the check if it were used
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    try:
        assert sessions.debug_port_answers(server.server_address[1]) is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert sessions.debug_port_answers(server.server_address[1]) is False


# ----- a real browser ------------------------------------------------------------------------


def test_a_real_browser_opens_normally_attaches_and_closes_cleanly(tmp_path: Path) -> None:
    pytest.importorskip("playwright")
    pytest.importorskip("psutil")
    from browserkit import find_browser

    exe = find_browser()
    if exe is None:
        pytest.skip("no Chrome/Chromium on this computer")
    extra = ["--headless=new", "--disable-gpu"]  # tests only: no screen needed in CI
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        extra.append("--no-sandbox")  # tests only: some CI containers run as root
    events, emit = collector()

    async def scenario() -> tuple[int, str]:
        mgr = SessionManager(emit, poll_seconds=0.2, extra_browser_args=extra)
        try:
            await mgr.open_all(sessions.app_managed_specs(1), Path(exe), (1000, 800), attach=True)
            assert kinds(events, "slot_open") and not kinds(events, "slot_error"), events
            assert not kinds(events, "slot_note"), "the real browser should attach without falling back"
            context = mgr._contexts[1]
            page = next(p for p in context.pages)
            await page.goto("data:text/html,<title>hello</title><textarea style='width:300px;height:60px'></textarea>")
            await asyncio.sleep(1.5)
            banner = await page.evaluate("(document.getElementById('__fanout_banner__') || {}).textContent")
            port = mgr._ports[1]
            return port, str(banner)
        finally:
            await mgr.shutdown()

    port, banner = asyncio.run(scenario())
    assert banner == "ACCOUNT 1 - log into your chat site here"
    assert any(e["ready"] for e in kinds(events, "slot_state")), "READY should appear once a chat box is on the page"
    import psutil

    time.sleep(0.5)
    left = [p for p in psutil.process_iter(["cmdline"]) if f"--remote-debugging-port={port}" in (p.info.get("cmdline") or [])]
    assert left == [], "closing FanOut must not leave the browser running"
