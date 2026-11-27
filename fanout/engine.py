"""Engine: put a prompt in each window, send it, and wait until the reply has really finished.

Every account runs at the same time (asyncio.gather). One account failing, timing out or hitting a
CAPTCHA never stops the others. All Playwright work happens on the worker's loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from .config import Config
from .errors import describe
from .mapping import Mapping, build_script, detect, read_box, type_text
from .results import AccountResult, clean_text, html_to_markdown, save_run

log = logging.getLogger(__name__)

Emit = Callable[..., None]
SEND_ORDER = ("button", "enter", "ctrl_enter", "meta_enter", "redetect")
SEND_KEYS = {"enter": "Enter", "ctrl_enter": "Control+Enter", "meta_enter": "Meta+Enter"}
GENTLE_MIN_DELAY = 10.0


@dataclass
class RunSettings:
    """How patient and how gentle a run is."""

    stable_seconds: float = 3.0
    timeout_seconds: float = 180.0
    min_delay_seconds: float = 5.0
    be_gentle: bool = False
    poll_seconds: float = 0.4
    send_check_seconds: float = 1.5

    @property
    def delay_between_sends(self) -> float:
        """Least time between two sends from the same account ('be gentle' doubles it, 10 s at least)."""
        if self.be_gentle:
            return max(self.min_delay_seconds * 2, GENTLE_MIN_DELAY)
        return self.min_delay_seconds

    @classmethod
    def from_config(cls, cfg: Config) -> RunSettings:
        """Take the person's saved settings."""
        return cls(
            stable_seconds=cfg.stable_seconds,
            timeout_seconds=float(cfg.timeout_seconds),
            min_delay_seconds=cfg.min_delay_seconds,
            be_gentle=cfg.be_gentle,
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> RunSettings:
        """Build from an event/command payload, ignoring unknown keys."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (raw or {}).items() if k in known})


@dataclass
class Job:
    """One account's task in a run."""

    slot: int
    prompt: str
    mapping: Mapping
    site: str = ""


class RunProblem(Exception):
    """Something went wrong that the person can understand and fix."""

    def __init__(self, message: str, fix: str = "") -> None:
        super().__init__(message)
        self.message, self.fix = message, fix


class ReplyTimeout(Exception):
    """The reply did not finish in time; `partial` is whatever had arrived."""

    def __init__(self, partial: str = "") -> None:
        super().__init__("timeout")
        self.partial = partial


class ReplyWatcher:
    """Decides, one look at a time, whether a reply has finished. No browser needed.

    A reply is new once the newest message differs from the 'before' snapshot (or a new message
    appeared) and is not just the prompt. It is finished when its text has not changed for
    `stable_seconds` AND the site shows no Stop button.
    """

    def __init__(self, before: str, prompt: str, stable_seconds: float, before_count: int = 0) -> None:
        self.before = self._norm(before)
        self.prompt = self._norm(prompt)
        self.before_count = before_count
        self.stable_seconds = stable_seconds
        self.text = ""
        self.changed_at: float | None = None

    @staticmethod
    def _norm(text: str) -> str:
        return " ".join(text.split())

    def feed(self, text: str, busy: bool, now: float, count: int = 0) -> str:
        """Look at the page once. Returns 'waiting' (nothing new), 'streaming' or 'done'."""
        norm = self._norm(text)
        is_new = bool(norm) and norm != self.prompt and (norm != self.before or count > self.before_count)
        if not is_new:
            self.text, self.changed_at = "", None
            return "waiting"
        if norm != self.text or self.changed_at is None:
            self.text, self.changed_at = norm, now
        if busy:
            self.changed_at = now  # the site says it is still writing
        return "done" if now - self.changed_at >= self.stable_seconds else "streaming"


class SendTracker:
    """Keeps the minimum delay between sends per account, and remembers which way of sending worked."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.last_send: dict[int, float] = {}
        self.method: dict[int, str] = {}

    def wait_needed(self, slot: int, delay: float) -> float:
        """Seconds still to wait before this account may send again."""
        last = self.last_send.get(slot)
        return 0.0 if last is None else max(0.0, last + delay - self.clock())

    def mark(self, slot: int, method: str = "") -> None:
        """Note that this account just sent."""
        self.last_send[slot] = self.clock()
        if method:
            self.method[slot] = method


async def read_reply(page: Any, message_selector: str) -> dict[str, Any]:
    """Text, cleaned HTML, message count, Stop-button and CAPTCHA/limit flags for the newest reply."""
    script = build_script("reply", f"const MESSAGE_SELECTOR = {json.dumps(message_selector)};\n")
    for _ in range(5):
        try:
            return dict(await page.evaluate(script))
        except Exception as exc:
            if page.is_closed():
                raise RunProblem("This window was closed.", "Open the windows again from the Scan screen.") from exc
            await asyncio.sleep(0.2)
    raise RunProblem("The page kept changing, so I couldn't read it.", "Wait a moment and press Retry failed.")


async def press_send(page: Any, locator: Any, mapping: Mapping, method: str) -> bool:
    """Try one way of sending. False if that way isn't possible on this page."""
    try:
        if method in ("button", "redetect"):
            selector = mapping.send_selector if method == "button" else (await detect(page)).send_selector
            if not selector:
                return False
            button = page.locator(selector).first
            if await button.count() == 0 or not await button.is_visible():
                return False
            await button.click(timeout=2000)
            return True
        await locator.focus()
        await page.keyboard.press(SEND_KEYS[method])
        return True
    except Exception:
        log.debug("Sending with %s failed", method, exc_info=True)
        return False


async def send_message(page: Any, locator: Any, mapping: Mapping, prompt: str, remembered: str, check_seconds: float) -> str:
    """Send what is typed. Tries the send button, Enter, Ctrl+Enter, Meta+Enter, then looks for the button again."""
    order = list(SEND_ORDER)
    if remembered in order:
        order.remove(remembered)
        order.insert(0, remembered)
    probe = " ".join(prompt.split())[:40]
    for method in order:
        if not await press_send(page, locator, mapping, method):
            continue
        await asyncio.sleep(check_seconds)
        try:
            still_there = probe in " ".join((await read_box(locator)).split())
        except Exception:
            still_there = False
        if not still_there:
            return method
    raise RunProblem(
        "I typed the prompt but couldn't send it.",
        "Press the send button yourself in that window, then use 'Map this account' or 'Fix by clicking' on the login screen.",
    )


async def stop_generation(page: Any) -> None:
    """Press the site's own Stop button if it is showing (used when the person cancels)."""
    with contextlib.suppress(Exception):
        await asyncio.wait_for(page.evaluate(build_script("stop")), timeout=2)


async def screenshot(page: Any, folder: Path | None, slot: int, state: str) -> None:
    """Save a picture of the window for the log folder. Best effort."""
    if folder is None:
        return
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"account{slot}-{state}-{time.strftime('%Y%m%d-%H%M%S')}.png"
        await page.screenshot(path=str(path), timeout=5000)
        log.info("Saved a screenshot of account %s (%s): %s", slot, state, path.name)
    except Exception:
        log.debug("Could not take a screenshot", exc_info=True)


def blocked_problem(slot: int, kind: str) -> RunProblem:
    """The plain-English problem for a CAPTCHA or a usage limit (never solved or bypassed)."""
    if kind == "captcha":
        return RunProblem(
            f"ACCOUNT {slot} is showing a CAPTCHA (a check that you are a person).",
            "Solve it yourself in that window, then press Retry failed. FanOut will not solve it for you.",
        )
    return RunProblem(
        f"ACCOUNT {slot} says you have reached a usage limit.",
        "Wait for the limit to pass, or use another account, then press Retry failed.",
    )


async def run_one(
    page: Any,
    job: Job,
    settings: RunSettings,
    tracker: SendTracker,
    emit: Emit,
    sink: dict[int, AccountResult],
    shots: Path | None = None,
) -> AccountResult:
    """Run one account from start to finish. Always ends by emitting one `run_result`."""
    loop = asyncio.get_running_loop()
    started_at, t0 = time.time(), loop.time()
    partial = ""

    def status(state: str, detail: str = "") -> None:
        emit("run_status", slot=job.slot, state=state, elapsed=loop.time() - t0, detail=detail)

    def finish(state: str, **extra: Any) -> AccountResult:
        result = AccountResult(
            slot=job.slot, site=job.site, prompt=job.prompt, state=state, elapsed=loop.time() - t0,
            started_at=started_at, finished_at=time.time(), **extra,
        )
        sink[job.slot] = result
        emit("run_result", **result.to_dict())
        return result

    try:
        wait = tracker.wait_needed(job.slot, settings.delay_between_sends)
        if wait > 0:
            status("idle", f"Waiting {wait:.0f} s so this account isn't asked too fast")
            await asyncio.sleep(wait)
        status("typing")
        mapping = job.mapping
        if not mapping.input_selector or await page.locator(mapping.input_selector).first.count() == 0:
            found = await detect(page)  # nothing saved, or the page changed: look again
            mapping = Mapping(found.input_selector, mapping.send_selector or found.send_selector,
                              mapping.message_selector or found.message_selector)
        if not mapping.input_selector:
            raise RunProblem(f"ACCOUNT {job.slot}: I couldn't find the chat box.",
                             "Make sure the chat page is showing and you are logged in, then use 'Map this account'.")
        locator = page.locator(mapping.input_selector).first
        before = await read_reply(page, mapping.message_selector)
        if not await type_text(page, locator, job.prompt):
            raise RunProblem(f"ACCOUNT {job.slot}: I couldn't type into the chat box.",
                             "Click inside the chat box in that window once, then press Retry failed.")
        method = await send_message(page, locator, mapping, job.prompt, tracker.method.get(job.slot, ""),
                                    settings.send_check_seconds)
        tracker.mark(job.slot, method)
        status("sent")
        watcher = ReplyWatcher(before["text"], job.prompt, settings.stable_seconds, int(before.get("count", 0)))
        sent_at, last_seen, announced = loop.time(), None, False
        message_selector = mapping.message_selector
        while True:
            if not message_selector:  # the reply area often only exists once the first reply starts
                message_selector = (await detect(page)).message_selector or ""
            info = await read_reply(page, message_selector)
            if info.get("blocked"):
                raise blocked_problem(job.slot, str(info["blocked"]))
            verdict = watcher.feed(str(info["text"]), bool(info["busy"]), loop.time(), int(info.get("count", 0)))
            if verdict != "waiting":
                partial = clean_text(str(info["text"]))
                if not announced:
                    status("streaming")
                    announced = True
                if partial != last_seen:
                    last_seen = partial
                    emit("run_progress", slot=job.slot, preview=partial[-240:], words=len(partial.split()))
            if verdict == "done":
                return finish("done", text=partial, markdown=html_to_markdown(str(info["html"])) or partial,
                              send_method=method)
            if loop.time() - sent_at > settings.timeout_seconds:
                raise ReplyTimeout(partial)
            await asyncio.sleep(settings.poll_seconds)
    except asyncio.CancelledError:
        await stop_generation(page)
        finish("cancelled", text=partial, message="You cancelled this run.")
        raise
    except ReplyTimeout as exc:
        await screenshot(page, shots, job.slot, "timeout")
        return finish("timeout", text=exc.partial,
                      message=f"ACCOUNT {job.slot}: no finished answer after {settings.timeout_seconds:.0f} seconds.",
                      fix="Look at that window. If the site is still writing, raise the wait time in Settings. Then press Retry failed.")
    except RunProblem as exc:
        await screenshot(page, shots, job.slot, "error")
        return finish("error", text=partial, message=exc.message, fix=exc.fix)
    except Exception as exc:
        friendly = describe(exc)
        await screenshot(page, shots, job.slot, "error")
        return finish("error", text=partial, message=f"ACCOUNT {job.slot}: {friendly.message}", fix=friendly.fix)


async def run_all(
    pages: dict[int, Any],
    jobs: list[Job],
    settings: RunSettings,
    tracker: SendTracker,
    emit: Emit,
    results_dir: Path | None = None,
    shots: Path | None = None,
) -> list[AccountResult]:
    """Run every job at once. Saves the run to `results_dir` and ends with one `run_finished`."""
    sink: dict[int, AccountResult] = {}
    started = time.time()
    emit("run_started", slots=[j.slot for j in jobs], started_at=started)
    cancelled = False

    async def one(job: Job) -> None:
        page = pages.get(job.slot)
        if page is None:
            result = AccountResult(slot=job.slot, site=job.site, prompt=job.prompt, state="error",
                                   message=f"ACCOUNT {job.slot}: there is no open window for this account.",
                                   fix="Open the windows again from the Scan screen.", started_at=time.time(),
                                   finished_at=time.time())
            sink[job.slot] = result
            emit("run_result", **result.to_dict())
            return
        await run_one(page, job, settings, tracker, emit, sink, shots)

    try:
        outcomes = await asyncio.gather(*(one(j) for j in jobs), return_exceptions=True)
        for job, outcome in zip(jobs, outcomes, strict=True):
            if isinstance(outcome, BaseException) and job.slot not in sink:
                log.error("Account %s failed unexpectedly", job.slot, exc_info=outcome)
                friendly = describe(outcome)
                result = AccountResult(slot=job.slot, site=job.site, prompt=job.prompt, state="error",
                                       message=friendly.message, fix=friendly.fix, finished_at=time.time())
                sink[job.slot] = result
                emit("run_result", **result.to_dict())
    except asyncio.CancelledError:
        cancelled = True
        raise
    finally:
        results = [sink[j.slot] for j in jobs if j.slot in sink]
        folder = ""
        if results and results_dir is not None:
            try:
                folder = str(save_run(results_dir, results, started))
            except OSError:
                log.exception("Could not save the results")
        emit("run_finished", cancelled=cancelled, done=sum(r.ok for r in results), total=len(jobs), folder=folder)
    return results
