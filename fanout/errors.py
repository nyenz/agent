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


def describe(exc: BaseException) -> FriendlyError:
    """Log the full traceback to fanout.log and return a friendly explanation."""
    log.error("Unhandled error", exc_info=(type(exc), exc, exc.__traceback__))
    if isinstance(exc, PermissionError):
        return FriendlyError(
            "FanOut isn't allowed to read or write a file it needs.",
            "Close other programs using it, or run FanOut from a folder you own.",
        )
    if isinstance(exc, FileNotFoundError):
        return FriendlyError(
            "A file FanOut expected to find is missing.",
            "Click Rescan. If it keeps happening, use Settings > Repair installation.",
        )
    if isinstance(exc, OSError):
        return FriendlyError(
            "Your computer refused a file or network operation.",
            "Check your disk space and connection, then try again.",
        )
    return FriendlyError(
        "Something unexpected went wrong.",
        "Try again. If it repeats, send us logs/fanout.log from the FanOut folder.",
    )
