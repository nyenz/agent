"""Shared helpers for tests that need a real (headless) Chromium-based browser."""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from fanout.sessions import banner_script


def find_browser() -> str | None:
    """A browser to test with: FANOUT_TEST_BROWSER, else Chrome/Chromium/Brave on PATH."""
    explicit = os.environ.get("FANOUT_TEST_BROWSER")
    if explicit:
        return explicit
    for name in ("google-chrome", "chromium", "chromium-browser", "brave-browser", "brave"):
        found = shutil.which(name)
        if found:
            return found
    return None


def run_with_browser(scenario: Callable[[Any], Awaitable[Any]]) -> Any:
    """Run `scenario(browser)` against a fresh headless browser; skip the test if none can start."""
    from playwright.async_api import async_playwright

    async def main() -> Any:
        async with async_playwright() as pw:
            options: dict[str, Any] = {"headless": True}
            exe = find_browser()
            if exe:
                options["executable_path"] = exe
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                options["args"] = ["--no-sandbox"]  # test-only: some CI containers run as root
            try:
                browser = await pw.chromium.launch(**options)
            except Exception as exc:  # noqa: BLE001
                pytest.skip(f"no browser available for tests: {type(exc).__name__}")
            try:
                return await scenario(browser)
            finally:
                await browser.close()

    return asyncio.run(main())


def run_in_browser(scenario: Callable[[Any], Awaitable[Any]]) -> Any:
    """Run `scenario(page)` on one page that already has the account banner, like the real windows."""

    async def with_page(browser: Any) -> Any:
        page = await browser.new_page(viewport={"width": 900, "height": 600})
        await page.add_init_script(banner_script(1))
        return await scenario(page)

    return run_with_browser(with_page)
