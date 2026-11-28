"""Update Nyenz Agent from its GitHub Releases, safely.

Every step is built so that a failure leaves the old version working:
  check (silent when offline) -> download -> verify SHA-256 -> unpack to a staging folder -> sanity-check
  -> back up the current code to .backup/ -> swap in the new code -> refresh packages -> restart,
  and if anything after the backup fails, the backup is put back (automatic rollback).

Your settings, logins and results live in the per-user data folder, outside the program folder, so an
update never touches them. An update is never started while browser windows are open.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__

log = logging.getLogger(__name__)

REPO = "nyenz/agent"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
SOURCE_PREFIX = "FanOut-source-"
SETUP_PREFIX = "FanOut-Setup-"
PROTECTED = {".venv", ".backup", ".git", "install_state.json", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
MAX_DOWNLOAD = 100 * 1024 * 1024
MAX_INSTALLER = 1024 * 1024 * 1024
KEEP_BACKUPS = 3
ROOT = Path(__file__).resolve().parent.parent


class UpdateError(Exception):
    """A problem that can be shown to the user as it is. Nothing was changed when this is raised."""


@dataclass
class ReleaseInfo:
    tag: str
    version: tuple[int, ...]
    notes: str
    page_url: str
    zip_url: str
    sha_url: str
    setup_url: str = ""  # Windows installer (FanOut-Setup-x.y.z.exe), used by the installed app
    setup_sha_url: str = ""

    @property
    def has_installer(self) -> bool:
        return bool(self.setup_url and self.setup_sha_url)


def parse_version(text: str) -> tuple[int, ...] | None:
    """'v1.2.3' or '1.2.3' -> (1, 2, 3). Anything else (a pre-release such as 1.2.3-beta) -> None."""
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", text.strip())
    return tuple(int(g) for g in match.groups()) if match else None


def is_frozen() -> bool:
    """True when running as a packaged program (it can't swap its own files; it points to the download page)."""
    return bool(getattr(sys, "frozen", False))


def allowed_url(url: str) -> bool:
    """Only https addresses (and plain http on this computer, which the tests use) may be fetched."""
    parts = urllib.parse.urlsplit(url)
    return parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost"))


def _get(url: str, timeout: float, limit: int, accept: str = "application/octet-stream") -> bytes:
    if not allowed_url(url):
        raise UpdateError("The update address didn't look safe, so I didn't download it. Nothing was changed.")
    request = urllib.request.Request(url, headers={"User-Agent": f"FanOut/{__version__}", "Accept": accept})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data: bytes = response.read(limit + 1)
    if len(data) > limit:
        raise UpdateError("The download was bigger than expected, so I stopped. Nothing was changed.")
    return data


def fetch_latest(api_url: str = API_URL, timeout: float = 8.0) -> ReleaseInfo | None:
    """The newest published release that carries a source zip and its checksum, or None. Never raises."""
    try:
        data: dict[str, Any] = json.loads(_get(api_url, timeout, 2_000_000, "application/vnd.github+json"))
        tag = str(data.get("tag_name", ""))
        version = parse_version(tag)
        assets = {str(a.get("name", "")): str(a.get("browser_download_url", "")) for a in data.get("assets", [])}
        zips = [n for n in assets if n.startswith(SOURCE_PREFIX) and n.endswith(".zip")]
        if version is None or not zips or f"{zips[0]}.sha256" not in assets:
            return None
        setups = [n for n in assets if n.startswith(SETUP_PREFIX) and n.endswith(".exe") and f"{n}.sha256" in assets]
        return ReleaseInfo(tag, version, str(data.get("body") or ""), str(data.get("html_url") or ""),
                           assets[zips[0]], assets[f"{zips[0]}.sha256"],
                           assets[setups[0]] if setups else "", assets[f"{setups[0]}.sha256"] if setups else "")
    except (OSError, ValueError, UpdateError, AttributeError, TypeError):
        log.info("Update check skipped (offline or no usable release)", exc_info=True)
        return None


def check_for_update(current: str = __version__, skipped: str = "", api_url: str = API_URL) -> ReleaseInfo | None:
    """A release newer than `current` that the user hasn't chosen to skip, else None."""
    mine = parse_version(current)
    info = fetch_latest(api_url)
    if info is None or mine is None or info.version <= mine:
        return None
    if skipped and parse_version(skipped) == info.version:
        return None
    return info


def read_checksum(text: str) -> str:
    """First 64-hex-digit word in a '.sha256' file (works for 'abc...  file.zip' and a bare hash)."""
    match = re.search(r"\b[0-9a-fA-F]{64}\b", text)
    if not match:
        raise UpdateError("The update's checksum file was unreadable, so I did not install it. Nothing was changed.")
    return match.group(0).lower()


def safe_extract(archive: Path, dest: Path) -> Path:
    """Unpack a zip, refusing paths that escape `dest`. Returns the folder holding app.py (one level down is fine)."""
    base = dest.resolve()
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            target = (dest / member.filename).resolve()
            if not target.is_relative_to(base):
                raise UpdateError("The update file contained an unsafe path, so I did not install it. Nothing was changed.")
        zf.extractall(dest)
    if (dest / "app.py").exists():
        return dest
    children = [c for c in dest.iterdir() if c.is_dir()]
    if len(children) == 1 and (children[0] / "app.py").exists():
        return children[0]
    raise UpdateError("The update file didn't look like Nyenz Agent, so I did not install it. Nothing was changed.")


def version_in(tree: Path) -> str:
    """The __version__ written in an unpacked Nyenz Agent folder, or ''."""
    try:
        text = (tree / "fanout" / "__init__.py").read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else ""


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def apply_tree(new_tree: Path, root: Path, backup: Path) -> list[str]:
    """Back up each top-level item the new version has, then put the new one in place.

    On any error everything already swapped is restored and the error is raised again.
    """
    names = sorted(p.name for p in new_tree.iterdir() if p.name not in PROTECTED)
    backup.mkdir(parents=True, exist_ok=True)
    backed: list[str] = []
    placed: list[str] = []
    try:
        for name in names:
            old = root / name
            if old.exists():
                shutil.move(str(old), str(backup / name))
                backed.append(name)
            shutil.move(str(new_tree / name), str(root / name))
            placed.append(name)
    except Exception:
        restore(root, backup, backed, placed)
        raise
    return names


def restore(root: Path, backup: Path, backed: list[str], placed: list[str]) -> None:
    """Undo apply_tree: remove what was put in place and move the backed-up items home."""
    for name in placed:
        _remove(root / name)
    for name in backed:
        if (backup / name).exists():
            _remove(root / name)
            shutil.move(str(backup / name), str(root / name))


def prune_backups(root: Path, keep: int = KEEP_BACKUPS) -> None:
    """Keep only the newest few backup folders."""
    folder = root / ".backup"
    if not folder.is_dir():
        return
    for old in sorted((p for p in folder.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)[:-keep]:
        shutil.rmtree(old, ignore_errors=True)


def venv_python(root: Path) -> Path:
    sub = Path("Scripts") / "python.exe" if sys.platform == "win32" else Path("bin") / "python"
    py = root / ".venv" / sub
    return py if py.exists() else Path(sys.executable)


def plain_tail(output: str) -> str:
    """Last few lines of a helper's output, or a plain sentence if it looks like a Python crash."""
    if "Traceback (most recent call last)" in output:
        return "The helper stopped unexpectedly. The details are in fanout.log."
    return "\n".join(output.strip().splitlines()[-6:])


def refresh_packages(root: Path) -> tuple[bool, str]:
    """Run `bootstrap.py update` with the app's own Python. Returns (ok, last few lines of output)."""
    script = root / "bootstrap.py"
    if not script.exists():
        return False, "bootstrap.py is missing"
    try:
        done = subprocess.run(
            [str(venv_python(root)), str(script), "update"], cwd=root, capture_output=True, text=True, timeout=1800, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    tail = plain_tail(done.stdout + done.stderr)
    return done.returncode == 0, tail


def perform_update(
    info: ReleaseInfo,
    root: Path = ROOT,
    progress: Callable[[str], None] = lambda _t: None,
    refresh: Callable[[Path], tuple[bool, str]] = refresh_packages,
    timeout: float = 60.0,
) -> str:
    """Download, verify and install `info`. Returns the new version. Raises UpdateError with a plain message.

    If the update fails after the swap the old files are restored, then `refresh` runs once more for the old
    package list, so a failed update leaves the same working Nyenz Agent as before.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    with tempfile.TemporaryDirectory(prefix="fanout-update-") as tmp_name:
        tmp = Path(tmp_name)
        progress("Downloading the update...")
        try:
            archive = tmp / "update.zip"
            archive.write_bytes(_get(info.zip_url, timeout, MAX_DOWNLOAD))
            wanted = read_checksum(_get(info.sha_url, timeout, 10_000).decode("utf-8", "replace"))
        except OSError as exc:
            raise UpdateError("I couldn't download the update. Check your internet connection and try again.") from exc
        progress("Checking the download...")
        if hashlib.sha256(archive.read_bytes()).hexdigest() != wanted:
            raise UpdateError("The download didn't match its checksum, so I did not install it. Nothing was changed.")
        progress("Unpacking...")
        try:
            new_tree = safe_extract(archive, tmp / "new")
        except zipfile.BadZipFile as exc:
            raise UpdateError("The update file was damaged, so I did not install it. Nothing was changed.") from exc
        found = version_in(new_tree)
        if found != ".".join(str(n) for n in info.version):
            raise UpdateError(f"The update says it is {found or 'unknown'}, not {info.tag}, so I did not install it. Nothing was changed.")

        progress("Saving a backup of the current version...")
        backup = root / ".backup" / f"{__version__}-{stamp}"
        names: list[str] = []
        try:
            names = apply_tree(new_tree, root, backup)
        except OSError as exc:
            raise UpdateError("I couldn't replace the program files (are they open somewhere?). Nothing was changed.") from exc

        progress("Updating Nyenz Agent's packages (this can take a few minutes)...")
        ok, tail = refresh(root)
        if not ok:
            log.error("Package refresh failed after update; rolling back. Output: %s", tail)
            progress("Something went wrong. Going back to the old version...")
            restore(root, backup, [n for n in names if (backup / n).exists()], names)
            refresh(root)  # put the old package list back; best effort
            raise UpdateError("The update didn't finish, so I went back to your old version. It still works. Nothing was lost.")
    prune_backups(root)
    return ".".join(str(n) for n in info.version)


def can_self_install(info: ReleaseInfo) -> bool:
    """True when this copy updates by running the new Windows installer (the installed FanOut-Setup app)."""
    return is_frozen() and sys.platform == "win32" and info.has_installer


def download_installer(info: ReleaseInfo, progress: Callable[[str], None] = lambda _t: None, timeout: float = 120.0) -> Path:
    """Download and verify the new installer into a fresh temporary folder. Returns its path.

    Nothing is installed yet, so on any problem nothing has changed.
    """
    if not info.has_installer:
        raise UpdateError("This release has no Windows installer. Nothing was changed.")
    folder = Path(tempfile.mkdtemp(prefix="fanout-setup-"))
    target = folder / f"{SETUP_PREFIX}{info.tag.lstrip('v')}.exe"
    progress("Downloading the update...")
    try:
        target.write_bytes(_get(info.setup_url, timeout, MAX_INSTALLER))
        wanted = read_checksum(_get(info.setup_sha_url, timeout, 10_000).decode("utf-8", "replace"))
    except OSError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise UpdateError("I couldn't download the update. Check your internet connection and try again.") from exc
    progress("Checking the download...")
    if hashlib.sha256(target.read_bytes()).hexdigest() != wanted:
        shutil.rmtree(folder, ignore_errors=True)
        raise UpdateError("The download didn't match its checksum, so I did not install it. Nothing was changed.")
    return target


def installer_command(setup: Path) -> list[str]:
    """Run the installer with no questions, close Nyenz Agent if needed, and reopen Nyenz Agent when it is done."""
    return [str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/relaunch=1"]


def start_installer(setup: Path) -> None:
    """Start the installer on its own, so it keeps going after this copy of Nyenz Agent closes."""
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    subprocess.Popen(installer_command(setup), creationflags=flags, close_fds=True)


def restart_command(root: Path = ROOT) -> list[str]:
    """Command that starts the app again with the right Python."""
    return [str(venv_python(root)), str(root / "app.py")]


def restart(root: Path = ROOT) -> None:
    """Start a fresh copy of the app (the caller then closes this one)."""
    subprocess.Popen(restart_command(root), cwd=root, close_fds=True)


def repair_installation(root: Path = ROOT) -> tuple[bool, str]:
    """Run `bootstrap.py repair` (rebuild anything missing). Returns (ok, last few lines of output)."""
    script = root / "bootstrap.py"
    if not script.exists():
        return False, "bootstrap.py is missing, so I can't repair. Run INSTALL again from the Nyenz Agent folder."
    try:
        done = subprocess.run(
            [str(venv_python(root)), str(script), "repair", "--no-launch", "--no-dialog", "--no-shortcut"],
            cwd=root, capture_output=True, text=True, timeout=1800, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    tail = plain_tail(done.stdout + done.stderr)
    return done.returncode == 0, tail
