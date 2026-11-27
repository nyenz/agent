"""Browser windows: one persistent profile per account, tiled, bannered, with login detection.

Everything that touches Playwright lives here and runs on the worker thread's asyncio loop
(see worker.py). Logging in is always done by the person, by hand, in the window.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from . import engine, mapping as mapping_tools
from .brave import BraveProfile, running_brave_processes
from .config import app_data_dir
from .errors import describe
from .logging_setup import log_dir

log = logging.getLogger(__name__)

ACCOUNT_COLORS = ("#d9480f", "#1971c2", "#2f9e44", "#9c36b5", "#c2255c", "#0c8599")
BANNER_HEIGHT = 28

# The ONLY files an optional login import may copy. Saved-login databases, history,
# extensions and caches are never touched.
IMPORT_FILES = ("Network/Cookies", "Network/Cookies-journal", "Preferences")

READY_JS = """() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width >= 120 && r.height >= 20 && r.bottom > 0 && r.right > 0 &&
           s.visibility !== 'hidden' && s.display !== 'none';
  };
  const found = document.querySelectorAll(
    'textarea, [contenteditable="true"], [contenteditable=""], [role="textbox"]');
  return Array.from(found).some((el) => !el.disabled && !el.readOnly && visible(el));
}"""


def account_color(number: int) -> str:
    """Colour for account number (1-based); wraps around after six."""
    return ACCOUNT_COLORS[(number - 1) % len(ACCOUNT_COLORS)]


def banner_text(number: int) -> str:
    """Words shown in the banner at the top of each window."""
    return f"ACCOUNT {number} - log into your chat site here"


def banner_script(number: int) -> str:
    """JavaScript that draws the coloured banner (re-added if a page removes it)."""
    text = json.dumps(banner_text(number))
    color = json.dumps(account_color(number))
    return f"""(() => {{
  const ID = "__fanout_banner__";
  const add = () => {{
    if (!document.documentElement || document.getElementById(ID)) return;
    const bar = document.createElement("div");
    bar.id = ID;
    bar.textContent = {text};
    bar.style.cssText = "position:fixed;top:0;left:0;right:0;height:{BANNER_HEIGHT}px;z-index:2147483647;" +
      "background:" + {color} + ";color:#fff;font:600 14px system-ui,sans-serif;display:flex;" +
      "align-items:center;justify-content:center;pointer-events:none;box-shadow:0 1px 4px rgba(0,0,0,.4)";
    document.documentElement.appendChild(bar);
  }};
  add();
  document.addEventListener("DOMContentLoaded", add);
  new MutationObserver(add).observe(document, {{childList: true}});
}})();"""


def start_page_url(number: int) -> str:
    """A tiny built-in page telling the person what to do in a fresh window."""
    html = (
        f"<title>FanOut account {number}</title>"
        "<body style='font-family:system-ui,sans-serif;margin:0;padding:72px 40px;background:#fafafa'>"
        f"<h1 style='color:{account_color(number)}'>Account {number}</h1>"
        "<p>Type your chat site's address in the address bar above, then log in as you normally do.</p>"
        "<p>FanOut notices when the chat box is ready. You log in yourself; "
        "FanOut never types or stores your login details.</p>"
    )
    return "data:text/html;charset=utf-8," + quote(html)


def tile_geometry(index: int, count: int, screen_w: int, screen_h: int, bottom_margin: int = 48) -> tuple[int, int, int, int]:
    """(x, y, width, height) of window `index` (0-based) when `count` windows share the screen."""
    count = max(1, min(6, count))
    cols = count if count <= 3 else (2 if count == 4 else 3)
    rows = 1 if count <= 3 else 2
    usable_h = max(200, screen_h - bottom_margin)
    width, height = screen_w // cols, usable_h // rows
    col, row = index % cols, index // cols
    return (col * width, row * height, width, height)


def origin_of(url: str) -> str | None:
    """'https://host' for an http(s) address, else None. Paths and queries are never stored."""
    parts = urlsplit(url)
    if parts.scheme in ("http", "https") and parts.hostname:
        return f"{parts.scheme}://{parts.netloc}"
    return None


@dataclass(frozen=True)
class SlotSpec:
    """How to open one account window."""

    number: int  # 1-based
    user_data_dir: Path
    profile_directory: str | None = None  # Mode B only: which profile inside user_data_dir
    import_from: Path | None = None  # Brave profile folder whose logins to copy (opt-in)
    temporary: bool = False  # delete user_data_dir when the window closes
    fallback: SlotSpec | None = None  # tried if this spec cannot be opened


def sessions_dir() -> Path:
    """Folder holding every app-managed browser profile."""
    return app_data_dir() / "sessions"


def app_managed_specs(count: int, import_from: list[Path | None] | None = None) -> list[SlotSpec]:
    """Mode A: a private persistent profile per account, optionally seeded from Brave profiles."""
    sources = import_from or []
    return [
        SlotSpec(
            number=i,
            user_data_dir=sessions_dir() / f"account_{i}",
            import_from=sources[i - 1] if i - 1 < len(sources) else None,
        )
        for i in range(1, count + 1)
    ]


def plan_reuse_profiles(profiles: list[BraveProfile], temp_base: Path) -> list[SlotSpec]:
    """Mode B: the first profile of each Brave data folder opens in place; others use a private copy.

    One browser data folder can only be opened once at a time, so any further profile from
    the same folder (and any profile that cannot be opened in place) is cloned, using the
    same selective copy as the login import.
    """
    seen: set[Path] = set()
    specs: list[SlotSpec] = []
    for i, profile in enumerate(profiles, start=1):
        clone = SlotSpec(i, temp_base / f"clone_{i}", import_from=profile.path, temporary=True)
        if profile.root in seen:
            specs.append(clone)
        else:
            seen.add(profile.root)
            specs.append(SlotSpec(i, profile.root, profile_directory=profile.folder, fallback=clone))
    return specs


def import_logins(src_profile: Path, dest_user_data_dir: Path) -> bool:
    """Copy ONLY cookies, Preferences and Local State into a fresh profile. False means 'just log in'.

    Never copies saved-login databases, history, extensions or caches. An existing logged-in
    profile is left alone. Cookies may still not decrypt on another profile; that is fine.
    """
    dest_profile = dest_user_data_dir / "Default"
    if (dest_profile / "Network" / "Cookies").exists():
        return True
    plan = [(src_profile / rel, dest_profile / rel) for rel in IMPORT_FILES if (src_profile / rel).is_file()]
    local_state = src_profile.parent / "Local State"
    if local_state.is_file():
        plan.append((local_state, dest_user_data_dir / "Local State"))
    if not any(dst.name == "Cookies" for _, dst in plan):
        return False
    copied: list[Path] = []
    try:
        for src, dst in plan:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            copied.append(dst)
    except OSError:
        log.warning("Login import failed; falling back to a fresh login", exc_info=True)
        for path in copied:
            path.unlink(missing_ok=True)
        return False
    return True


def _is_ours(proc: Any, me: Any) -> bool:
    """True if proc is a descendant of this program (so it is one of our own browsers)."""
    try:
        return me.pid in {p.pid for p in proc.parents()}
    except Exception:  # noqa: BLE001 - psutil raises several error types for vanished processes
        return False


def reap_orphans(private_dirs: list[Path]) -> int:
    """Terminate browsers still using our private profile folders (or our own child browsers).

    Called after a normal close, so it normally finds nothing. Returns how many it stopped.
    """
    try:
        import psutil
    except ImportError:
        return 0
    me = psutil.Process(os.getpid())
    wanted = [str(d) for d in private_dirs]
    victims = []
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmd = " ".join(proc.info.get("cmdline") or [])
        except (psutil.Error, OSError):
            continue
        if proc.pid != me.pid and any(w in cmd for w in wanted) and ("--user-data-dir" in cmd):
            victims.append(proc)
    for proc in victims:
        try:
            proc.terminate()
        except psutil.Error:
            continue
    _, alive = psutil.wait_procs(victims, timeout=3)
    for proc in alive:
        try:
            proc.kill()
        except psutil.Error:
            continue
    return len(victims)


async def _start_playwright() -> Any:
    from playwright.async_api import async_playwright

    return await async_playwright().start()


Emit = Callable[..., None]
PlaywrightFactory = Callable[[], Awaitable[Any]]


class SessionManager:
    """Owns every Playwright object. Methods must only be awaited on the worker's loop."""

    def __init__(
        self,
        emit: Emit,
        playwright_factory: PlaywrightFactory = _start_playwright,
        poll_seconds: float = 1.0,
    ) -> None:
        self.emit = emit
        self._factory = playwright_factory
        self.poll_seconds = poll_seconds
        self._pw: Any = None
        self._contexts: dict[int, Any] = {}
        self._specs: dict[int, SlotSpec] = {}
        self._watchers: dict[int, asyncio.Task[None]] = {}
        self.sends = engine.SendTracker()

    # ----- opening --------------------------------------------------------------------------

    async def open_all(
        self,
        specs: list[SlotSpec],
        exe: Path | None,
        screen: tuple[int, int],
        geometry: dict[str, list[int]] | None = None,
        last_urls: dict[str, str] | None = None,
    ) -> None:
        """Open every window at once. One failing window never stops the others."""
        if self._contexts:
            await self.close_all()
        if self._pw is None:
            self._pw = await self._factory()
        count = len(specs)
        results = await asyncio.gather(
            *(
                self._open_one(spec, i, count, exe, screen, geometry or {}, last_urls or {})
                for i, spec in enumerate(specs)
            ),
            return_exceptions=True,
        )
        for spec, result in zip(specs, results, strict=True):
            if isinstance(result, BaseException):
                friendly = describe(result)
                self.emit("slot_error", slot=spec.number, message=friendly.message, fix=friendly.fix)

    async def _launch(
        self, spec: SlotSpec, index: int, count: int, exe: Path | None, screen: tuple[int, int], saved: list[int] | None
    ) -> tuple[Any, tuple[int, int, int, int]]:
        spec.user_data_dir.mkdir(parents=True, exist_ok=True)
        if spec.import_from is not None and not import_logins(spec.import_from, spec.user_data_dir):
            self.emit(
                "slot_note",
                slot=spec.number,
                message="Couldn't copy your existing logins - just log in once, it'll be remembered.",
            )
        rect = tuple(saved) if saved else tile_geometry(index, count, screen[0], screen[1])
        x, y, w, h = rect
        args = [f"--window-position={x},{y}", f"--window-size={w},{h}"]
        if spec.profile_directory:
            args.append(f"--profile-directory={spec.profile_directory}")
        options: dict[str, Any] = {"headless": False, "args": args, "no_viewport": True}
        if exe is not None:
            options["executable_path"] = str(exe)
        context = await self._pw.chromium.launch_persistent_context(str(spec.user_data_dir), **options)
        return context, (x, y, w, h)

    async def _open_one(
        self,
        spec: SlotSpec,
        index: int,
        count: int,
        exe: Path | None,
        screen: tuple[int, int],
        geometry: dict[str, list[int]],
        last_urls: dict[str, str],
    ) -> None:
        saved = geometry.get(str(spec.number))
        try:
            context, rect = await self._launch(spec, index, count, exe, screen, saved)
        except Exception:
            if spec.fallback is None:
                raise
            log.warning("Could not open %s directly; using a private copy", spec.user_data_dir, exc_info=True)
            self.emit(
                "slot_note",
                slot=spec.number,
                message="Couldn't open your real profile directly, so FanOut is using a private copy of its logins.",
            )
            spec = spec.fallback
            context, rect = await self._launch(spec, index, count, exe, screen, saved)
        number = spec.number
        self._contexts[number] = context
        self._specs[number] = spec
        context.on("close", lambda *_: self._on_closed(number))
        await context.add_init_script(banner_script(number))
        page = context.pages[0] if context.pages else await context.new_page()
        for existing in list(context.pages):
            await self._try(existing.evaluate(banner_script(number)))
        await self._set_bounds(context, page, rect)
        await self._go(page, last_urls.get(str(number)), start_page_url(number))
        self._watchers[number] = asyncio.create_task(self._watch(number, context))
        self.emit("slot_open", slot=number)

    @staticmethod
    async def _try(awaitable: Awaitable[Any]) -> Any:
        try:
            return await awaitable
        except Exception:
            log.debug("Page tweak failed", exc_info=True)
            return None

    async def _go(self, page: Any, url: str | None, fallback_url: str) -> None:
        """Open the remembered site, or the built-in start page if that fails."""
        if url:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                return
            except Exception:  # noqa: BLE001 - a page that is mid-navigation or closed is simply skipped
                log.info("Could not reopen %s; showing the start page", url)
        await self._try(page.goto(fallback_url))

    async def _set_bounds(self, context: Any, page: Any, rect: tuple[int, int, int, int]) -> None:
        """Place the window exactly, in addition to the launch flags (some systems ignore those)."""
        x, y, w, h = rect
        try:
            session = await context.new_cdp_session(page)
            try:
                target = await session.send("Browser.getWindowForTarget")
                bounds = {"left": x, "top": y, "width": w, "height": h, "windowState": "normal"}
                await session.send("Browser.setWindowBounds", {"windowId": target["windowId"], "bounds": bounds})
            finally:
                await session.detach()
        except Exception:
            log.debug("Could not set window bounds", exc_info=True)

    # ----- watching -------------------------------------------------------------------------

    async def _watch(self, number: int, context: Any) -> None:
        """Poll the window; report when a usable chat input appears or the site changes."""
        last: tuple[bool, str] | None = None
        while True:
            await asyncio.sleep(self.poll_seconds)
            ready, host = False, ""
            for page in list(context.pages):
                try:
                    here = urlsplit(page.url).hostname or ""
                    host = host or here
                    if await page.evaluate(READY_JS):
                        ready, host = True, here or host
                        break
                except Exception:  # noqa: BLE001, S112 - skip a window that is closing
                    continue
            if (ready, host) != last:
                last = (ready, host)
                self.emit("slot_state", slot=number, ready=ready, host=host)

    def _on_closed(self, number: int) -> None:
        """The person closed a window themselves."""
        watcher = self._watchers.pop(number, None)
        if watcher:
            watcher.cancel()
        self._contexts.pop(number, None)
        self.emit("slot_closed", slot=number)

    # ----- mapping --------------------------------------------------------------------------

    def _chat_page(self, number: int) -> Any | None:
        """The window's page that shows a website (not the built-in start page), if any."""
        context = self._contexts.get(number)
        pages = [p for p in (context.pages if context else []) if origin_of(p.url)]
        return pages[-1] if pages else None

    def _no_page(self, number: int) -> None:
        self.emit(
            "map_result",
            slot=number,
            ok=False,
            message=f"Account {number}: there is no chat page open in that window.",
            fix="Go to your chat site in that window and log in first.",
            origin="",
            mapping={},
            notes=[],
        )

    async def map_account(self, slot: int, mapping: dict[str, str] | None = None) -> None:
        """Auto-detect this window's chat page (or test the given selectors) with a harmless dry run."""
        page = self._chat_page(slot)
        if page is None:
            self._no_page(slot)
            return
        given = mapping_tools.Mapping.from_dict(mapping) if mapping else None
        result = await mapping_tools.map_account(page, slot, given)
        self.emit(
            "map_result",
            slot=slot,
            ok=result.ok,
            message=result.message,
            fix=result.fix,
            origin=origin_of(page.url) or "",
            mapping=result.mapping.to_dict(),
            notes=result.notes,
        )

    async def pick_element(self, slot: int, kind: str) -> None:
        """Let the person click the chat box, send button or a reply in this window."""
        page = self._chat_page(slot)
        if page is None:
            self._no_page(slot)
            return
        picked = await mapping_tools.pick(page, kind)
        self.emit("pick_result", slot=slot, pick_kind=kind, ok=picked.ok, selector=picked.selector, cancelled=picked.cancelled)

    # ----- sending --------------------------------------------------------------------------

    async def send_all(self, jobs: list[dict[str, Any]], settings: dict[str, Any] | None = None) -> None:
        """Send a different prompt in each listed window at the same time and wait for the replies."""
        job_list = [
            engine.Job(
                slot=int(j["slot"]),
                prompt=str(j["prompt"]),
                mapping=mapping_tools.Mapping.from_dict(j.get("mapping")),
                site=str(j.get("site") or ""),
            )
            for j in jobs
        ]
        pages = {job.slot: page for job in job_list if (page := self._chat_page(job.slot)) is not None}
        await engine.run_all(
            pages, job_list, engine.RunSettings.from_dict(settings), self.sends, self.emit,
            results_dir=app_data_dir() / "results", shots=log_dir(),
        )

    # ----- reporting and closing ------------------------------------------------------------

    async def snapshot(self) -> dict[str, Any]:
        """Current window positions and sites (origin only), for restoring next time."""
        geometry: dict[str, list[int]] = {}
        urls: dict[str, str] = {}
        for number, context in list(self._contexts.items()):
            for page in list(context.pages):
                try:
                    origin = origin_of(page.url)
                    if origin:
                        urls[str(number)] = origin
                    session = await context.new_cdp_session(page)
                    try:
                        bounds = (await session.send("Browser.getWindowForTarget"))["bounds"]
                    finally:
                        await session.detach()
                    geometry[str(number)] = [bounds["left"], bounds["top"], bounds["width"], bounds["height"]]
                    break
                except Exception:  # noqa: BLE001, S112 - skip a window that is closing
                    continue
        return {"window_geometry": geometry, "last_urls": urls}

    async def close_all(self) -> None:
        """Close every window, delete temporary copies, and make sure no browser is left behind."""
        for watcher in self._watchers.values():
            watcher.cancel()
        await asyncio.gather(*self._watchers.values(), return_exceptions=True)
        self._watchers.clear()
        contexts = list(self._contexts.values())
        self._contexts.clear()
        await asyncio.gather(*(self._close_context(c) for c in contexts), return_exceptions=True)
        private = [s.user_data_dir for s in self._specs.values() if s.temporary or sessions_dir() in s.user_data_dir.parents]
        for spec in self._specs.values():
            if spec.temporary:
                shutil.rmtree(spec.user_data_dir, ignore_errors=True)
        self._specs.clear()
        stopped = reap_orphans(private)
        if stopped:
            log.warning("Stopped %d leftover browser process(es)", stopped)
        self.emit("windows_closed")

    @staticmethod
    async def _close_context(context: Any) -> None:
        await asyncio.wait_for(context.close(), timeout=10)

    async def shutdown(self) -> dict[str, Any]:
        """Final snapshot, close everything, stop Playwright. Returns the snapshot."""
        try:
            final = await self.snapshot()
        except Exception:  # noqa: BLE001 - already stopped
            final = {}
        await self.close_all()
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                log.debug("Playwright stop failed", exc_info=True)
            self._pw = None
        return final

    # ----- Mode B helper --------------------------------------------------------------------

    @staticmethod
    def _users_brave_pids() -> list[int]:
        """Main Brave processes that belong to the person, not to FanOut's own windows."""
        import psutil

        me = psutil.Process(os.getpid())
        pids: list[int] = []
        for pid, cmd in running_brave_processes():
            if any(str(sessions_dir()) in part for part in cmd):
                continue
            try:
                if not _is_ours(psutil.Process(pid), me):
                    pids.append(pid)
            except psutil.Error:
                continue
        return pids

    async def close_user_brave(self, timeout: float = 15.0) -> bool:
        """Ask the person's own Brave to quit politely. Only called after they agreed. Never forces."""
        if not self._users_brave_pids():
            self.emit("brave_closed", ok=True)
            return True
        if sys.platform == "win32":
            polite = await asyncio.create_subprocess_exec(  # no /F: asks Brave to close, never forces
                "taskkill", "/IM", "brave.exe", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            await polite.wait()
        else:
            import psutil

            for pid in self._users_brave_pids():
                try:
                    psutil.Process(pid).terminate()
                except psutil.Error:
                    continue
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if not self._users_brave_pids():
                self.emit("brave_closed", ok=True)
                return True
            await asyncio.sleep(0.5)
        self.emit("brave_closed", ok=False)
        return False
