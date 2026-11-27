"""FanOut installer and repair tool.

Uses only the Python standard library, so it can run before FanOut's own environment exists.

    python bootstrap.py install   build .venv, install pinned packages, add the browser, make a shortcut
    python bootstrap.py check     quick check on every launch; quietly repairs if something is missing
    python bootstrap.py repair    redo every step (safe to run any time; keeps your settings and results)
    python bootstrap.py update    after an update: refresh packages; fetch Chromium only if its version changed

Your settings, logins and results live in the per-user app folder, never in this folder, so none of
these commands can touch them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MIN_PYTHON = (3, 10)
STATE_NAME = "install_state.json"
STATE_VERSION = 1
STEPS = 7

TK_HELP = {
    "win32": "Tkinter comes with the official Python from python.org. Re-run INSTALL.bat, or reinstall Python and tick 'tcl/tk'.",
    "darwin": "Run: brew install python-tk@3.12   then double-click install.command again.",
    "linux": (
        "Run: sudo apt install python3-tk   (Fedora: sudo dnf install python3-tkinter, Arch: sudo pacman -S tk)"
        "   then run install.command again."
    ),
}


# ----- small pure helpers (these have tests) ------------------------------------------------------


def venv_python(root: Path = ROOT) -> Path:
    """Path of the Python inside the .venv folder for this operating system."""
    sub = Path("Scripts") / "python.exe" if sys.platform == "win32" else Path("bin") / "python"
    return root / ".venv" / sub


def read_requirements(path: Path) -> list[str]:
    """Package lines from a requirements file (comments, blanks and '-r' lines skipped)."""
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            lines.append(line)
    return lines


def split_requirement(line: str) -> tuple[str, str | None]:
    """'playwright==1.63.0' -> ('playwright', '1.63.0'); no pin -> (name, None)."""
    match = re.match(r"^([A-Za-z0-9_.\-]+)\s*(?:==\s*([^\s;]+))?", line.strip())
    if not match:
        return line.strip(), None
    return match.group(1).lower().replace("_", "-"), match.group(2)


def file_hash(path: Path) -> str:
    """SHA-256 of a file, as hex text."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_state(root: Path = ROOT) -> dict[str, object]:
    """install_state.json, or an empty dict if it is missing or unreadable."""
    try:
        data = json.loads((root / STATE_NAME).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: dict[str, object], root: Path = ROOT) -> None:
    """Write install_state.json through a temporary file so a crash can't leave half a file."""
    fd, tmp = tempfile.mkstemp(dir=root, prefix=".state-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=1)
    os.replace(tmp, root / STATE_NAME)


def desktop_entry(root: Path) -> str:
    """Text of a Linux .desktop launcher."""
    return (
        "[Desktop Entry]\nType=Application\nName=FanOut\n"
        "Comment=Send different prompts to several chat sites at once\n"
        f'Exec="{root / "run.command"}"\nPath={root}\nIcon={root / "fanout" / "assets" / "icon.png"}\n'
        "Terminal=false\nCategories=Utility;\n"
    )


def mac_launcher(root: Path) -> str:
    """Text of the double-clickable macOS launcher."""
    return f'#!/bin/bash\ncd "{root}" || exit 1\nexec "{root / "run.command"}"\n'


def windows_shortcut_script(root: Path) -> str:
    """PowerShell that puts FanOut.lnk (pointing at run.bat) on the real Desktop folder."""
    run_bat = str(root / "run.bat").replace("'", "''")
    work = str(root).replace("'", "''")
    return (
        "$desktop = [Environment]::GetFolderPath('Desktop'); "
        "$shell = New-Object -ComObject WScript.Shell; "
        "$link = $shell.CreateShortcut((Join-Path $desktop 'FanOut.lnk')); "
        f"$link.TargetPath = '{run_bat}'; $link.WorkingDirectory = '{work}'; "
        "$link.WindowStyle = 7; $link.Description = 'FanOut'; $link.Save(); "
        "Write-Output (Join-Path $desktop 'FanOut.lnk')"
    )


# ----- running things ------------------------------------------------------------------------------


def say(text: str) -> None:
    print(text, flush=True)


def step(n: int, text: str, quiet: bool = False) -> None:
    if not quiet:
        say(f"[{n}/{STEPS}] {text}")


def run(cmd: Sequence[str | Path], quiet: bool = False, env: dict[str, str] | None = None) -> int:
    """Run a command, showing its output unless quiet. Returns the exit code (never raises)."""
    full_env = dict(os.environ, **(env or {}))
    try:
        proc = subprocess.run(
            [str(c) for c in cmd], cwd=ROOT, env=full_env, check=False,
            stdout=subprocess.DEVNULL if quiet else None, stderr=subprocess.STDOUT if quiet else None,
        )
    except OSError as exc:
        if not quiet:
            say(f"  Could not start {cmd[0]}: {exc}")
        return 127
    return proc.returncode


def venv_works(root: Path = ROOT) -> bool:
    """True if the .venv Python starts."""
    py = venv_python(root)
    return py.exists() and run([py, "-c", "import sys"], quiet=True) == 0


def package_problems(py: Path, requirements: Sequence[str]) -> list[str]:
    """Names of pinned packages that are missing or at a different version inside that Python."""
    names = [split_requirement(r) for r in requirements]
    code = (
        "import importlib.metadata as m, json, sys\n"
        "out = {}\n"
        "for n in json.loads(sys.argv[1]):\n"
        "    try: out[n] = m.version(n)\n"
        "    except m.PackageNotFoundError: out[n] = None\n"
        "print(json.dumps(out))"
    )
    try:
        done = subprocess.run(
            [str(py), "-c", code, json.dumps([n for n, _ in names])],
            capture_output=True, text=True, timeout=60, check=False,
        )
        found = json.loads(done.stdout.strip().splitlines()[-1])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return [n for n, _ in names]
    return [n for n, want in names if found.get(n) is None or (want and found[n] != want)]


def tk_works(py: Path) -> bool:
    """True if that Python can load Tkinter."""
    return run([py, "-c", "import tkinter"], quiet=True) == 0


def chromium_path(py: Path) -> Path | None:
    """Where Playwright keeps its own Chromium, or None if it isn't installed."""
    code = "from playwright.sync_api import sync_playwright as s\nwith s() as p: print(p.chromium.executable_path)"
    try:
        done = subprocess.run([str(py), "-c", code], capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = done.stdout.strip().splitlines()
    path = Path(lines[-1]) if lines else None
    return path if path and path.exists() else None


def create_shortcut(root: Path = ROOT) -> Path | None:
    """Put a FanOut launcher on the Desktop. Returns where it went, or None if that wasn't possible."""
    try:
        if sys.platform == "win32":
            done = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", windows_shortcut_script(root)],
                capture_output=True, text=True, timeout=60, check=False,
            )
            out = done.stdout.strip()
            return Path(out) if done.returncode == 0 and out else None
        desktop = Path.home() / "Desktop"
        if sys.platform == "darwin":
            target = desktop / "FanOut.command"
            body = mac_launcher(root)
        else:
            target = desktop / "FanOut.desktop"
            body = desktop_entry(root)
            apps = Path.home() / ".local" / "share" / "applications"
            apps.mkdir(parents=True, exist_ok=True)
            (apps / "FanOut.desktop").write_text(body, encoding="utf-8")
        if not desktop.is_dir():
            return None
        target.write_text(body, encoding="utf-8")
        target.chmod(0o755)
        return target
    except (OSError, subprocess.SubprocessError):
        return None


def show_dialog(py: Path, title: str, text: str) -> None:
    """Final Tk message box. Skipped quietly when there is no screen or no Tkinter."""
    code = (
        "import sys, tkinter as tk\nfrom tkinter import messagebox\n"
        "r = tk.Tk(); r.withdraw(); messagebox.showinfo(sys.argv[1], sys.argv[2]); r.destroy()"
    )
    run([py, "-c", code, title, text], quiet=True)


# ----- the steps -----------------------------------------------------------------------------------


def make_venv(root: Path = ROOT, quiet: bool = False) -> bool:
    """Create .venv, rebuilding it first if it exists but no longer starts."""
    if venv_works(root):
        return True
    venv = root / ".venv"
    if venv.exists():
        say("  The old .venv folder is broken; rebuilding it.") if not quiet else None
        shutil.rmtree(venv, ignore_errors=True)
    cmd: list[str | Path] = [sys.executable, "-m", "venv", venv]
    if os.environ.get("FANOUT_VENV_SYSTEM_SITE"):  # used by tests that have no internet
        cmd.append("--system-site-packages")
    return run(cmd, quiet=quiet) == 0 and venv_works(root)


def install_all(
    root: Path = ROOT, *, browser: bool = True, shortcut: bool = True, quiet: bool = False,
    requirements_file: Path | None = None, check_tk: bool = True, force_browser: bool = True,
) -> tuple[bool, str]:
    """Do every install step. Returns (ok, plain-English message). Safe to repeat.

    force_browser=False fetches Chromium only when the Playwright version changed or it was never fetched.
    check_tk=False skips the Tkinter test (an app that is already open obviously has it).
    """
    req_path = requirements_file or root / "requirements.txt"
    state = load_state(root)

    step(1, f"Checking Python {sys.version_info.major}.{sys.version_info.minor}", quiet)
    if sys.version_info < MIN_PYTHON:
        return False, f"FanOut needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer, this is {sys.version.split()[0]}."

    step(2, "Creating FanOut's private Python folder (.venv)", quiet)
    if not make_venv(root, quiet):
        return False, "I couldn't create the .venv folder. Check there is free disk space and try again."
    py = venv_python(root)

    step(3, "Updating pip (a failure here is fine)", quiet)
    run([py, "-m", "pip", "install", "--disable-pip-version-check", "--retries", "1", "--timeout", "15", "--upgrade", "pip"], quiet=quiet)

    step(4, "Installing FanOut's packages", quiet)
    code = run([py, "-m", "pip", "install", "--disable-pip-version-check", "-r", req_path], quiet=quiet)
    if code != 0:
        return False, "I couldn't install FanOut's packages. Check your internet connection, then run INSTALL again."
    still = package_problems(py, read_requirements(req_path))
    if still:
        return False, f"These packages still look wrong after installing: {', '.join(still)}."

    step(5, "Checking that Tkinter (the window toolkit) works", quiet)
    if check_tk and not tk_works(py):
        return False, "Tkinter is missing, so the window can't open. " + TK_HELP.get(sys.platform, TK_HELP["linux"])

    step(6, "Adding FanOut's built-in Chromium browser (a few minutes the first time)", quiet)
    pins = dict(split_requirement(r) for r in read_requirements(req_path))
    fetch = browser and (force_browser or state.get("playwright") != pins.get("playwright") or not state.get("browser_installed"))
    if fetch:
        if sys.platform.startswith("linux"):
            sudo = [] if hasattr(os, "geteuid") and os.geteuid() == 0 else ["sudo"]
            if sudo and not shutil.which("sudo"):
                say("  Skipping system libraries (no sudo). If windows fail to open, install them by hand.")
            else:
                run([*sudo, py, "-m", "playwright", "install-deps", "chromium"], quiet=quiet)
        if run([py, "-m", "playwright", "install", "chromium"], quiet=quiet) != 0:
            return False, "I couldn't download the built-in Chromium. Check your internet connection and run INSTALL again."
    else:
        step(6, "(Chromium is already up to date)", quiet)

    step(7, "Making a Desktop shortcut and saving install notes", quiet)
    where = create_shortcut(root) if shortcut else None
    state.update({
        "state_version": STATE_VERSION, "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "python": sys.version.split()[0], "requirements_hash": file_hash(req_path),
        "playwright": pins.get("playwright"), "browser_installed": bool(browser) and (fetch or bool(state.get("browser_installed"))),
        "shortcut": str(where) if where else None,
    })
    save_state(state, root)
    extra = f" A shortcut is on your Desktop ({where.name})." if where else " (No Desktop shortcut was made; use run.bat / run.command.)"
    return True, "FanOut is installed." + extra


def needs_repair(root: Path = ROOT) -> str:
    """Empty text if everything looks fine, else the reason a repair is needed."""
    req = root / "requirements.txt"
    if not venv_works(root):
        return "the private Python folder is missing or broken"
    state = load_state(root)
    if not state:
        return "no install notes were found"
    if req.exists() and state.get("requirements_hash") != file_hash(req):
        return "the package list changed"
    bad = package_problems(venv_python(root), read_requirements(req)) if req.exists() else []
    if bad:
        return f"packages are missing or wrong: {', '.join(bad)}"
    return ""


def check(root: Path = ROOT) -> int:
    """Run on every launch. Silent when fine; one short line and a quiet repair when not."""
    reason = needs_repair(root)
    if not reason:
        return 0
    say(f"Fixing FanOut's setup ({reason}). One moment...")
    ok, message = install_all(root, browser=bool(load_state(root).get("browser_installed", True)),
                              shortcut=False, quiet=True, check_tk=False, force_browser=False)
    if not ok:
        say(message)
        return 1
    say("Done.")
    return 0


# ----- command line --------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bootstrap.py", description="FanOut installer and repair tool")
    parser.add_argument("command", choices=["install", "check", "repair", "update"])
    parser.add_argument("--no-launch", action="store_true", help="don't start the app afterwards")
    parser.add_argument("--no-dialog", action="store_true", help="don't show the final message box")
    parser.add_argument("--no-browser", action="store_true", help="skip downloading the built-in Chromium")
    parser.add_argument("--no-shortcut", action="store_true", help="don't make a Desktop shortcut")
    args = parser.parse_args(argv)

    if args.command == "check":
        return check()

    if args.command == "update":
        ok, message = install_all(shortcut=False, check_tk=False, force_browser=False,
                                  browser=bool(load_state().get("browser_installed", True)))
        say(message)
        return 0 if ok else 1

    ok, message = install_all(browser=not args.no_browser, shortcut=not args.no_shortcut)
    say("")
    say(message)
    py = venv_python()
    if not args.no_dialog and py.exists():
        show_dialog(py, "FanOut installer", message if ok else message + "\n\nDetails are in the black window.")
    if ok and not args.no_launch and args.command == "install":
        say("Starting FanOut...")
        return run([py, ROOT / "app.py"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
