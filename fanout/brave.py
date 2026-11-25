"""Find Brave and list its profiles. Read-only and best-effort: it never writes to Brave's data."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CHANNELS = ("Brave-Browser", "Brave-Browser-Beta", "Brave-Browser-Nightly")
DOWNLOAD_URL = "https://brave.com/download/"


@dataclass(frozen=True)
class BraveProfile:
    """One Brave profile folder as the Scan screen shows it."""

    folder: str
    name: str
    path: Path
    root: Path
    channel: str
    last_used: datetime | None
    appears_running: bool
    login_saved: bool


@dataclass
class ScanResult:
    """Everything the Scan screen needs."""

    roots: list[Path] = field(default_factory=list)
    profiles: list[BraveProfile] = field(default_factory=list)
    exe: Path | None = None


def candidate_roots(platform: str, env: Mapping[str, str], home: Path) -> list[Path]:
    """Return every place Brave could keep its user data on this OS (they may not exist)."""
    roots: list[Path] = []
    for channel in CHANNELS:
        if platform == "win32":
            local = Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local")
            roots.append(local / "BraveSoftware" / channel / "User Data")
        elif platform == "darwin":
            roots.append(home / "Library" / "Application Support" / "BraveSoftware" / channel)
        else:
            roots.append(home / ".config" / "BraveSoftware" / channel)
            roots.append(home / ".var" / "app" / "com.brave.Browser" / "config" / "BraveSoftware" / channel)
            roots.append(home / "snap" / "brave" / "current" / ".config" / "BraveSoftware" / channel)
    return roots


def find_roots(platform: str, env: Mapping[str, str], home: Path) -> list[Path]:
    """Return the candidate roots that actually exist."""
    return [r for r in candidate_roots(platform, env, home) if r.is_dir()]


def find_brave_exe(
    platform: str,
    env: Mapping[str, str],
    home: Path,
    which: Callable[[str], str | None] = shutil.which,
) -> Path | None:
    """Locate the Brave program: standard install paths first, then PATH."""
    paths: list[Path] = []
    if platform == "win32":
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = env.get(var)
            if base:
                paths.append(Path(base) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe")
    elif platform == "darwin":
        paths += [
            Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
            home / "Applications" / "Brave Browser.app" / "Contents" / "MacOS" / "Brave Browser",
        ]
    else:
        paths += [
            Path("/usr/bin/brave-browser"),
            Path("/usr/bin/brave"),
            Path("/opt/brave.com/brave/brave-browser"),
            Path("/snap/bin/brave"),
            Path("/var/lib/flatpak/exports/bin/com.brave.Browser"),
            home / ".local" / "share" / "flatpak" / "exports" / "bin" / "com.brave.Browser",
        ]
    for path in paths:
        if path.is_file():
            return path
    for name in ("brave-browser", "brave", "brave-browser-stable", "brave.exe"):
        found = which(name)
        if found:
            return Path(found)
    return None


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object, returning {} when the file is missing or damaged."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _dig(data: Any, *keys: str) -> Any:
    """Walk nested dicts, returning None as soon as a step is missing."""
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def profile_folders(root: Path) -> list[str]:
    """Return 'Default' then 'Profile N' folders in natural order."""
    found = [p.name for p in root.iterdir() if p.is_dir() and (p.name == "Default" or p.name.startswith("Profile "))]

    def order(name: str) -> tuple[int, int]:
        if name == "Default":
            return (0, 0)
        tail = name.split(" ", 1)[1]
        return (1, int(tail)) if tail.isdigit() else (2, 0)

    return sorted(found, key=order)


def profile_name(root: Path, folder: str, local_state: dict[str, Any]) -> str:
    """Name from Local State info_cache, then the profile's Preferences, then the folder name."""
    info = _dig(local_state, "profile", "info_cache", folder)
    for key in ("name", "gaia_name", "user_name"):
        value = _dig(info, key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    value = _dig(_read_json(root / folder / "Preferences"), "profile", "name")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return folder


def profile_last_used(root: Path, folder: str, local_state: dict[str, Any]) -> datetime | None:
    """Per-profile 'active_time' from Local State, else the folder's modified time.

    Note: Local State's profile.last_used only names the most recent folder (no time), so it is
    used for the running guess below instead.
    """
    stamp = _dig(local_state, "profile", "info_cache", folder, "active_time")
    if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
        try:
            return datetime.fromtimestamp(stamp)
        except (OverflowError, OSError, ValueError):
            pass
    try:
        return datetime.fromtimestamp((root / folder).stat().st_mtime)
    except OSError:
        return None


def login_probably_saved(root: Path, folder: str) -> bool:
    """True if the profile has a non-empty cookie database. A hint only; logins may be expired."""
    for candidate in (root / folder / "Network" / "Cookies", root / folder / "Cookies"):
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return True
        except OSError:
            continue
    return False


def running_brave_cmdlines() -> list[list[str]]:
    """Command lines of main Brave browser processes (not helper processes). Needs psutil."""
    try:
        import psutil
    except ImportError:
        return []
    result: list[list[str]] = []
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            cmd = proc.info.get("cmdline") or []
        except (psutil.Error, OSError):
            continue
        if "brave" in name and cmd and not any(part.startswith("--type=") for part in cmd):
            result.append(list(cmd))
    return result


def _flag(cmd: list[str], name: str) -> str | None:
    """Value of a --name=value argument, if present."""
    prefix = f"--{name}="
    for part in cmd:
        if part.startswith(prefix):
            return part[len(prefix):]
    return None


def lock_looks_live(root: Path) -> bool:
    """Check Brave's lock files. A Linux/macOS lock naming a dead process is treated as stale."""
    for name in ("SingletonLock", "SingletonSocket", "lockfile"):
        lock = root / name
        if not os.path.lexists(lock):
            continue
        if name == "SingletonLock" and lock.is_symlink():
            tail = os.readlink(lock).rsplit("-", 1)[-1]
            if tail.isdigit():
                try:
                    import psutil

                    return psutil.pid_exists(int(tail))
                except ImportError:
                    return True
        return True
    return False


def running_folders(root: Path, cmdlines: list[list[str]], local_state: dict[str, Any]) -> set[str]:
    """Guess which profile folders under root are in use. Never certain."""
    last = _dig(local_state, "profile", "last_used")
    fallback = last if isinstance(last, str) and last else "Default"
    active: set[str] = set()
    for cmd in cmdlines:
        data_dir = _flag(cmd, "user-data-dir")
        if data_dir is not None and Path(data_dir) != root:
            continue
        active.add(_flag(cmd, "profile-directory") or fallback)
    if not active and lock_looks_live(root):
        active.add(fallback)
    return active


def _channel_label(root: Path) -> str:
    """Short label such as 'Brave', 'Brave Beta' or 'Brave Nightly' for a root folder."""
    text = str(root)
    if "Brave-Browser-Beta" in text:
        return "Brave Beta"
    if "Brave-Browser-Nightly" in text:
        return "Brave Nightly"
    return "Brave"


def scan(
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    cmdlines: list[list[str]] | None = None,
) -> ScanResult:
    """Find Brave and every profile it has. Arguments exist so tests can fake the machine."""
    platform = platform or sys.platform
    env = env if env is not None else os.environ
    home = home or Path.home()
    if cmdlines is None:
        cmdlines = running_brave_cmdlines()
    result = ScanResult(exe=find_brave_exe(platform, env, home))
    for root in find_roots(platform, env, home):
        result.roots.append(root)
        local_state = _read_json(root / "Local State")
        running = running_folders(root, cmdlines, local_state)
        try:
            folders = profile_folders(root)
        except OSError:
            log.warning("Could not list %s", root, exc_info=True)
            continue
        for folder in folders:
            result.profiles.append(
                BraveProfile(
                    folder=folder,
                    name=profile_name(root, folder, local_state),
                    path=root / folder,
                    root=root,
                    channel=_channel_label(root),
                    last_used=profile_last_used(root, folder, local_state),
                    appears_running=folder in running,
                    login_saved=login_probably_saved(root, folder),
                )
            )
    return result


def profiles_as_text(profiles: list[BraveProfile]) -> str:
    """Plain-text table of profiles (used by `app.py --list-profiles`)."""
    rows = [("PROFILE NAME", "FOLDER", "LAST USED", "APPEARS RUNNING?", "LOGIN SAVED?")]
    for p in profiles:
        rows.append(
            (
                p.name if p.channel == "Brave" else f"{p.name} ({p.channel})",
                p.folder,
                p.last_used.strftime("%Y-%m-%d %H:%M") if p.last_used else "unknown",
                "appears to be running" if p.appears_running else "no",
                "probably" if p.login_saved else "no",
            )
        )
    widths = [max(len(row[i]) for row in rows) for i in range(5)]
    return "\n".join("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in rows)
