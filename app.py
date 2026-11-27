"""FanOut entry point.

    python app.py                  open the app
    python app.py --list-profiles  print the Brave profiles found on this computer
    python app.py --smoke-test     open the app, wait for the first screen, then exit
    python app.py --self-test      check the built-in Chromium can start (no window), then exit
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading

from fanout import __version__
from fanout.brave import profiles_as_text, scan
from fanout.config import load_config, save_config
from fanout.errors import describe
from fanout.logging_setup import setup_logging

log = logging.getLogger("fanout")


def list_profiles() -> int:
    """Print the profile table and the Brave program path (or that Brave is missing)."""
    result = scan()
    print(f"Brave program: {result.exe if result.exe else 'NOT FOUND'}")
    print(f"Brave data folders: {', '.join(str(r) for r in result.roots) or 'none found'}")
    print()
    print(profiles_as_text(result.profiles) if result.profiles else "No Brave profiles found.")
    return 0


def self_test() -> int:
    """Start the built-in Chromium without a window, open a blank page, close it. 0 = works.

    The packaged app has no console, so the outcome also goes to fanout.log.
    """
    import asyncio

    async def check() -> str:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content("<p id=ok>ok</p>")
                return await page.inner_text("#ok")
            finally:
                await browser.close()

    try:
        ok = asyncio.run(check()) == "ok"
    except Exception as exc:  # plain words on screen, the traceback only in the log
        log.exception("Self-test failed")
        print("SELF-TEST FAILED: " + describe(exc).text())
        return 1
    log.info("Self-test passed: the built-in Chromium starts and shows a page")
    print("SELF-TEST OK: the built-in Chromium starts and shows a page.")
    return 0 if ok else 1


def run_gui(smoke: bool) -> int:
    """Open the window. In smoke mode, exit 0 once the first real screen has appeared."""
    cfg = load_config()
    try:
        import tkinter as tk

        from fanout.ui import FanOutApp
    except ImportError:
        log.exception("Tkinter is not available")
        print(
            "FanOut can't open its window because Tkinter (the window toolkit) is missing. "
            "Double-click INSTALL.bat (Windows) or install.command (Mac/Linux) to fix it."
        )
        return 1
    try:
        root = tk.Tk()
    except tk.TclError:
        log.exception("Could not open a window")
        print(
            "FanOut couldn't open a window. If you're on Linux, make sure a desktop session is running "
            "and Tkinter is installed. Details are in fanout.log (see the README for where it is)."
        )
        return 1
    outcome = {"code": 1}

    def on_screen(name: str, detail: str) -> None:
        if smoke:
            print(f"SMOKE: first screen = {name} ({detail})", flush=True)
            outcome["code"] = 0 if name in ("scan", "brave-missing") else 1
            root.after(300, app.close)

    app = FanOutApp(root, cfg, save=save_config, on_screen=on_screen)
    if smoke:
        root.after(15000, app.close)
    root.mainloop()
    return outcome["code"] if smoke else 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch."""
    parser = argparse.ArgumentParser(prog="FanOut", description="Send different prompts to several chat sites at once.")
    parser.add_argument("--version", action="version", version=f"FanOut {__version__}")
    parser.add_argument("--list-profiles", action="store_true", help="print Brave profiles and exit")
    parser.add_argument("--smoke-test", action="store_true", help="open the app, check the first screen, exit")
    parser.add_argument("--self-test", action="store_true", help="check the built-in Chromium starts, exit")
    args = parser.parse_args(argv)
    setup_logging()
    if getattr(sys, "frozen", False):
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")  # the packaged app carries its own Chromium
    threading.excepthook = lambda args: log.error("Unhandled error in a helper thread", exc_info=args.exc_value)
    try:
        if args.list_profiles:
            return list_profiles()
        if args.self_test:
            return self_test()
        return run_gui(smoke=args.smoke_test)
    except Exception as exc:
        print(describe(exc).text())
        return 1


if __name__ == "__main__":
    sys.exit(main())
