"""Checks for the numbers on the Settings screen. Pure functions, so they are easy to test."""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config

STABLE_RANGE = (1.0, 30.0)
TIMEOUT_RANGE = (10, 1800)
DELAY_RANGE = (0.0, 300.0)


@dataclass
class SettingsValues:
    stable_seconds: float
    timeout_seconds: int
    min_delay_seconds: float


def _number(text: str, low: float, high: float, what: str) -> tuple[float | None, str]:
    try:
        value = float(text.strip().replace(",", "."))
    except ValueError:
        return None, f"{what} must be a number from {low:g} to {high:g}."
    if not low <= value <= high:
        return None, f"{what} must be between {low:g} and {high:g}."
    return value, ""


def parse_settings(stable: str, timeout: str, delay: str) -> tuple[SettingsValues | None, list[str]]:
    """Turn the three text boxes into numbers. Returns (values, []) or (None, plain-English problems)."""
    problems: list[str] = []
    s, msg = _number(stable, *STABLE_RANGE, "'Wait until the answer stops changing' (seconds)")
    problems += [msg] if msg else []
    t, msg = _number(timeout, *TIMEOUT_RANGE, "'Give up on an account after' (seconds)")
    problems += [msg] if msg else []
    d, msg = _number(delay, *DELAY_RANGE, "'Gap between two sends from one account' (seconds)")
    problems += [msg] if msg else []
    if problems or s is None or t is None or d is None:
        return None, problems
    return SettingsValues(s, int(round(t)), d), []


def apply_settings(cfg: Config, values: SettingsValues, be_gentle: bool, check_updates: bool) -> None:
    """Copy checked values into the config (the caller saves it)."""
    cfg.stable_seconds = values.stable_seconds
    cfg.timeout_seconds = values.timeout_seconds
    cfg.min_delay_seconds = values.min_delay_seconds
    cfg.be_gentle = be_gentle
    cfg.check_updates = check_updates
