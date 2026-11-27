"""Updater tests: a fake GitHub (local web server) serves a newer release; nothing touches the real network."""

from __future__ import annotations

import hashlib
import http.server
import json
import tempfile
import threading
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from fanout import updater
from fanout.updater import ReleaseInfo, UpdateError


def make_zip(path: Path, version: str, *, top: str = "FanOut-source", extra: dict[str, str] | None = None) -> None:
    files = {
        "app.py": "print('new app')\n",
        "bootstrap.py": "# new bootstrap\n",
        "requirements.txt": "psutil==1.0.0\n",
        "fanout/__init__.py": f'__version__ = "{version}"\n',
        "fanout/newmodule.py": "NEW = True\n",
    }
    files.update(extra or {})
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in files.items():
            zf.writestr(f"{top}/{name}" if top else name, body)


class FakeGitHub:
    """Serves /latest (the API), /asset.zip and /asset.sha256 from memory."""

    def __init__(self) -> None:
        self.tag = "v9.0.0"
        self.api_status = 200
        self.zip_bytes = b""
        self.sha_text = ""
        self.has_sha_asset = True
        self.setup_bytes = b""
        self.setup_sha = ""
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_a: object) -> None:
                pass

            def do_GET(self) -> None:
                if self.path == "/latest":
                    if outer.api_status != 200:
                        self.send_response(outer.api_status)
                        self.end_headers()
                        return
                    base = f"http://127.0.0.1:{outer.server.server_port}"
                    assets = [{"name": f"FanOut-source-{outer.tag}.zip", "browser_download_url": f"{base}/asset.zip"}]
                    if outer.has_sha_asset:
                        assets.append({"name": f"FanOut-source-{outer.tag}.zip.sha256", "browser_download_url": f"{base}/asset.sha256"})
                    release = {"tag_name": outer.tag, "body": "Notes here", "html_url": f"{base}/release", "assets": assets}
                    if outer.setup_bytes:
                        name = f"FanOut-Setup-{outer.tag.lstrip('v')}.exe"
                        assets += [{"name": name, "browser_download_url": f"{base}/setup.exe"},
                                   {"name": f"{name}.sha256", "browser_download_url": f"{base}/setup.sha256"}]
                    body = json.dumps(release).encode()
                elif self.path == "/asset.zip":
                    body = outer.zip_bytes
                elif self.path == "/asset.sha256":
                    body = outer.sha_text.encode()
                elif self.path == "/setup.exe":
                    body = outer.setup_bytes
                elif self.path == "/setup.sha256":
                    body = outer.setup_sha.encode()
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def api(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/latest"

    def publish(self, tmp: Path, version: str, **kw: object) -> None:
        self.tag = f"v{version}"
        archive = tmp / "release.zip"
        make_zip(archive, version, **kw)  # type: ignore[arg-type]
        self.zip_bytes = archive.read_bytes()
        self.sha_text = f"{hashlib.sha256(self.zip_bytes).hexdigest()}  FanOut-source-{self.tag}.zip\n"


@pytest.fixture
def github() -> Iterator[FakeGitHub]:
    fake = FakeGitHub()
    yield fake
    fake.server.shutdown()


@pytest.fixture
def install(tmp_path: Path) -> Path:
    """A pretend installed copy of an older FanOut."""
    root = tmp_path / "install"
    (root / "fanout").mkdir(parents=True)
    (root / "fanout" / "__init__.py").write_text('__version__ = "0.0.1"\n')
    (root / "fanout" / "oldmodule.py").write_text("OLD = True\n")
    (root / "app.py").write_text("print('old app')\n")
    (root / "bootstrap.py").write_text("# old bootstrap\n")
    (root / "requirements.txt").write_text("psutil==0.0.1\n")
    (root / ".venv").mkdir()
    (root / ".venv" / "keep.txt").write_text("my venv")
    (root / "install_state.json").write_text("{}")
    return root


def info_for(github: FakeGitHub) -> ReleaseInfo:
    found = updater.fetch_latest(github.api)
    assert found is not None
    return found


def test_version_parsing_ignores_prereleases() -> None:
    assert updater.parse_version("v1.2.3") == (1, 2, 3)
    assert updater.parse_version("0.10.0") == (0, 10, 0)
    assert updater.parse_version("1.2.3-beta") is None
    assert updater.parse_version("latest") is None


def test_banner_appears_for_a_newer_tag_and_not_for_older_or_skipped(github: FakeGitHub, tmp_path: Path) -> None:
    github.publish(tmp_path, "9.0.0")
    found = updater.check_for_update("0.2.0", "", github.api)
    assert found is not None and found.tag == "v9.0.0" and found.notes == "Notes here"
    assert updater.check_for_update("9.0.0", "", github.api) is None, "same version: no banner"
    assert updater.check_for_update("10.0.0", "", github.api) is None, "older release: no banner"
    assert updater.check_for_update("0.2.0", "9.0.0", github.api) is None, "skipped version: no banner"
    assert updater.check_for_update("0.2.0", "8.0.0", github.api) is not None, "skipping an older one doesn't hide this"


def test_offline_or_broken_github_is_silent(github: FakeGitHub, tmp_path: Path) -> None:
    github.api_status = 403  # e.g. rate limited
    assert updater.check_for_update("0.2.0", "", github.api) is None
    assert updater.check_for_update("0.2.0", "", "http://127.0.0.1:9/nothing-listens-here") is None
    github.api_status = 200
    github.publish(tmp_path, "9.0.0")
    github.has_sha_asset = False
    assert updater.check_for_update("0.2.0", "", github.api) is None, "a release without a checksum is not offered"


def test_update_swaps_code_keeps_private_files_and_leaves_a_backup(github: FakeGitHub, install: Path, tmp_path: Path) -> None:
    github.publish(tmp_path, "9.0.0")
    steps: list[str] = []
    refreshed: list[Path] = []
    new = updater.perform_update(info_for(github), install, steps.append, lambda r: (refreshed.append(r) or True, ""))
    assert new == "9.0.0"
    assert "9.0.0" in (install / "fanout" / "__init__.py").read_text()
    assert (install / "fanout" / "newmodule.py").exists()
    assert not (install / "fanout" / "oldmodule.py").exists(), "a module the new version dropped must not linger"
    assert (install / ".venv" / "keep.txt").read_text() == "my venv"
    assert (install / "install_state.json").exists()
    assert refreshed == [install]
    backups = list((install / ".backup").iterdir())
    assert len(backups) == 1 and (backups[0] / "app.py").read_text() == "print('old app')\n"
    assert (backups[0] / "fanout" / "oldmodule.py").exists()
    assert any("Downloading" in s for s in steps) and any("backup" in s.lower() for s in steps)


def test_a_failed_package_refresh_rolls_everything_back(github: FakeGitHub, install: Path, tmp_path: Path) -> None:
    github.publish(tmp_path, "9.0.0")
    calls: list[int] = []

    def failing(_root: Path) -> tuple[bool, str]:
        calls.append(1)
        return (len(calls) > 1), "pip exploded"  # the first call (new packages) fails; the second (old packages) works

    with pytest.raises(UpdateError, match="went back to your old version"):
        updater.perform_update(info_for(github), install, refresh=failing)
    assert "0.0.1" in (install / "fanout" / "__init__.py").read_text()
    assert (install / "fanout" / "oldmodule.py").exists() and not (install / "fanout" / "newmodule.py").exists()
    assert (install / "app.py").read_text() == "print('old app')\n"
    assert (install / ".venv" / "keep.txt").exists()
    assert len(calls) == 2, "the old package list is restored after a rollback"


def test_a_bad_checksum_changes_nothing(github: FakeGitHub, install: Path, tmp_path: Path) -> None:
    github.publish(tmp_path, "9.0.0")
    github.sha_text = "0" * 64 + "  x.zip\n"
    with pytest.raises(UpdateError, match="checksum"):
        updater.perform_update(info_for(github), install)
    assert (install / "app.py").read_text() == "print('old app')\n" and not (install / ".backup").exists()


def test_an_unreadable_checksum_file_changes_nothing(github: FakeGitHub, install: Path, tmp_path: Path) -> None:
    github.publish(tmp_path, "9.0.0")
    github.sha_text = "not a hash"
    with pytest.raises(UpdateError, match="checksum file"):
        updater.perform_update(info_for(github), install)
    assert (install / "app.py").read_text() == "print('old app')\n"


def test_a_zip_that_escapes_its_folder_is_refused(github: FakeGitHub, install: Path, tmp_path: Path) -> None:
    github.publish(tmp_path, "9.0.0", extra={"../../evil.txt": "x"})
    with pytest.raises(UpdateError, match="unsafe"):
        updater.perform_update(info_for(github), install)
    assert not (tmp_path / "evil.txt").exists() and not (install.parent / "evil.txt").exists()


def test_a_release_whose_version_doesnt_match_its_tag_is_refused(github: FakeGitHub, install: Path, tmp_path: Path) -> None:
    github.publish(tmp_path, "9.0.0")
    github.tag = "v9.1.0"  # tag says 9.1.0 but the code inside says 9.0.0
    with pytest.raises(UpdateError, match="not v9.1.0"):
        updater.perform_update(info_for(github), install)
    assert (install / "app.py").read_text() == "print('old app')\n"


def test_something_that_isnt_fanout_is_refused(github: FakeGitHub, install: Path, tmp_path: Path) -> None:
    archive = tmp_path / "other.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("readme.txt", "hello")
    github.zip_bytes = archive.read_bytes()
    github.sha_text = hashlib.sha256(github.zip_bytes).hexdigest()
    github.tag = "v9.0.0"
    with pytest.raises(UpdateError, match="didn't look like FanOut"):
        updater.perform_update(info_for(github), install)


def test_a_dropped_connection_gives_a_plain_message(github: FakeGitHub, install: Path, tmp_path: Path) -> None:
    github.publish(tmp_path, "9.0.0")
    found = info_for(github)
    github.server.shutdown()
    github.server.server_close()
    with pytest.raises(UpdateError, match="internet connection"):
        updater.perform_update(found, install, timeout=2)


def test_only_the_newest_backups_are_kept(tmp_path: Path) -> None:
    import os

    root = tmp_path
    for i in range(6):
        d = root / ".backup" / f"b{i}"
        d.mkdir(parents=True)
        os.utime(d, (1_000_000 + i, 1_000_000 + i))
    updater.prune_backups(root, keep=3)
    assert sorted(p.name for p in (root / ".backup").iterdir()) == ["b3", "b4", "b5"]


def test_a_real_refresh_runs_bootstrap_update_in_the_install(install: Path) -> None:
    """The real refresh step: runs `bootstrap.py update` in that install, with a stand-in bootstrap."""
    (install / "bootstrap.py").write_text("import sys\nprint('refreshed', sys.argv[1])\n")
    ok, tail = updater.refresh_packages(install)
    assert ok and "refreshed update" in tail
    (install / "bootstrap.py").write_text("import sys\nprint('nope')\nsys.exit(3)\n")
    ok, tail = updater.refresh_packages(install)
    assert not ok and "nope" in tail


def test_a_whole_update_with_the_real_bootstrap(github: FakeGitHub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Real venv, real `bootstrap.py update` subprocess, real swap. Offline: the venv can see system packages."""
    import importlib.metadata as md
    import shutil

    import bootstrap

    root = tmp_path / "live"
    root.mkdir()
    (root / "fanout").mkdir()
    (root / "fanout" / "__init__.py").write_text('__version__ = "0.0.1"\n')
    (root / "app.py").write_text("print('old')\n")
    shutil.copy(Path(bootstrap.__file__), root / "bootstrap.py")
    pin = f"psutil=={md.version('psutil')}\n"
    (root / "requirements.txt").write_text(pin)
    monkeypatch.setenv("FANOUT_VENV_SYSTEM_SITE", "1")
    monkeypatch.setattr(bootstrap, "tk_works", lambda _py: True)
    assert bootstrap.install_all(root, browser=False, shortcut=False, quiet=True)[0]

    github.publish(tmp_path, "9.0.0", extra={"bootstrap.py": Path(bootstrap.__file__).read_text(), "requirements.txt": pin + "# bumped\n"})
    assert updater.perform_update(info_for(github), root) == "9.0.0"
    assert "9.0.0" in (root / "fanout" / "__init__.py").read_text()
    state = bootstrap.load_state(root)
    assert state["requirements_hash"] == bootstrap.file_hash(root / "requirements.txt"), "packages were refreshed for the new list"
    assert bootstrap.needs_repair(root) == ""
    assert updater.restart_command(root)[0] == str(bootstrap.venv_python(root))


def test_only_safe_addresses_are_downloaded(install: Path) -> None:
    assert updater.allowed_url("https://github.com/x/y.zip")
    assert updater.allowed_url("http://127.0.0.1:8000/a.zip")
    for bad in ("file:///etc/passwd", "http://example.com/a.zip", "ftp://example.com/a.zip"):
        assert not updater.allowed_url(bad), bad
    info = ReleaseInfo("v9.0.0", (9, 0, 0), "", "", "file:///etc/hosts", "file:///etc/hosts")
    with pytest.raises(UpdateError, match="didn't look safe"):
        updater.perform_update(info, install)
    assert (install / "app.py").read_text() == "print('old app')\n"


def test_the_installed_windows_app_updates_with_the_new_installer(
    github: FakeGitHub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    github.publish(tmp_path, "9.0.0")
    github.setup_bytes = b"MZ pretend installer"
    github.setup_sha = hashlib.sha256(github.setup_bytes).hexdigest() + "  FanOut-Setup-9.0.0.exe\n"
    info = info_for(github)
    assert info.has_installer and info.setup_url.endswith("/setup.exe")
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater.sys, "platform", "win32")
    assert updater.can_self_install(info)
    steps: list[str] = []
    setup = updater.download_installer(info, steps.append)
    assert setup.read_bytes() == b"MZ pretend installer" and setup.name == "FanOut-Setup-9.0.0.exe"
    assert any("Checking" in s for s in steps)
    cmd = updater.installer_command(setup)
    assert cmd[0] == str(setup) and {"/VERYSILENT", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS", "/relaunch=1"} <= set(cmd)


def test_a_bad_installer_download_is_thrown_away(github: FakeGitHub, tmp_path: Path) -> None:
    github.publish(tmp_path, "9.0.0")
    github.setup_bytes = b"MZ tampered"
    github.setup_sha = "0" * 64
    info = info_for(github)
    before = set(Path(tempfile.gettempdir()).glob("fanout-setup-*"))
    with pytest.raises(UpdateError, match="checksum"):
        updater.download_installer(info)
    assert set(Path(tempfile.gettempdir()).glob("fanout-setup-*")) == before, "the bad file must be deleted"


def test_mac_linux_and_source_copies_do_not_use_the_windows_installer(
    github: FakeGitHub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    github.publish(tmp_path, "9.0.0")
    github.setup_bytes = b"MZ"
    github.setup_sha = hashlib.sha256(b"MZ").hexdigest()
    info = info_for(github)
    assert not updater.can_self_install(info), "a copy run from source updates by swapping files instead"
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater.sys, "platform", "darwin")
    assert not updater.can_self_install(info)
    without = ReleaseInfo("v9.0.0", (9, 0, 0), "", "", "https://x/z.zip", "https://x/z.sha256")
    monkeypatch.setattr(updater.sys, "platform", "win32")
    assert not updater.can_self_install(without), "no installer in the release: open the download page instead"
