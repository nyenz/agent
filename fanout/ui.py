"""Tk shell: splash, Brave-missing screen and Brave Scan screen.

Tk lives on the main thread and never blocks. Slow work (the profile scan) runs on a helper
thread that only talks to the UI through a queue, which `pump` drains every 80 ms.
"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
import webbrowser
from collections.abc import Callable
from tkinter import messagebox, ttk
from typing import Any

from . import __version__
from .brave import DOWNLOAD_URL, BraveProfile, ScanResult, scan
from .config import MAX_ACCOUNTS, Config
from .errors import FriendlyError, describe

log = logging.getLogger(__name__)

NOTICE = (
    "This drives your own logged-in accounts. Some sites' terms restrict automation - "
    "check theirs, keep volume low, prefer official APIs. You are responsible for how you use it."
)
PUMP_MS = 80
DEFAULT_PICKS = 3
COLUMNS = (
    ("use", "Use", 50),
    ("name", "Profile name", 220),
    ("folder", "Folder", 110),
    ("last", "Last used", 140),
    ("running", "Appears running?", 150),
    ("login", "Login saved?", 110),
)


def profile_key(profile: BraveProfile) -> str:
    """Stable id for a profile, used in the table and in config.json."""
    return f"{profile.channel}/{profile.folder}"


def display_name(profile: BraveProfile) -> str:
    """Profile name, with the Brave channel appended for Beta/Nightly."""
    return profile.name if profile.channel == "Brave" else f"{profile.name} ({profile.channel})"


class FanOutApp:
    """Owns the main window and switches between screens."""

    def __init__(
        self,
        root: tk.Tk,
        cfg: Config,
        save: Callable[[Config], None],
        scan_fn: Callable[[], ScanResult] = scan,
        on_screen: Callable[[str, str], None] | None = None,
    ) -> None:
        self.root = root
        self.cfg = cfg
        self.save = save
        self.scan_fn = scan_fn
        self.on_screen = on_screen
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.result: ScanResult | None = None
        self.picked: list[str] = []
        self.tree: ttk.Treeview | None = None
        self.count_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.mode_var = tk.StringVar(value=cfg.mode)
        self.accounts_var = tk.IntVar(value=cfg.num_accounts)

        root.title(f"FanOut {__version__}")
        root.geometry("940x600")
        root.minsize(760, 480)
        root.report_callback_exception = self._on_tk_error
        root.protocol("WM_DELETE_WINDOW", self.close)

        self.notice_holder = ttk.Frame(root)
        self.notice_holder.pack(fill="x")
        self.body = ttk.Frame(root, padding=16)
        self.body.pack(fill="both", expand=True)
        self._build_notice()
        self.start_scan()
        self._pump_job = root.after(PUMP_MS, self.pump)

    # ----- plumbing -------------------------------------------------------------------------

    def pump(self) -> None:
        """Drain messages from the helper thread. Runs on the Tk thread only."""
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "scan":
                    self.on_scan(payload)
                elif kind == "scan_error":
                    self.show_problem(describe(payload))
        except queue.Empty:
            pass
        finally:
            self._pump_job = self.root.after(PUMP_MS, self.pump)

    def close(self) -> None:
        """Stop the pump and close the window."""
        self.root.after_cancel(self._pump_job)
        self.root.destroy()

    def _on_tk_error(self, exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
        """Any error inside a Tk callback: log the traceback, show a plain message."""
        exc.__traceback__ = tb
        friendly = describe(exc)
        messagebox.showerror("FanOut", friendly.text())

    def _clear(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        self.tree = None

    def _announce(self, screen: str, detail: str) -> None:
        if self.on_screen:
            self.on_screen(screen, detail)

    def _build_notice(self) -> None:
        """First-run notice; stays dismissed once the user closes it."""
        if self.cfg.first_run_notice_dismissed:
            return
        bar = tk.Frame(self.notice_holder, bg="#fff4cc", padx=12, pady=8)
        bar.pack(fill="x")
        tk.Label(bar, text=NOTICE, bg="#fff4cc", wraplength=780, justify="left").pack(side="left")

        def dismiss() -> None:
            self.cfg.first_run_notice_dismissed = True
            self.save(self.cfg)
            bar.destroy()

        ttk.Button(bar, text="Dismiss", command=dismiss).pack(side="right")

    # ----- scanning -------------------------------------------------------------------------

    def start_scan(self) -> None:
        """Show the splash and scan Brave on a helper thread."""
        self.show_splash()

        def work() -> None:
            try:
                self.events.put(("scan", self.scan_fn()))
            except Exception as exc:  # noqa: BLE001 - any failure must reach the UI via the queue
                self.events.put(("scan_error", exc))

        threading.Thread(target=work, name="fanout-scan", daemon=True).start()

    def on_scan(self, result: ScanResult) -> None:
        """Choose the next screen from the scan result."""
        self.result = result
        if result.exe is None and not self.cfg.use_bundled_chromium:
            self.show_missing()
        else:
            self.show_scan()

    # ----- screens --------------------------------------------------------------------------

    def show_splash(self) -> None:
        """Startup screen while the scan runs."""
        self._clear()
        ttk.Label(self.body, text="FanOut", font=("TkDefaultFont", 26, "bold")).pack(pady=(120, 8))
        ttk.Label(self.body, text="Checking setup...", font=("TkDefaultFont", 13)).pack()

    def show_problem(self, friendly: FriendlyError) -> None:
        """A plain-English error screen with a retry button."""
        self._clear()
        ttk.Label(self.body, text=friendly.message, font=("TkDefaultFont", 14, "bold"), wraplength=700).pack(
            pady=(100, 8)
        )
        ttk.Label(self.body, text=f"What to try: {friendly.fix}", wraplength=700).pack(pady=(0, 16))
        ttk.Button(self.body, text="Try again", command=self.start_scan).pack()
        self._announce("problem", friendly.message)

    def show_missing(self) -> None:
        """Friendly screen for a computer without Brave."""
        self._clear()
        ttk.Label(self.body, text="Brave isn't installed on this computer", font=("TkDefaultFont", 16, "bold")).pack(
            pady=(90, 8)
        )
        ttk.Label(
            self.body,
            text=(
                "FanOut works best with the Brave browser. Download it (it's free), then click "
                "'Check again'. Or let FanOut use its own built-in Chromium browser instead."
            ),
            wraplength=640,
            justify="center",
        ).pack(pady=(0, 18))
        row = ttk.Frame(self.body)
        row.pack()
        ttk.Button(row, text="Download Brave", command=lambda: webbrowser.open(DOWNLOAD_URL)).pack(side="left", padx=6)
        ttk.Button(row, text="Check again", command=self.start_scan).pack(side="left", padx=6)
        ttk.Button(row, text="Use built-in Chromium instead", command=self.use_bundled).pack(side="left", padx=6)
        self._announce("brave-missing", "Brave not found")

    def use_bundled(self) -> None:
        """Remember the choice to use the bundled Chromium and continue."""
        self.cfg.use_bundled_chromium = True
        self.save(self.cfg)
        self.show_scan()

    def show_scan(self) -> None:
        """The Brave Scan screen: a table of profiles to tick."""
        assert self.result is not None
        profiles = self.result.profiles
        known = {profile_key(p) for p in profiles}
        saved = [k for k in self.cfg.selected_profiles if k in known]
        self.picked = saved or [profile_key(p) for p in profiles[:DEFAULT_PICKS]]
        self.status_var.set("")

        self._clear()
        ttk.Label(self.body, text="Choose your accounts", font=("TkDefaultFont", 16, "bold")).pack(anchor="w")
        ttk.Label(
            self.body,
            text=f"Tick up to {MAX_ACCOUNTS} Brave profiles. FanOut opens its own windows; you log in once in each.",
        ).pack(anchor="w", pady=(0, 8))

        if profiles:
            frame = ttk.Frame(self.body)
            frame.pack(fill="both", expand=True)
            tree = ttk.Treeview(frame, columns=[c[0] for c in COLUMNS], show="headings", selectmode="browse")
            for ident, title, width in COLUMNS:
                tree.heading(ident, text=title)
                tree.column(ident, width=width, anchor="center" if ident == "use" else "w")
            scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=scroll.set)
            tree.pack(side="left", fill="both", expand=True)
            scroll.pack(side="right", fill="y")
            for p in profiles:
                key = profile_key(p)
                tree.insert("", "end", iid=key, values=self._row(p, key in self.picked))
            tree.bind("<Button-1>", self._on_click)
            self.tree = tree
        else:
            ttk.Label(
                self.body,
                text="No Brave profiles were found. You can still use fresh windows and log in by hand.",
                wraplength=700,
            ).pack(anchor="w", pady=(20, 8))
            row = ttk.Frame(self.body)
            row.pack(anchor="w")
            ttk.Label(row, text="Number of accounts:").pack(side="left")
            ttk.Spinbox(row, from_=1, to=MAX_ACCOUNTS, width=4, textvariable=self.accounts_var).pack(
                side="left", padx=6
            )

        mode = ttk.LabelFrame(self.body, text="How should FanOut open Brave?", padding=8)
        mode.pack(fill="x", pady=(12, 0))
        ttk.Radiobutton(mode, text="App-managed windows (recommended)", value="app_managed", variable=self.mode_var).pack(
            anchor="w"
        )
        ttk.Radiobutton(
            mode,
            text="Reuse my real Brave profiles (advanced - coming in a later update)",
            value="reuse_profiles",
            variable=self.mode_var,
            state="disabled",
        ).pack(anchor="w")
        self.mode_var.set("app_managed")

        bar = ttk.Frame(self.body)
        bar.pack(fill="x", pady=(12, 0))
        ttk.Label(bar, textvariable=self.count_var).pack(side="left")
        ttk.Button(bar, text="Save selection", command=self.save_selection).pack(side="right")
        ttk.Button(bar, text="Rescan", command=self.start_scan).pack(side="right", padx=8)
        ttk.Label(self.body, textvariable=self.status_var, wraplength=800).pack(anchor="w", pady=(8, 0))
        ttk.Label(
            self.body,
            text="'Appears to be running' is a best guess from Brave's lock files and open programs, never certain.",
            foreground="#666666",
        ).pack(anchor="w", pady=(4, 0))
        self._update_count()
        self._announce("scan", f"{len(profiles)} profiles")

    # ----- scan-screen behaviour ------------------------------------------------------------

    def _row(self, profile: BraveProfile, ticked: bool) -> tuple[str, ...]:
        return (
            "☑" if ticked else "☐",
            display_name(profile),
            profile.folder,
            profile.last_used.strftime("%Y-%m-%d %H:%M") if profile.last_used else "unknown",
            "appears to be running" if profile.appears_running else "no",
            "probably" if profile.login_saved else "no",
        )

    def _on_click(self, event: tk.Event) -> str | None:
        assert self.tree is not None
        key = self.tree.identify_row(event.y)
        if not key:
            return None
        self.toggle(key)
        return "break"

    def toggle(self, key: str) -> None:
        """Tick or untick a profile, enforcing the maximum."""
        assert self.result is not None and self.tree is not None
        if key in self.picked:
            self.picked.remove(key)
            self.status_var.set("")
        elif len(self.picked) >= MAX_ACCOUNTS:
            self.status_var.set(f"You can pick up to {MAX_ACCOUNTS} profiles. Untick one first.")
            return
        else:
            self.picked.append(key)
            self.status_var.set("")
        profile = next(p for p in self.result.profiles if profile_key(p) == key)
        self.tree.item(key, values=self._row(profile, key in self.picked))
        self._update_count()

    def _update_count(self) -> None:
        self.count_var.set(f"{len(self.picked)} of {MAX_ACCOUNTS} selected" if self.result and self.result.profiles else "")

    def save_selection(self) -> None:
        """Store the choice. Opening the windows is the next build step."""
        assert self.result is not None
        if self.result.profiles:
            if not self.picked:
                self.status_var.set("Tick at least one profile first.")
                return
            count = len(self.picked)
        else:
            try:
                count = int(self.accounts_var.get())
            except tk.TclError:
                count = DEFAULT_PICKS
        self.cfg.num_accounts = max(1, min(MAX_ACCOUNTS, count))
        self.cfg.selected_profiles = list(self.picked)
        self.cfg.mode = "app_managed"
        self.save(self.cfg)
        self.status_var.set(
            f"Saved: {self.cfg.num_accounts} account(s). Opening the login windows arrives in the next update."
        )
