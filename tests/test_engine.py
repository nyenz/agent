"""Engine: the finished-reply state machine, sending, concurrency, timeouts, cancel and results."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psutil
import pytest
from browserkit import run_with_browser

from fanout import engine
from fanout.engine import Job, ReplyWatcher, RunSettings, SendTracker, run_all, run_one
from fanout.mapping import Mapping
from fanout.results import (
    AccountResult,
    PromptHistory,
    clean_text,
    copy_all_text,
    export_json,
    export_markdown,
    html_to_markdown,
    save_run,
)
from fanout.sessions import SessionManager

FIXTURES = Path(__file__).parent / "fixtures"
CHAT = (FIXTURES / "stream_chat.html").as_uri()
MAPPING_A = Mapping("#box", '[data-testid="send-button"]', "[data-message-author-role]")
MAPPING_TEXTAREA = Mapping("#box", "", "[data-message-author-role]")
FAST = RunSettings(stable_seconds=1.0, timeout_seconds=30, min_delay_seconds=0, poll_seconds=0.1, send_check_seconds=0.4)


def chat(**params: object) -> str:
    return CHAT + "?" + "&".join(f"{k}={v}" for k, v in params.items())


# ----- the finished-reply state machine (no browser) -------------------------------------------


def test_watcher_waits_until_something_new_appears() -> None:
    w = ReplyWatcher(before="old answer", prompt="my question", stable_seconds=3)
    assert w.feed("old answer", False, 0) == "waiting"        # nothing new yet
    assert w.feed("my question", False, 1) == "waiting"       # only our own prompt on screen
    assert w.feed("", False, 2) == "waiting"
    assert w.feed("Hello", False, 3) == "streaming"


def test_watcher_is_done_only_after_the_text_stays_the_same() -> None:
    w = ReplyWatcher("", "q", stable_seconds=3)
    assert w.feed("Hel", False, 0) == "streaming"
    assert w.feed("Hello wor", False, 2) == "streaming"       # changed: the clock starts again
    assert w.feed("Hello wor", False, 4.9) == "streaming"     # 2.9 s unchanged: not yet
    assert w.feed("Hello wor", False, 5.0) == "done"          # 3.0 s unchanged


def test_watcher_never_finishes_while_a_stop_button_shows() -> None:
    w = ReplyWatcher("", "q", stable_seconds=3)
    assert w.feed("Thinking...", True, 0) == "streaming"
    assert w.feed("Thinking...", True, 60) == "streaming"     # a long pause, but the site is still busy
    assert w.feed("Thinking...", False, 61) == "streaming"    # the button went away: the clock starts now
    assert w.feed("Thinking...", False, 64) == "done"


def test_watcher_counts_a_new_message_even_if_the_words_match_the_old_one() -> None:
    w = ReplyWatcher("same", "q", stable_seconds=1, before_count=1)
    assert w.feed("same", False, 0, count=1) == "waiting"
    assert w.feed("same", False, 1, count=2) == "streaming"   # a second message appeared
    assert w.feed("same", False, 2.1, count=2) == "done"


def test_watcher_starts_over_if_the_reply_disappears() -> None:
    w = ReplyWatcher("old", "q", stable_seconds=2)
    assert w.feed("partial", False, 0) == "streaming"
    assert w.feed("old", False, 1) == "waiting"               # the page went back to the old text
    assert w.feed("partial", False, 1.5) == "streaming"
    assert w.feed("partial", False, 3.0) == "streaming"       # clock restarted at 1.5
    assert w.feed("partial", False, 3.5) == "done"


# ----- settings and the send tracker (no browser) ----------------------------------------------


def test_be_gentle_doubles_the_delay_with_a_floor() -> None:
    assert RunSettings(min_delay_seconds=5).delay_between_sends == 5
    assert RunSettings(min_delay_seconds=5, be_gentle=True).delay_between_sends == 10
    assert RunSettings(min_delay_seconds=1, be_gentle=True).delay_between_sends == 10
    assert RunSettings(min_delay_seconds=20, be_gentle=True).delay_between_sends == 40
    assert RunSettings.from_dict({"stable_seconds": 2, "nonsense": 1}).stable_seconds == 2


def test_send_tracker_keeps_the_gap_per_account_and_remembers_the_way() -> None:
    now = [100.0]
    tracker = SendTracker(clock=lambda: now[0])
    assert tracker.wait_needed(1, 5) == 0                      # never sent: no wait
    tracker.mark(1, "enter")
    now[0] = 102.0
    assert tracker.wait_needed(1, 5) == pytest.approx(3.0)
    assert tracker.wait_needed(2, 5) == 0                      # another account is not held back
    now[0] = 106.0
    assert tracker.wait_needed(1, 5) == 0
    assert tracker.method == {1: "enter"}


# ----- results helpers (no browser) ------------------------------------------------------------


def test_html_to_markdown_covers_the_usual_reply_shapes() -> None:
    html = (
        "<h2>Title</h2><p>Some <strong>bold</strong>, <em>soft</em> and <code>code</code> "
        'with a <a href="https://x.example">link</a>.</p><ul><li>one</li><li>two<ul><li>deep</li></ul></li></ul>'
        '<ol><li>first</li><li>second</li></ol><pre><code class="language-python">print("hi")\nx = 1</code></pre>'
        "<blockquote><p>quoted</p></blockquote><table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
        "<button>Copy</button><script>bad()</script>"
    )
    md = html_to_markdown(html)
    assert "## Title" in md
    assert "Some **bold**, *soft* and `code` with a [link](https://x.example)." in md
    assert "- one\n- two\n  - deep" in md
    assert "1. first\n2. second" in md
    assert '```python\nprint("hi")\nx = 1\n```' in md
    assert "> quoted" in md
    assert "| A | B |\n| --- | --- |\n| 1 | 2 |" in md
    assert "Copy" not in md and "bad()" not in md


def test_clean_text_drops_the_sites_own_buttons() -> None:
    assert clean_text("Hello\nCopy\nRegenerate\n\n\n\nBye\nGood response") == "Hello\n\nBye"


def make_results() -> list[AccountResult]:
    return [
        AccountResult(2, "site-b.example", "q2", "timeout", text="part", message="no answer", elapsed=9.0),
        AccountResult(1, "site-a.example", "q1", "done", text="Answer one", markdown="**Answer** one", elapsed=3.2),
    ]


def test_copy_all_export_and_save(tmp_path: Path) -> None:
    results = make_results()
    copied = copy_all_text(results)
    assert copied.startswith("=== Account 1 - site-a.example ===\n**Answer** one")
    assert "=== Account 2 - site-b.example ===\npart" in copied
    assert "## Account 1 - site-a.example" in export_markdown(results)
    data = json.loads(export_json(results))
    assert [a["account"] if "account" in a else a["slot"] for a in data["accounts"]] == [1, 2]
    assert data["accounts"][0]["words"] == 2
    folder = save_run(tmp_path / "results", results)
    assert sorted(p.name for p in folder.iterdir()) == ["account_1.md", "account_2.md", "run.json"]
    assert "Answer" in (folder / "account_1.md").read_text(encoding="utf-8")
    assert save_run(tmp_path / "results", results) != folder   # same second: still a new folder


def test_prompt_history_is_searchable_and_persistent(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    history = PromptHistory(path)
    for prompt in ("Write a poem", "Write a poem", "Explain tides", "  "):
        history.add(prompt)
    assert [i["prompt"] for i in history.search()] == ["Explain tides", "Write a poem"]
    assert [i["prompt"] for i in PromptHistory(path).search("POEM")] == ["Write a poem"]


# ----- run_one with a scripted page (no browser) -----------------------------------------------


class FakeLocator:
    first: FakeLocator

    def __init__(self) -> None:
        self.first = self

    async def count(self) -> int:
        return 1


class FakePage:
    def __init__(self) -> None:
        self.shots: list[str] = []

    def locator(self, _selector: str) -> FakeLocator:
        return FakeLocator()

    def is_closed(self) -> bool:
        return False

    async def screenshot(self, path: str, timeout: int = 0) -> None:
        self.shots.append(path)
        Path(path).write_bytes(b"png")


def scripted(monkeypatch: pytest.MonkeyPatch, replies: list[dict[str, Any]], typed: str = "insert_text") -> list[str]:
    """Replace the browser-facing helpers: reads follow `replies` (the last one repeats)."""
    calls: list[str] = []
    state = {"i": 0}

    async def read_reply(_page: Any, _selector: str) -> dict[str, Any]:
        item = replies[min(state["i"], len(replies) - 1)]
        state["i"] += 1
        return {"text": "", "html": "", "count": 0, "busy": False, "blocked": "", **item}

    async def type_text(*_a: Any, **_k: Any) -> str:
        return typed

    async def send_message(*_a: Any, **_k: Any) -> str:
        calls.append("sent")
        return "button"

    async def stop(_page: Any) -> None:
        calls.append("stop pressed")

    monkeypatch.setattr(engine, "read_reply", read_reply)
    monkeypatch.setattr(engine, "type_text", type_text)
    monkeypatch.setattr(engine, "send_message", send_message)
    monkeypatch.setattr(engine, "stop_generation", stop)
    return calls


QUICK = RunSettings(stable_seconds=0.3, timeout_seconds=1.0, min_delay_seconds=0, poll_seconds=0.05, send_check_seconds=0)


def run_job(
    page: FakePage, settings: RunSettings = QUICK, shots: Path | None = None
) -> tuple[AccountResult, list[tuple[str, dict[str, Any]]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    sink: dict[int, AccountResult] = {}
    job = Job(1, "my prompt", MAPPING_A, "chat.example")
    result = asyncio.run(run_one(page, job, settings, SendTracker(), lambda k, **d: events.append((k, d)), sink, shots))
    assert sink[1] is result
    return result, events


def test_run_one_finishes_when_the_reply_settles(monkeypatch: pytest.MonkeyPatch) -> None:
    scripted(monkeypatch, [{"text": ""}, {"text": "Hel"}, {"text": "Hello", "html": "<p>Hello <b>you</b></p>"}])
    result, events = run_job(FakePage())
    assert result.state == "done" and result.text == "Hello"
    assert result.markdown == "Hello **you**"
    assert [d["state"] for k, d in events if k == "run_status"] == ["typing", "sent", "streaming"]
    assert events[-1][0] == "run_result" and events[-1][1]["state"] == "done"
    assert any(k == "run_progress" and d["preview"] == "Hello" for k, d in events)


def test_run_one_keeps_waiting_while_the_stop_button_shows(monkeypatch: pytest.MonkeyPatch) -> None:
    busy_for = [{"text": "Thinking", "busy": True}] * 12     # 12 looks x 0.05 s = longer than stable_seconds
    scripted(monkeypatch, [*busy_for, {"text": "Thinking about it", "busy": False}])
    started = time.monotonic()
    result, _ = run_job(FakePage())
    assert result.state == "done" and result.text == "Thinking about it"
    assert time.monotonic() - started > 0.6 + 0.3 - 0.1


def test_run_one_times_out_with_what_arrived_and_a_screenshot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    scripted(monkeypatch, [{"text": ""}, {"text": "Still going", "busy": True}])
    page = FakePage()
    result, _ = run_job(page, shots=tmp_path)
    assert result.state == "timeout" and result.text == "Still going"
    assert "no finished answer" in result.message and result.fix
    assert page.shots and Path(page.shots[0]).exists()


def test_run_one_stops_on_a_captcha_without_touching_it(monkeypatch: pytest.MonkeyPatch) -> None:
    scripted(monkeypatch, [{"text": "", "blocked": "captcha"}])
    result, _ = run_job(FakePage())
    assert result.state == "error" and "CAPTCHA" in result.message and "yourself" in result.fix


def test_run_one_reports_a_usage_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    scripted(monkeypatch, [{"blocked": "limit"}])
    result, _ = run_job(FakePage())
    assert result.state == "error" and "usage limit" in result.message


def test_run_one_explains_when_typing_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = scripted(monkeypatch, [{"text": ""}], typed="")
    result, _ = run_job(FakePage())
    assert result.state == "error" and "couldn't type" in result.message
    assert calls == []                                           # never sent anything


def test_cancel_marks_the_account_cancelled_and_presses_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = scripted(monkeypatch, [{"text": ""}, {"text": "Partial answer", "busy": True}])
    events: list[tuple[str, dict[str, Any]]] = []
    patient = RunSettings(timeout_seconds=30, stable_seconds=5, min_delay_seconds=0, poll_seconds=0.05, send_check_seconds=0)

    async def scenario() -> None:
        task = asyncio.create_task(
            run_all({1: FakePage()}, [Job(1, "p", MAPPING_A, "s")], patient, SendTracker(), lambda k, **d: events.append((k, d)))
        )
        await asyncio.sleep(0.4)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    result = next(d for k, d in events if k == "run_result")
    assert result["state"] == "cancelled" and result["text"] == "Partial answer"
    assert "stop pressed" in calls
    assert events[-1][0] == "run_finished" and events[-1][1]["cancelled"] is True


# ----- real browser: fake streaming chat sites -------------------------------------------------


async def open_chat(browser: Any, **params: object) -> Any:
    page = await browser.new_page(viewport={"width": 900, "height": 600})
    await page.goto(chat(**params))
    return page


def test_each_way_of_sending_works_and_the_answer_is_clean() -> None:
    async def scenario(browser: Any) -> list[AccountResult]:
        out = []
        for layout, mapping in (("a", MAPPING_A), ("b", MAPPING_TEXTAREA), ("c", MAPPING_TEXTAREA)):
            page = await open_chat(browser, layout=layout, chunks=4, interval=80, first=100)
            sink: dict[int, AccountResult] = {}
            out.append(await run_one(page, Job(1, f"Question for layout {layout}", mapping, "fake.example"), FAST,
                                     SendTracker(), lambda *_a, **_k: None, sink))
        return out

    a, b, c = run_with_browser(scenario)
    assert [r.send_method for r in (a, b, c)] == ["button", "enter", "ctrl_enter"]
    for r in (a, b, c):
        assert r.state == "done", r
        assert "w3 END-OF-ANSWER" in r.text and r.text.endswith("two")
        assert "Copy" not in r.text and "Copy" not in r.markdown, "the site's own Copy button must be stripped"
        assert "**w0 w1 w2 w3 END-OF-ANSWER**" in r.markdown and "- one\n- two" in r.markdown


def test_nothing_mapped_yet_still_reads_the_reply_not_the_pages_own_code() -> None:
    """Regression: with no saved mapping and an empty chat, the page's <script> text was captured as the answer."""

    async def scenario(browser: Any) -> AccountResult:
        page = await open_chat(browser, layout="a", chunks=4, interval=80, first=100)
        sink: dict[int, AccountResult] = {}
        return await run_one(page, Job(1, "Question with nothing mapped", Mapping("", "", ""), "fake.example"), FAST,
                             SendTracker(), lambda *_a, **_k: None, sink)

    r = run_with_browser(scenario)
    assert r.state == "done", r
    assert r.text.endswith("two") and "END-OF-ANSWER" in r.text
    assert "function" not in r.text and "setTimeout" not in r.text and "const " not in r.text
    assert "**w0 w1 w2 w3 END-OF-ANSWER**" in r.markdown


def test_a_site_that_clears_its_box_slowly_gets_the_prompt_only_once() -> None:
    """Regression: the box still held the prompt after 1.5 s, so FanOut tried the next key and sent it twice."""

    async def scenario(browser: Any) -> tuple[AccountResult, int]:
        page = await open_chat(browser, layout="b", chunks=6, interval=150, first=100, slow_clear=4000)
        sink: dict[int, AccountResult] = {}
        settings = RunSettings(stable_seconds=0.8, timeout_seconds=20, min_delay_seconds=0, poll_seconds=0.1, send_check_seconds=0.5)
        result = await run_one(page, Job(1, "Only once please", MAPPING_TEXTAREA, "fake.example"), settings,
                               SendTracker(), lambda *_a, **_k: None, sink)
        return result, int(await page.evaluate("window.submits"))

    result, submits = run_with_browser(scenario)
    assert result.state == "done", result
    assert submits == 1, f"the prompt was sent {submits} times"
    assert result.send_method == "enter"


def test_three_prompts_run_at_the_same_time_and_are_saved(tmp_path: Path) -> None:
    async def scenario(browser: Any) -> tuple[list[AccountResult], list[tuple[str, dict[str, Any]]]]:
        pages = {n: await open_chat(browser, layout="a", chunks=10, interval=200, first=200) for n in (1, 2, 3)}
        jobs = [Job(n, f"Prompt number {n} about topic {n}", MAPPING_A, f"site{n}.example") for n in (1, 2, 3)]
        events: list[tuple[str, dict[str, Any]]] = []
        results = await run_all(pages, jobs, FAST, SendTracker(), lambda k, **d: events.append((k, d)),
                                results_dir=tmp_path / "results", shots=tmp_path / "shots")
        return results, events

    results, events = run_with_browser(scenario)
    assert [r.state for r in results] == ["done"] * 3
    assert [f"topic {r.slot}" in r.prompt and f"number {r.slot}" in r.prompt for r in results] == [True] * 3
    latest_start, earliest_end = max(r.started_at for r in results), min(r.finished_at for r in results)
    assert latest_start < earliest_end, "the three runs must overlap in time"
    total = sum(r.elapsed for r in results)
    span = max(r.finished_at for r in results) - min(r.started_at for r in results)
    assert span < total * 0.6, f"ran one after another? span {span:.1f}s vs total {total:.1f}s"
    assert events[0][0] == "run_started" and events[-1][0] == "run_finished" and events[-1][1]["done"] == 3
    folder = Path(events[-1][1]["folder"])
    assert {p.name for p in folder.iterdir()} == {"account_1.md", "account_2.md", "account_3.md", "run.json"}
    starts = [round(r.started_at % 1000, 2) for r in results]
    ends = [round(r.finished_at % 1000, 2) for r in results]
    print(f"\nOVERLAP: starts {starts} ends {ends} span {span:.2f}s total {total:.2f}s")


def test_a_long_answer_with_a_long_pause_is_captured_complete() -> None:
    async def scenario(browser: Any) -> AccountResult:
        page = await open_chat(browser, layout="a", chunks=60, interval=60, first=100, pause_at=30, pause_ms=2500)
        sink: dict[int, AccountResult] = {}
        return await run_one(page, Job(1, "Write something long", MAPPING_A, "fake.example"), FAST, SendTracker(),
                             lambda *_a, **_k: None, sink)

    result = run_with_browser(scenario)
    assert result.state == "done"
    assert "w29" in result.text and "w30" in result.text, "the pause (longer than stable_seconds) must not cut it short"
    assert "w59 END-OF-ANSWER" in result.text and result.text.endswith("two")


def test_one_account_timing_out_does_not_break_the_others(tmp_path: Path) -> None:
    async def scenario(browser: Any) -> tuple[list[AccountResult], Path]:
        pages = {
            1: await open_chat(browser, layout="a", chunks=5, interval=100, first=100),
            2: await open_chat(browser, layout="a", stall=1),
            3: await open_chat(browser, layout="b", chunks=5, interval=100, first=100),
        }
        jobs = [Job(1, "first", MAPPING_A, "s1"), Job(2, "second", MAPPING_A, "s2"), Job(3, "third", MAPPING_TEXTAREA, "s3")]
        settings = RunSettings(stable_seconds=1.0, timeout_seconds=3, min_delay_seconds=0, poll_seconds=0.1, send_check_seconds=0.4)
        results = await run_all(pages, jobs, settings, SendTracker(), lambda *_a, **_k: None, shots=tmp_path / "shots")
        return results, tmp_path / "shots"

    results, shots = run_with_browser(scenario)
    assert [r.state for r in results] == ["done", "timeout", "done"]
    assert "END-OF-ANSWER" in results[0].text and "END-OF-ANSWER" in results[2].text
    assert "no finished answer" in results[1].message and results[1].fix
    assert any(p.suffix == ".png" for p in shots.iterdir()), "a screenshot of the stuck window is saved"


def test_captcha_pauses_only_that_account() -> None:
    async def scenario(browser: Any) -> list[AccountResult]:
        pages = {1: await open_chat(browser, layout="a", captcha=1), 2: await open_chat(browser, layout="a", chunks=3, first=100)}
        jobs = [Job(1, "one", MAPPING_A, "s1"), Job(2, "two", MAPPING_A, "s2")]
        return await run_all(pages, jobs, FAST, SendTracker(), lambda *_a, **_k: None)

    first, second = run_with_browser(scenario)
    assert first.state == "error" and "CAPTCHA" in first.message and "yourself" in first.fix
    assert second.state == "done"


def browser_processes() -> set[int]:
    """Running browser processes (a finished process nobody has collected yet is a zombie and doesn't count)."""
    found = set()
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            if proc.status() == psutil.STATUS_ZOMBIE:
                continue
            if "chrome" in (proc.info["name"] or "").lower() or any("chrome" in a.lower() for a in (proc.info["cmdline"] or [])[:1]):
                found.add(proc.pid)
        except psutil.Error:
            continue
    return found


def test_cancel_leaves_the_windows_alone_and_no_orphans_after_close() -> None:
    before = browser_processes()
    seen: dict[str, Any] = {}

    async def scenario(browser: Any) -> list[tuple[str, dict[str, Any]]]:
        pages = {n: await open_chat(browser, layout="a", stall=1) for n in (1, 2, 3)}
        jobs = [Job(n, f"prompt {n}", MAPPING_A, f"s{n}") for n in (1, 2, 3)]
        events: list[tuple[str, dict[str, Any]]] = []
        seen["running"] = browser_processes() - before
        task = asyncio.create_task(run_all(pages, jobs, FAST, SendTracker(), lambda k, **d: events.append((k, d))))
        await asyncio.sleep(1.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        seen["after_cancel"] = browser_processes() - before
        seen["still_open"] = [not p.is_closed() for p in pages.values()]
        return events

    events = run_with_browser(scenario)
    states = [d["state"] for k, d in events if k == "run_result"]
    assert sorted(states) == ["cancelled"] * 3
    assert events[-1][0] == "run_finished" and events[-1][1]["cancelled"] is True
    assert seen["still_open"] == [True] * 3, "cancel only stops waiting; the windows stay open"
    assert seen["after_cancel"] == seen["running"], "cancel must not start or leave extra browser processes"
    deadline = time.monotonic() + 5
    while browser_processes() - before and time.monotonic() < deadline:
        time.sleep(0.2)
    assert browser_processes() - before == set(), "after closing, no browser process is left"
    print(f"\nBROWSER PROCESSES: while open {len(seen['running'])}, after cancel {len(seen['after_cancel'])}, after close 0")


def test_minimum_delay_between_two_sends_from_the_same_account() -> None:
    async def scenario(browser: Any) -> tuple[float, list[dict[str, Any]]]:
        page = await open_chat(browser, layout="a", chunks=2, interval=50, first=50)
        tracker, events = SendTracker(), []
        settings = RunSettings(stable_seconds=0.5, timeout_seconds=20, min_delay_seconds=2.0, poll_seconds=0.1, send_check_seconds=0.3)
        await run_one(page, Job(1, "first", MAPPING_A, "s"), settings, tracker, lambda *_a, **_k: None, {})
        t = time.monotonic()
        await run_one(page, Job(1, "second", MAPPING_A, "s"), settings, tracker, lambda k, **d: events.append(d), {})
        return time.monotonic() - t, [e for e in events if e.get("state") == "idle"]

    second_run, waits = run_with_browser(scenario)
    assert second_run >= 2.0, f"second send came after only {second_run:.1f}s"
    assert waits and "Waiting" in waits[0]["detail"]


def test_session_manager_send_all_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FANOUT_HOME", str(tmp_path))
    events: list[tuple[str, dict[str, Any]]] = []

    async def scenario(browser: Any) -> None:
        html = (FIXTURES / "stream_chat.html").read_text(encoding="utf-8")
        manager = SessionManager(lambda k, **d: events.append((k, d)))
        for n in (1, 2):
            page = await browser.new_page()
            await page.route("http://chat.test/**", lambda route: route.fulfill(body=html, content_type="text/html"))
            await page.goto(f"http://chat.test/?layout=a&chunks=3&first=100&interval=80&n={n}")
            manager._contexts[n] = SimpleNamespace(pages=[page])  # type: ignore[assignment]
        jobs = [{"slot": n, "prompt": f"hello {n}", "mapping": MAPPING_A.to_dict(), "site": "chat.test"} for n in (1, 2, 3)]
        quick = {"stable_seconds": 1.0, "timeout_seconds": 20, "min_delay_seconds": 0, "poll_seconds": 0.1, "send_check_seconds": 0.3}
        await manager.send_all(jobs, quick)

    run_with_browser(scenario)
    results = {d["slot"]: d for k, d in events if k == "run_result"}
    assert results[1]["state"] == results[2]["state"] == "done"
    assert results[3]["state"] == "error" and "no open window" in results[3]["message"]
    assert events[-1][0] == "run_finished" and (tmp_path / "results").exists()


def test_history_trouble_never_blocks_a_run(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    history = PromptHistory(blocker / "history.json")  # its folder can't be made: every save fails
    history.add("Still works")
    assert [i["prompt"] for i in history.search()] == ["Still works"]
    damaged = tmp_path / "old.json"
    damaged.write_text('[{"prompt": "no time stamp"}, {"ts": 5}, "junk"]')
    loaded = PromptHistory(damaged).search()
    assert loaded == [{"ts": "", "prompt": "no time stamp"}]
