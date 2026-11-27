"""Pieces shared by the three PyInstaller specs. Builds a single FOLDER (not one big file), so it starts fast.

PLAYWRIGHT_BROWSERS_PATH=0 must be set when `playwright install chromium` runs (the release workflow does this),
which puts Chromium inside the playwright package. This file then carries it into the build.
"""

from __future__ import annotations

from pathlib import Path

from PyInstaller.utils.hooks import collect_all


def pieces(root: Path) -> tuple[list, list, list]:
    """(datas, binaries, hiddenimports) for Analysis."""
    datas = [(str(root / "fanout" / "js"), "fanout/js"), (str(root / "fanout" / "assets"), "fanout/assets")]
    binaries: list = []
    hidden = ["psutil", "pyperclip", "tkinter"]
    pw_datas, pw_bins, pw_hidden = collect_all("playwright")
    datas += pw_datas
    binaries += pw_bins
    hidden += pw_hidden
    import playwright

    local = Path(playwright.__file__).parent / "driver" / "package" / ".local-browsers"
    if local.exists():  # hidden folder: make sure it is carried even if collect_all skipped it
        datas.append((str(local), "playwright/driver/package/.local-browsers"))
    return datas, binaries, hidden
