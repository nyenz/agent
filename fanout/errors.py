"""Turn exceptions into plain-English messages. Tracebacks are logged, never shown."""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FriendlyError:
    """A message a non-technical person can act on."""

    message: str
    fix: str

    def text(self) -> str:
        """Return message and suggested fix as one block."""
        return f"{self.message}\n\nWhat to try: {self.fix}"


class AttachError(RuntimeError):
    """Compatibility mode could not start a window or connect to it. The message is for the log only."""


def describe(exc: BaseException) -> FriendlyError:
    """Log the full traceback to fanout.log and return a friendly explanation."""
    log.error("Unhandled error", exc_info=(type(exc), exc, exc.__traceback__))
    if isinstance(exc, AttachError):
        return FriendlyError(
            "The window didn't start in compatibility mode.",
            "Nyenz Agent will use its standard mode instead. To stop trying, turn off Compatibility mode in Settings.",
        )
    text = str(exc).lower()
    if "browser helper isn't running" in text:
        return FriendlyError(
            "Nyenz Agent's browser helper stopped.",
            "Close Nyenz Agent and open it again. If it keeps happening, use Settings > Repair installation.",
        )
    if "executable doesn't exist" in text or "failed to launch" in text or "enoent" in text:
        return FriendlyError(
            "Nyenz Agent couldn't start the browser.",
            "If you chose the built-in Chromium, use Settings > Repair installation. "
            "If you use Brave, check that it is installed, then try again.",
        )
    if "singleton" in text or "already in use" in text or "profile appears to be in use" in text:
        return FriendlyError(
            "That browser profile is already open somewhere else.",
            "Close Brave completely, or choose 'App-managed windows' instead.",
        )
    if "timeout" in text or isinstance(exc, TimeoutError):
        return FriendlyError(
            "The browser took too long to respond.",
            "Close any stuck browser windows and try again.",
        )
    if isinstance(exc, PermissionError):
        return FriendlyError(
            "Nyenz Agent isn't allowed to read or write a file it needs.",
            "Close other programs using it, or run Nyenz Agent from a folder you own.",
        )
    if isinstance(exc, FileNotFoundError):
        return FriendlyError(
            "A file Nyenz Agent expected to find is missing.",
            "Click Rescan. If it keeps happening, use Settings > Repair installation.",
        )
    if isinstance(exc, OSError):
        return FriendlyError(
            "Your computer refused a file or network operation.",
            "Check your disk space and connection, then try again.",
        )
    return FriendlyError(
        "Something unexpected went wrong.",
        "Try again. If it repeats, open Settings > Open logs folder and send us fanout.log.",
    )
