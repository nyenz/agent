"""The promises FanOut makes, checked against the source: no hiding from sites, no passwords."""

from __future__ import annotations

import re
from pathlib import Path

from fanout import sessions

REPO = Path(__file__).resolve().parent.parent
SOURCES = [*sorted((REPO / "fanout").rglob("*.py")), *sorted((REPO / "fanout").rglob("*.js")), REPO / "app.py", REPO / "bootstrap.py"]

# Ways of making an automated browser look like a person's. FanOut uses none of them.
ANTI_DETECTION = re.compile(
    r"disable-blink-features|AutomationControlled|navigator\.webdriver|stealth|enable-automation|ignore_default_args|"
    r"undetected|fingerprint|headless=new|set_extra_http_headers|--user-agent|user_agent\s*=",
    re.IGNORECASE,
)
# Anything that would read, store or type a password or a saved login.
PASSWORD_HANDLING = re.compile(
    r"password|passwd|credential|keychain|autofill|get_cookies|add_cookies|storage_state|input\[type=.?password", re.I
)


def test_sources_are_found() -> None:
    assert len(SOURCES) > 15


def test_no_anti_detection_flags_anywhere_in_the_program() -> None:
    hits = [f"{p.name}: {m.group(0)}" for p in SOURCES for m in ANTI_DETECTION.finditer(p.read_text(encoding="utf-8"))]
    assert hits == []


def test_no_password_handling_anywhere_in_the_program() -> None:
    hits = [f"{p.name}: {m.group(0)}" for p in SOURCES for m in PASSWORD_HANDLING.finditer(p.read_text(encoding="utf-8"))]
    assert hits == []


def test_only_the_visible_banner_is_injected_into_pages() -> None:
    uses = [
        (p.name, line.strip())
        for p in SOURCES
        for line in p.read_text(encoding="utf-8").splitlines()
        if "add_init_script" in line or "evaluate(" in line and "navigator" in line
    ]
    assert uses == [("sessions.py", "await context.add_init_script(banner_script(number))")]


def test_a_login_import_can_only_copy_the_three_allowed_things() -> None:
    assert set(sessions.IMPORT_FILES) <= {"Network/Cookies", "Network/Cookies-journal", "Preferences", "Local State"}
    for name in sessions.IMPORT_FILES:
        assert not re.search(r"login|history|web data|extension|cache", name, re.I)


def test_the_installer_and_updater_never_ask_for_secrets() -> None:
    for name in ("INSTALL.bat", "run.bat", "install.command", "run.command"):
        text = (REPO / name).read_text(encoding="utf-8", errors="replace").lower()
        assert "token" not in text and "password" not in text.replace("your mac password", "").replace("your password (sudo)", ""), name
