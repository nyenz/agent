"""Mapping: learn where the chat box, send button and reply text are on each site.

Three ways to get there, easiest first: auto-detect (js/detect.js), a visual picker where the
person just clicks the element (js/picker.js), and a collapsed "Advanced" text field. Every
mapping is proved by a harmless dry run (type a test string, check it landed, clear it) that
NEVER clicks send. Everything here takes a Playwright page and runs on the worker's loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config import MAPPING_KEYS
from .errors import describe

log = logging.getLogger(__name__)

PROBE_TEXT = "fanout-test"
SELECT_ALL = "Meta+A" if sys.platform == "darwin" else "Control+A"
PICK_KINDS = ("input", "send", "message")
READ_BOX_JS = "el => ('value' in el) ? el.value : (el.innerText || el.textContent || '')"
POLL_PICK_JS = "() => ({picked: window.__fanoutPicked || null, active: !!window.__fanoutPickActive})"
STOP_PICK_JS = "() => { if (window.__fanoutPickStop) window.__fanoutPickStop(); }"
FIX_HINT = "Make sure you are logged in and the chat page is showing, then try again, or use 'Fix by clicking'."


@dataclass
class Mapping:
    """The three selectors for one account on one site. Empty text means 'not known'."""

    input_selector: str = ""
    send_selector: str = ""
    message_selector: str = ""

    def to_dict(self) -> dict[str, str]:
        """Plain dict for config.json and for events."""
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> Mapping:
        """Build from stored data, ignoring anything that isn't text."""
        raw = raw or {}
        return cls(**{k: str(raw[k]) for k in MAPPING_KEYS if isinstance(raw.get(k), str)})


@dataclass
class MapResult:
    """Outcome of auto-mapping or testing one account."""

    ok: bool
    message: str
    fix: str = ""
    mapping: Mapping = field(default_factory=Mapping)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PickResult:
    """Outcome of one visual pick."""

    ok: bool
    selector: str = ""
    cancelled: bool = False


def get_mapping(mappings: dict[str, dict[str, dict[str, str]]], account: int, origin: str) -> Mapping | None:
    """The saved mapping for this account on this site, if any."""
    raw = mappings.get(str(account), {}).get(origin)
    return Mapping.from_dict(raw) if raw else None


def store_mapping(mappings: dict[str, dict[str, dict[str, str]]], account: int, origin: str, mapping: Mapping) -> None:
    """Remember a mapping (in place). Only the selectors are kept, never page content."""
    mappings.setdefault(str(account), {})[origin] = mapping.to_dict()


def find_by_host(mappings: dict[str, dict[str, dict[str, str]]], account: int, host: str) -> Mapping | None:
    """The saved mapping for this account on the site with this host name, if any."""
    for origin, raw in mappings.get(str(account), {}).items():
        if urlsplit(origin).hostname == host:
            return Mapping.from_dict(raw)
    return None


@cache
def load_js(name: str) -> str:
    """Text of fanout/js/<name>.js."""
    return (Path(__file__).parent / "js" / f"{name}.js").read_text(encoding="utf-8")


def build_script(name: str, prefix: str = "") -> str:
    """An expression for page.evaluate: the shared selector helpers, then js/<name>.js."""
    return "(() => {\n" + load_js("selector") + "\n" + prefix + load_js(name) + "\n})()"


async def detect(page: Any) -> Mapping:
    """Look for the chat box, send button and reply text on the current page."""
    found = await page.evaluate(build_script("detect"))
    return Mapping(
        input_selector=found.get("input") or "",
        send_selector=found.get("send") or "",
        message_selector=found.get("message") or "",
    )


async def clear_box(page: Any, locator: Any) -> None:
    """Empty the chat box: select all, then Delete."""
    await locator.focus()
    await page.keyboard.press(SELECT_ALL)
    await page.keyboard.press("Delete")


async def read_box(locator: Any) -> str:
    """Text currently in the chat box."""
    return str(await locator.evaluate(READ_BOX_JS))


async def type_text(page: Any, locator: Any, text: str) -> str:
    """Put text in the chat box. Tries three ways; returns the one that worked ('' if none did)."""
    for method in ("insert_text", "fill", "type"):
        try:
            await clear_box(page, locator)
            if method == "insert_text":
                await page.keyboard.insert_text(text)
            elif method == "fill":
                await locator.fill(text)
            else:
                await locator.press_sequentially(text, delay=20)
            if text in await read_box(locator):
                return method
        except Exception:
            log.debug("Typing with %s failed", method, exc_info=True)
    return ""


async def map_account(page: Any, number: int, mapping: Mapping | None = None) -> MapResult:
    """Auto-detect (or test the given mapping) and prove it with a dry run. Never clicks send."""
    label = f"Account {number}"
    locator = None
    try:
        found = mapping if mapping is not None else await detect(page)
        if not found.input_selector:
            return MapResult(False, f"{label}: I couldn't find the chat box on this page.", FIX_HINT)
        locator = page.locator(found.input_selector).first
        if await locator.count() == 0:
            return MapResult(False, f"{label}: the chat box I was told about isn't on this page.", FIX_HINT, found)
        if not await type_text(page, locator, PROBE_TEXT):
            return MapResult(False, f"{label}: I couldn't type into the chat box.", FIX_HINT, found)
        # Many sites only show the send button once there is text, so look again now.
        after = await detect(page)
        final = Mapping(
            found.input_selector,
            found.send_selector or after.send_selector,
            found.message_selector or after.message_selector,
        )
        notes: list[str] = []
        if not final.send_selector or not await page.locator(final.send_selector).first.is_visible():
            notes.append("No send button found - FanOut will press Enter to send.")
        if not final.message_selector:
            notes.append("The reply area will be found after the first answer.")
        return MapResult(True, f"{label} mapped", "", final, notes)
    except Exception as exc:
        friendly = describe(exc)
        return MapResult(False, f"{label}: {friendly.message}", friendly.fix)
    finally:
        if locator is not None:
            try:
                await clear_box(page, locator)
            except Exception:
                log.debug("Could not clear the test text", exc_info=True)


async def pick(page: Any, kind: str, timeout: float = 120.0, poll: float = 0.3) -> PickResult:
    """Let the person click the element for `kind`. Re-arms itself if the page navigates."""
    if kind not in PICK_KINDS:
        raise ValueError(f"Unknown pick kind: {kind}")
    script = build_script("picker", f"const KIND = {json.dumps(kind)};\n")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    armed = False
    try:
        while loop.time() < deadline:
            try:
                if armed:
                    state = await page.evaluate(POLL_PICK_JS)
                    if state["picked"]:
                        done = state["picked"]
                        return PickResult(bool(done.get("ok")), str(done.get("selector") or ""), bool(done.get("cancelled")))
                    armed = bool(state["active"])
                if not armed:
                    await page.evaluate(script)  # first time, and again after every navigation
                    armed = True
            except Exception:
                armed = False
                if page.is_closed():
                    break
            await asyncio.sleep(poll)
        return PickResult(False, cancelled=True)
    finally:
        try:
            await page.evaluate(STOP_PICK_JS)
        except Exception:
            log.debug("Could not leave pick mode", exc_info=True)
