"""Mapping: stored selectors, selector generation, auto-detect, dry run and the visual picker.

The browser tests run a real Chromium-based browser (headless) against two small fake chat sites in
tests/fixtures. They are skipped when no browser can be started.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from browserkit import run_in_browser

from fanout.config import Config, clean_mappings, load_config, save_config
from fanout.mapping import Mapping, build_script, get_mapping, load_js, map_account, pick, store_mapping
from fanout.sessions import SessionManager

FIXTURES = Path(__file__).parent / "fixtures"
SITE_A = (FIXTURES / "chat_site_a.html").as_uri()
SITE_B = (FIXTURES / "chat_site_b.html").as_uri()


async def with_selector_lib(page: Any, body: str) -> Any:
    """Evaluate `body` (a function body) with the FanOutSel helpers in scope."""
    return await page.evaluate("(() => {\n" + load_js("selector") + "\n" + body + "\n})()")


# ----- stored mappings (no browser needed) -----------------------------------------------------


def test_mapping_round_trip_and_defaults() -> None:
    mapping = Mapping.from_dict({"input_selector": "#a", "send_selector": 5, "junk": "x"})
    assert mapping == Mapping(input_selector="#a")
    assert Mapping.from_dict(None) == Mapping()
    assert Mapping("#a", "#b", "#c").to_dict() == {"input_selector": "#a", "send_selector": "#b", "message_selector": "#c"}


def test_store_and_get_per_account_and_site() -> None:
    mappings: dict[str, dict[str, dict[str, str]]] = {}
    store_mapping(mappings, 2, "https://chat.example", Mapping("#box", "#go", ".msg"))
    store_mapping(mappings, 2, "https://other.example", Mapping("textarea"))
    assert get_mapping(mappings, 2, "https://chat.example") == Mapping("#box", "#go", ".msg")
    assert get_mapping(mappings, 2, "https://other.example") == Mapping("textarea")
    assert get_mapping(mappings, 1, "https://chat.example") is None
    assert get_mapping(mappings, 2, "https://nope.example") is None


def test_clean_mappings_drops_garbage() -> None:
    raw = {
        "1": {"https://a.example": {"input_selector": "#x", "evil": "y", "send_selector": 7}, "ftp://b": {"input_selector": "#z"}},
        "2": "not a dict",
        "3": {"https://c.example": {}},
    }
    assert clean_mappings(raw) == {"1": {"https://a.example": {"input_selector": "#x"}}}
    assert clean_mappings("nonsense") == {}


def test_mappings_survive_restart(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    cfg = Config()
    store_mapping(cfg.mappings, 1, "https://chat.example", Mapping("#box", "#go", ".msg"))
    save_config(cfg, path)
    again = load_config(path)  # a new "launch"
    assert get_mapping(again.mappings, 1, "https://chat.example") == Mapping("#box", "#go", ".msg")


# ----- selector generation (real browser) ------------------------------------------------------


def test_stable_id_rules() -> None:
    async def scenario(page: Any) -> Any:
        await page.goto(SITE_A)
        ids = ["chat-input", "prompt-textarea", ":r1a:", ":rk:", "ember123", "input-9482711", "a b", ""]
        return await with_selector_lib(page, f"return {ids!r}.map((i) => FanOutSel.stableId(i));".replace("'", '"'))

    assert run_in_browser(scenario) == [True, True, False, False, False, False, False, False]


def test_build_prefers_id_then_attribute_then_path() -> None:
    async def scenario(page: Any) -> Any:
        await page.set_content(
            """
            <div id="wrap">
              <input id="good-id">
              <input id=":r9:" data-testid="by-testid">
              <input id="ember4567" aria-label='say "hi"'>
              <input placeholder="same"><input placeholder="same">
              <span></span><p>one</p><p>two</p><p id="bad-12345">three</p>
            </div>"""
        )
        return await with_selector_lib(
            page,
            """
            const all = Array.from(document.querySelectorAll("input, p"));
            return all.map((el) => {
              const sel = FanOutSel.build(el);
              return [sel, FanOutSel.isUnique(sel, el)];
            });""",
        )

    got = run_in_browser(scenario)
    selectors = [sel for sel, _ in got]
    assert selectors[0] == "#good-id"
    assert selectors[1] == '[data-testid="by-testid"]'
    assert selectors[2] == '[aria-label="say \\"hi\\""]'
    assert all(unique for _, unique in got), got  # every selector matches exactly its element
    assert "nth-of-type" in selectors[3] and "nth-of-type" in selectors[4]  # duplicates fall back to position
    assert not any("ember" in s or ":r9:" in s or "12345" in s for s in selectors)


# ----- auto-detect + dry run (real browser) ----------------------------------------------------


def test_map_site_a_contenteditable_and_late_send_button() -> None:
    async def scenario(page: Any) -> Any:
        await page.goto(SITE_A)
        result = await map_account(page, 1)
        left = await page.evaluate("document.getElementById('prompt-textarea').innerText.trim()")
        sent = await page.evaluate("window.sent")
        return result, left, sent

    result, left, sent = run_in_browser(scenario)
    assert result.ok, result
    assert result.message == "Account 1 mapped"
    assert result.mapping == Mapping("#prompt-textarea", '[data-testid="send-button"]', "[data-message-author-role]")
    assert left == "", "the test text must be cleared"
    assert sent == 0, "the dry run must never click send"


def test_map_site_b_textarea_with_react_id() -> None:
    async def scenario(page: Any) -> Any:
        await page.goto(SITE_B)
        result = await map_account(page, 2)
        left = await page.evaluate("document.querySelector('textarea').value")
        sent = await page.evaluate("window.sent")
        return result, left, sent

    result, left, sent = run_in_browser(scenario)
    assert result.ok, result
    assert result.message == "Account 2 mapped"
    assert result.mapping.input_selector == '[placeholder="Ask anything"]'  # ":r1a:" is rejected as unstable
    assert result.mapping.send_selector
    assert result.mapping.message_selector == ""
    assert any("reply area" in note for note in result.notes)
    assert left == "" and sent == 0


def test_map_failure_messages_are_plain_english() -> None:
    async def scenario(page: Any) -> Any:
        await page.set_content("<p>Please log in</p>")
        nothing = await map_account(page, 3)
        await page.goto(SITE_B)
        wrong = await map_account(page, 3, Mapping(input_selector="#does-not-exist"))
        return nothing, wrong

    nothing, wrong = run_in_browser(scenario)
    assert not nothing.ok and "couldn't find the chat box" in nothing.message and nothing.fix
    assert not wrong.ok and "isn't on this page" in wrong.message and wrong.fix
    for result in (nothing, wrong):
        assert "Traceback" not in result.message + result.fix


# ----- visual picker (real browser, real mouse and keyboard) -----------------------------------


async def start_pick(page: Any, kind: str) -> asyncio.Task[Any]:
    task = asyncio.create_task(pick(page, kind, timeout=20, poll=0.05))
    await page.wait_for_selector("#__fanout_pick_bar__")
    return task


async def hover(page: Any, selector: str) -> tuple[float, float]:
    box = await page.locator(selector).first.bounding_box()
    assert box is not None
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    await page.mouse.move(x, y)
    return x, y


def test_picker_site_a_shows_overlay_and_swallows_the_click() -> None:
    async def scenario(page: Any) -> Any:
        await page.goto(SITE_A)
        task = await start_pick(page, "input")
        banner = await page.text_content("#__fanout_pick_bar__")
        x, y = await hover(page, "#prompt-textarea")
        await page.wait_for_function("document.getElementById('__fanout_pick_tip__').textContent !== ''")
        tip = await page.text_content("#__fanout_pick_tip__")
        outline = await page.evaluate("getComputedStyle(document.getElementById('__fanout_pick_box__')).borderTopColor")
        await page.mouse.click(x, y)
        result = await task
        return banner, tip, outline, result, await page.evaluate("window.clicks"), await page.query_selector("#__fanout_pick_bar__")

    banner, tip, outline, result, clicks, bar_left = run_in_browser(scenario)
    assert banner == "PICK MODE - click the element, ESC to cancel"
    assert tip == "#prompt-textarea"
    assert outline == "rgb(224, 49, 49)"  # the red box
    assert result.ok and result.selector == "#prompt-textarea"
    assert clicks == 0, "the page must not see the picking click"
    assert bar_left is None, "pick mode must clean up after itself"


def test_picker_site_b_ignores_unstable_id_and_finds_send_button() -> None:
    async def scenario(page: Any) -> Any:
        await page.goto(SITE_B)
        task = await start_pick(page, "input")
        x, y = await hover(page, "textarea")
        await page.mouse.click(x, y)
        box = await task
        task = await start_pick(page, "send")
        x, y = await hover(page, "button")
        await page.mouse.click(x, y)
        send = await task
        text = await page.evaluate("(sel) => document.querySelector(sel).textContent", send.selector)
        return box, send, text, await page.evaluate("window.sent")

    box, send, text, sent = run_in_browser(scenario)
    assert box.ok and box.selector == '[placeholder="Ask anything"]'
    assert send.ok and text == "Send"
    assert sent == 0, "picking the send button must not press it"


def test_picker_message_kind_generalises_to_all_messages() -> None:
    async def scenario(page: Any) -> Any:
        await page.goto(SITE_A)
        task = await start_pick(page, "message")
        x, y = await hover(page, '[data-message-author-role="assistant"]')
        await page.mouse.click(x, y)
        return await task

    result = run_in_browser(scenario)
    assert result.ok and result.selector == "[data-message-author-role]"


def test_picker_escape_cancels_and_cleans_up() -> None:
    async def scenario(page: Any) -> Any:
        await page.goto(SITE_A)
        task = await start_pick(page, "input")
        await page.keyboard.press("Escape")
        return await task, await page.query_selector("#__fanout_pick_box__")

    result, leftover = run_in_browser(scenario)
    assert result.cancelled and not result.ok and result.selector == ""
    assert leftover is None


def test_picker_rearms_after_the_page_navigates() -> None:
    async def scenario(page: Any) -> Any:
        await page.goto(SITE_A)
        task = await start_pick(page, "input")
        await page.goto(SITE_B)  # the site moves to another page while picking
        await page.wait_for_selector("#__fanout_pick_bar__")
        x, y = await hover(page, "textarea")
        await page.mouse.click(x, y)
        return await task

    result = run_in_browser(scenario)
    assert result.ok and result.selector == '[placeholder="Ask anything"]'


def test_picker_times_out_cleanly() -> None:
    async def scenario(page: Any) -> Any:
        await page.goto(SITE_A)
        result = await pick(page, "input", timeout=0.6, poll=0.05)
        return result, await page.query_selector("#__fanout_pick_bar__")

    result, leftover = run_in_browser(scenario)
    assert result.cancelled and leftover is None


def test_scripts_are_plain_text_files_next_to_the_code() -> None:
    for name in ("selector", "detect", "picker"):
        assert load_js(name).strip()
    assert "FanOutSel" in build_script("detect")


# ----- through the session manager (the way the worker calls it) -------------------------------


def test_session_manager_map_and_pick_events() -> None:
    events: list[tuple[str, dict[str, Any]]] = []

    def emit(kind: str, **data: Any) -> None:  # same shape as Worker.emit, so a field called "kind" would clash
        events.append((kind, data))

    async def scenario(page: Any) -> None:
        html = (FIXTURES / "chat_site_a.html").read_text(encoding="utf-8")
        await page.route("http://chat.test/**", lambda route: route.fulfill(body=html, content_type="text/html"))
        await page.goto("http://chat.test/")
        manager = SessionManager(emit)
        manager._contexts[1] = SimpleNamespace(pages=[page])  # type: ignore[assignment]
        await manager.map_account(2)  # no window for account 2
        await manager.map_account(1)
        task = asyncio.create_task(manager.pick_element(1, "input"))
        await page.wait_for_selector("#__fanout_pick_bar__")
        x, y = await hover(page, "#prompt-textarea")
        await page.mouse.click(x, y)
        await task

    run_in_browser(scenario)
    results = [data for kind, data in events if kind == "map_result"]
    assert [r["slot"] for r in results] == [2, 1]
    assert not results[0]["ok"] and "no chat page" in results[0]["message"]
    assert results[1]["ok"] and results[1]["origin"] == "http://chat.test"
    assert results[1]["mapping"]["input_selector"] == "#prompt-textarea"
    picks = [data for kind, data in events if kind == "pick_result"]
    assert picks == [{"slot": 1, "pick_kind": "input", "ok": True, "selector": "#prompt-textarea", "cancelled": False}]
