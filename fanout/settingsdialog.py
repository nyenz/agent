"""The Settings window: timing numbers, update and repair buttons. Never blocks the main window."""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from collections.abc import Sequence
from pathlib import Path
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from . import __version__
from .config import app_data_dir
from .logging_setup import log_dir
from .settings import DELAY_RANGE, STABLE_RANGE, TIMEOUT_RANGE, apply_settings, parse_settings

if TYPE_CHECKING:
    from .ui import FanOutApp


def open_folder_command(path: Path, platform: str = sys.platform) -> Sequence[str]:
    """Command that shows a folder in the system's file manager (not used for Windows, which has os.startfile)."""
    return ["open", str(path)] if platform == "darwin" else ["xdg-open", str(path)]


def open_folder(path: Path) -> None:
    """Show a folder in the file manager; say so plainly if that isn't possible."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(open_folder_command(path))
    except OSError:
        messagebox.showinfo("Nyenz Agent", f"I couldn't open the folder for you. It is here:\n\n{path}")


class SettingsDialog:
    """A small window next to the main one. Saves only when the numbers pass the checks."""

    def __init__(self, app: FanOutApp) -> None:
        self.app = app
        cfg = app.cfg
        self.win = tk.Toplevel(app.root)
        self.win.title("Nyenz Agent settings")
        self.win.transient(app.root)
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        body = ttk.Frame(self.win, padding=16)
        body.pack(fill="both", expand=True)

        self.stable = tk.StringVar(value=f"{cfg.stable_seconds:g}")
        self.timeout = tk.StringVar(value=str(cfg.timeout_seconds))
        self.delay = tk.StringVar(value=f"{cfg.min_delay_seconds:g}")
        self.gentle = tk.BooleanVar(value=cfg.be_gentle)
        self.updates = tk.BooleanVar(value=cfg.check_updates)
        self.compat = tk.BooleanVar(value=cfg.compatibility_mode)
        self.auto = tk.BooleanVar(value=cfg.auto_update)
        self.message = tk.StringVar()

        ttk.Label(body, text="How long to wait", font=("TkDefaultFont", 12, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        rows = (
            ("Wait until an answer stops changing (seconds)", self.stable, STABLE_RANGE),
            ("Give up on an account after (seconds)", self.timeout, TIMEOUT_RANGE),
            ("Gap between two sends from one account (seconds)", self.delay, DELAY_RANGE),
        )
        for i, (label, var, (low, high)) in enumerate(rows, start=1):
            ttk.Label(body, text=label).grid(row=i, column=0, sticky="w", pady=4, padx=(0, 12))
            ttk.Entry(body, textvariable=var, width=8).grid(row=i, column=1, sticky="w")
            ttk.Label(body, text=f"{low:g} to {high:g}", foreground="#666666").grid(row=i, column=2, sticky="w", padx=8)
        ttk.Checkbutton(
            body, text="Be gentle (doubles the gap, never less than 10 seconds)", variable=self.gentle
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))

        ttk.Checkbutton(
            body, text="Compatibility mode: open windows as a normal browser (recommended)", variable=self.compat
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(
            body,
            text="Helps with sites that ask you to prove you are human. You still log in yourself. "
            "Takes effect the next time windows open. Turn it off only if windows won't open.",
            foreground="#666666",
            wraplength=480,
            justify="left",
        ).grid(row=6, column=0, columnspan=3, sticky="w")

        ttk.Separator(body).grid(row=7, column=0, columnspan=3, sticky="ew", pady=12)
        title = ttk.Label(body, text=f"Nyenz Agent {__version__}", font=("TkDefaultFont", 12, "bold"))
        title.grid(row=8, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(body, text="Look for updates when Nyenz Agent starts", variable=self.updates).grid(
            row=9, column=0, columnspan=3, sticky="w", pady=(4, 0)
        )
        ttk.Checkbutton(
            body, text="Install new versions by itself when nothing is running (recommended)", variable=self.auto
        ).grid(row=10, column=0, columnspan=3, sticky="w", pady=(2, 0))
        buttons = ttk.Frame(body)
        buttons.grid(row=11, column=0, columnspan=3, sticky="w", pady=8)
        ttk.Button(buttons, text="Check for updates", command=self.check_now).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Repair installation", command=self.repair).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Open logs folder", command=lambda: open_folder(log_dir())).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Open results folder", command=lambda: open_folder(app_data_dir() / "results")).pack(side="left")

        self.error = ttk.Label(body, textvariable=self.message, foreground="#b00020", wraplength=480, justify="left")
        self.error.grid(row=12, column=0, columnspan=3, sticky="w")
        bottom = ttk.Frame(body)
        bottom.grid(row=13, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(bottom, text="Save", command=self.save).pack(side="left", padx=(0, 8))
        ttk.Button(bottom, text="Close", command=self.close).pack(side="left")

    def exists(self) -> bool:
        return bool(self.win.winfo_exists())

    def lift(self) -> None:
        self.win.lift()
        self.win.focus_set()

    def close(self) -> None:
        if self.exists():
            self.win.destroy()

    def save(self) -> None:
        """Check the numbers; if they pass, store them, otherwise say in plain words what is wrong."""
        values, problems = parse_settings(self.stable.get(), self.timeout.get(), self.delay.get())
        if values is None:
            self.message.set("\n".join(problems))
            return
        apply_settings(self.app.cfg, values, self.gentle.get(), self.updates.get(), self.compat.get(), self.auto.get())
        self.app.save(self.app.cfg)
        self.close()

    def check_now(self) -> None:
        self.app.start_update_check(manual=True)

    def repair(self) -> None:
        self.app.repair_installation()
