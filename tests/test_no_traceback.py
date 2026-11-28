"""No raw traceback may reach a person. Tracebacks go to fanout.log only."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from fanout import errors, updater

REPO = Path(__file__).resolve().parent.parent
TRACE_MARKERS = ("Traceback (most recent call last)", 'File "', "line ", "Error:", "Exception:")


class Weird(Exception):
    pass


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("bad value"), KeyError("missing"), OSError("disk on fire"), PermissionError("nope"), FileNotFoundError("gone"),
        TimeoutError("took ages"), RuntimeError("Executable doesn't exist at /x/y\n  call log:\n  - File \"a.py\", line 3"),
        Weird("Profile appears to be in use by another process"), Exception("Target page, context or browser has been closed"),
    ],
)
def test_every_kind_of_error_becomes_plain_words(exc: Exception) -> None:
    shown = errors.describe(exc).text()
    assert "What to try:" in shown
    assert not any(marker in shown for marker in TRACE_MARKERS), shown
    assert type(exc).__name__ not in shown


def test_the_log_gets_the_traceback_but_the_screen_does_not(caplog: pytest.LogCaptureFixture) -> None:
    try:
        raise RuntimeError("secret details")
    except RuntimeError as exc:
        with caplog.at_level("ERROR"):
            shown = errors.describe(exc).text()
    assert "secret details" not in shown
    assert any(record.exc_info for record in caplog.records)


def test_a_helper_crash_is_not_pasted_into_a_dialog() -> None:
    crash = 'Traceback (most recent call last):\n  File "bootstrap.py", line 1, in <module>\nValueError: boom'
    assert "Traceback" not in updater.plain_tail(crash)
    assert updater.plain_tail("a\nb\nc") == "a\nb\nc"
    assert len(updater.plain_tail("\n".join(str(i) for i in range(50))).splitlines()) == 6


def test_the_ui_code_never_formats_a_traceback() -> None:
    for path in (REPO / "fanout").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "format_exc" not in text and "traceback.format" not in text, path.name


def run_app(*argv: str, hide_tkinter: bool = False) -> subprocess.CompletedProcess[str]:
    prelude = "import sys, runpy\n" + ("sys.modules['tkinter'] = None\n" if hide_tkinter else "")
    code = prelude + f"sys.argv = ['app.py', *{list(argv)!r}]\nrunpy.run_path('app.py', run_name='__main__')"
    return subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=60, check=False)


def test_starting_without_tkinter_says_what_to_do_and_shows_no_traceback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FANOUT_HOME", str(tmp_path))
    done = run_app(hide_tkinter=True)
    assert done.returncode == 1
    assert "Tkinter" in done.stdout and "INSTALL" in done.stdout
    assert "Traceback" not in done.stdout + done.stderr


def test_commands_that_need_no_window_work_without_tkinter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FANOUT_HOME", str(tmp_path))
    done = run_app("--version", hide_tkinter=True)
    assert done.returncode == 0 and "Nyenz Agent" in done.stdout
