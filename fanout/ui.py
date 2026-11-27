"""Tk shell: splash, Brave-missing screen and Brave Scan screen.

Tk lives on the main thread and never blocks. Slow work (the profile scan) runs on a helper
thread that only talks to the UI through a queue, which `pump` drains every 80 ms.
"""

from __future__ import annotations

import logging
import os
import queue
import shutil
import tempfile
import threading
import tkinter as tk
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from . import __version__, updater
from .brave import DOWNLOAD_URL, BraveProfile, ScanResult, running_brave_cmdlines, scan
from .config import MAX_ACCOUNTS, Config
from .errors import FriendlyError, describe
from .mapping import Mapping, find_by_host, store_mapping
from .runview import RunView
from .sessions import SlotSpec, account_color, app_managed_specs, plan_reuse_profiles
from .settingsdialog import SettingsDialog
from .worker import Worker

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
        worker_factory: Callable[[queue.Queue[tuple[str, Any]]], Worker] = Worker,
    ) -> None:
        self.root = root
        self.cfg = cfg
        self.save = save
        self.scan_fn = scan_fn
        self.on_screen = on_screen
        self.worker_factory = worker_factory
        self.worker: Worker | None = None
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.result: ScanResult | None = None
        self.picked: list[str] = []
        self.tree: ttk.Treeview | None = None
        self.count_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.mode_var = tk.StringVar(value=cfg.mode)
        self.accounts_var = tk.IntVar(value=cfg.num_accounts)
        self.import_var = tk.BooleanVar(value=cfg.import_logins)
        self.slot_vars: dict[int, tk.StringVar] = {}
        self.slot_ready: dict[int, bool] = {}
        self.slot_notes: dict[int, str] = {}
        self.slot_map: dict[int, str] = {}  # latest mapping message per account
        self.slot_host: dict[int, str] = {}
        self.slot_buttons: dict[int, list[ttk.Button]] = {}
        self.fix: FixDialog | None = None
        self.run_view: RunView | None = None
        self.continue_button: ttk.Button | None = None
        self.pending_profiles: list[BraveProfile] = []
        self.temp_dir: Path | None = None
        self.after_windows_closed: Callable[[], None] | None = None
        self.update_info: updater.ReleaseInfo | None = None
        self.update_bar: tk.Frame | None = None
        self.settings_dialog: SettingsDialog | None = None
        self.working_on_update = False

        root.title(f"FanOut {__version__}")
        root.geometry("940x600")
        root.minsize(760, 480)
        root.report_callback_exception = self._on_tk_error
        root.protocol("WM_DELETE_WINDOW", self.close)

        self.notice_holder = ttk.Frame(root)
        self.notice_holder.pack(fill="x")
        strip = ttk.Frame(root)
        strip.pack(fill="x", padx=12, pady=(6, 0))
        ttk.Button(strip, text="Settings", command=self.open_settings).pack(side="right")
        self.update_holder = ttk.Frame(root)
        self.update_holder.pack(fill="x")
        self.body = ttk.Frame(root, padding=16)
        self.body.pack(fill="both", expand=True)
        self._build_notice()
        self.start_scan()
        self.start_update_check()
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
                elif kind in ("update_checked", "update_progress", "update_done", "repair_done"):
                    self.on_update_event(kind, payload)
                else:
                    self.on_worker_event(kind, payload)
        except queue.Empty:
            pass
        finally:
            self._pump_job = self.root.after(PUMP_MS, self.pump)

    def close(self) -> None:
        """Close every browser window cleanly, remember where they were, then close the app."""
        self.root.after_cancel(self._pump_job)
        if self.worker is not None:
            self._clear()
            ttk.Label(self.body, text="Closing browser windows...", font=("TkDefaultFont", 13)).pack(pady=140)
            self.root.update_idletasks()
            self.worker.shutdown()
            if self.worker.final_snapshot:
                self._apply_snapshot(self.worker.final_snapshot)
        self._remove_temp_dir()
        self.root.destroy()

    def _on_tk_error(self, exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
        """Any error inside a Tk callback: log the traceback, show a plain message."""
        exc.__traceback__ = tb
        friendly = describe(exc)
        messagebox.showerror("FanOut", friendly.text())

    def _clear(self) -> None:
        if self.fix is not None:
            self.fix.close()
        if self.run_view is not None:
            self.run_view.close()
            self.run_view = None
        for child in self.body.winfo_children():
            child.destroy()
        self.tree = None
        self.continue_button, self.slot_buttons = None, {}  # their widgets are gone now

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

    # ----- settings, updates, repair ---------------------------------------------------------

    def is_busy(self) -> bool:
        """True while browser windows are open (a run may be going). Updates and repairs wait for this to end."""
        return self.worker is not None

    def open_settings(self) -> None:
        if self.settings_dialog is not None and self.settings_dialog.exists():
            self.settings_dialog.lift()
            return
        self.settings_dialog = SettingsDialog(self)

    def start_update_check(self, manual: bool = False) -> None:
        """Ask GitHub on a helper thread. Silent when offline, unless the user pressed the button."""
        if not manual and not self.cfg.check_updates:
            return
        if not manual and os.environ.get("FANOUT_NO_UPDATE_CHECK"):
            return  # tests and demos stay off the network
        # The helper thread must not hold on to `self`: if it dropped the last reference, Tk objects would be freed
        # on the wrong thread, which aborts the whole program on macOS.
        events, skipped = self.events, "" if manual else self.cfg.skipped_version

        def work() -> None:
            events.put(("update_checked", (updater.check_for_update(__version__, skipped), manual)))

        threading.Thread(target=work, name="fanout-update-check", daemon=True).start()

    def on_update_event(self, kind: str, payload: Any) -> None:
        if kind == "update_checked":
            info, manual = payload
            if info is not None:
                self.show_update_banner(info)
            elif manual:
                messagebox.showinfo("FanOut", f"You have the newest version ({__version__}), or I couldn't reach GitHub just now.")
        elif kind == "update_progress":
            self._set_update_text(str(payload))
        elif kind == "update_done":
            self.working_on_update = False
            new_version, problem = payload
            if problem:
                messagebox.showerror("FanOut update", problem)
                if self.update_info is not None:
                    self.show_update_banner(self.update_info)
                return
            messagebox.showinfo("FanOut update", f"FanOut {new_version} is installed. It will restart now.")
            updater.restart()
            self.close()
        elif kind == "repair_done":
            self.working_on_update = False
            ok, tail = payload
            if ok:
                messagebox.showinfo("FanOut", "Repair finished. Everything FanOut needs is in place.")
            else:
                messagebox.showerror("FanOut", f"The repair didn't finish.\n\n{tail}\n\nCheck your internet connection and try again.")

    def hide_update_banner(self) -> None:
        if self.update_bar is not None:
            self.update_bar.destroy()
            self.update_bar = None

    def show_update_banner(self, info: updater.ReleaseInfo) -> None:
        """Non-blocking bar: Update now / Later / Skip this version."""
        self.update_info = info
        self.hide_update_banner()
        bar = tk.Frame(self.update_holder, bg="#d9f2e0", padx=12, pady=8)
        bar.pack(fill="x")
        self.update_bar = bar
        self.update_text = tk.StringVar(value=f"FanOut {info.tag.lstrip('v')} is available (you have {__version__}).")
        tk.Label(bar, textvariable=self.update_text, bg="#d9f2e0").pack(side="left")
        row = tk.Frame(bar, bg="#d9f2e0")
        row.pack(side="right")
        ttk.Button(row, text="Update now", command=self.update_now).pack(side="left", padx=4)
        ttk.Button(row, text="Later", command=self.hide_update_banner).pack(side="left", padx=4)
        ttk.Button(row, text="Skip this version", command=self.skip_update).pack(side="left", padx=4)

    def _set_update_text(self, text: str) -> None:
        if self.update_bar is not None:
            self.update_text.set(text)

    def skip_update(self) -> None:
        if self.update_info is not None:
            self.cfg.skipped_version = self.update_info.tag.lstrip("v")
            self.save(self.cfg)
        self.hide_update_banner()

    def update_now(self) -> None:
        """Download, verify and install. Never while browser windows are open or another job is running."""
        info = self.update_info
        if info is None or self.working_on_update:
            return
        if updater.is_frozen():
            webbrowser.open(info.page_url)  # a packaged app can't swap its own files: send them to the download
            return
        if self.is_busy():
            messagebox.showinfo(
                "FanOut", "Please finish or cancel the current run and close the browser windows first. Then press Update now again."
            )
            return
        if not messagebox.askyesno(
            "Update FanOut", f"Install FanOut {info.tag.lstrip('v')} now? Your settings, logins and results are kept. FanOut will restart."
        ):
            return
        self.working_on_update = True
        self._set_update_text("Starting the update...")
        events = self.events  # not `self`: see start_update_check

        def work() -> None:
            try:
                new = updater.perform_update(info, progress=lambda t: events.put(("update_progress", t)))
                events.put(("update_done", (new, "")))
            except updater.UpdateError as exc:
                events.put(("update_done", ("", str(exc))))
            except Exception as exc:  # the traceback goes to the log (describe), the person sees plain words
                events.put(("update_done", ("", describe(exc).text())))

        threading.Thread(target=work, name="fanout-update", daemon=True).start()

    def repair_installation(self) -> None:
        """Rebuild anything missing (packages, built-in browser). Settings and results are untouched."""
        if updater.is_frozen():
            messagebox.showinfo("FanOut", "This packaged version can't repair itself. Download it again from the FanOut Releases page.")
            return
        if self.is_busy() or self.working_on_update:
            messagebox.showinfo("FanOut", "Please finish or cancel the current run and close the browser windows first, then try again.")
            return
        question = "Check and rebuild FanOut's packages and built-in browser? This can take a few minutes."
        if not messagebox.askyesno("Repair FanOut", question):
            return
        self.working_on_update = True
        events = self.events  # not `self`: see start_update_check

        def work() -> None:
            events.put(("repair_done", updater.repair_installation()))

        threading.Thread(target=work, name="fanout-repair", daemon=True).start()

    # ----- scanning -------------------------------------------------------------------------

    def start_scan(self) -> None:
        """Show the splash and scan Brave on a helper thread."""
        self.show_splash()
        events, scan_fn = self.events, self.scan_fn  # not `self`: see start_update_check

        def work() -> None:
            try:
                events.put(("scan", scan_fn()))
            except Exception as exc:  # any failure must reach the UI via the queue
                events.put(("scan_error", exc))

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
        self.mode_var.set("reuse_profiles" if self.cfg.mode == "reuse_profiles" else "app_managed")

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
            mode, text="Reuse my real Brave profiles (advanced)", value="reuse_profiles", variable=self.mode_var
        ).pack(anchor="w")
        ttk.Checkbutton(
            mode,
            text="Try to copy my existing logins into the new windows (optional; if it fails you just log in once)",
            variable=self.import_var,
        ).pack(anchor="w", pady=(6, 0))

        bar = ttk.Frame(self.body)
        bar.pack(fill="x", pady=(12, 0))
        ttk.Label(bar, textvariable=self.count_var).pack(side="left")
        ttk.Button(bar, text="Continue", command=self.continue_clicked).pack(side="right")
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

    def _picked_profiles(self) -> list[BraveProfile]:
        """Ticked profiles, in the order they were ticked."""
        assert self.result is not None
        by_key = {profile_key(p): p for p in self.result.profiles}
        return [by_key[k] for k in self.picked if k in by_key]

    def save_selection(self) -> int | None:
        """Store the choice and return the number of accounts, or None if it can't continue."""
        assert self.result is not None
        if self.result.profiles:
            if not self.picked:
                self.status_var.set("Tick at least one profile first.")
                return None
            count = len(self.picked)
        else:
            if self.mode_var.get() == "reuse_profiles":
                self.status_var.set("Reusing real profiles needs Brave profiles to pick from.")
                return None
            try:
                count = int(self.accounts_var.get())
            except tk.TclError:
                count = DEFAULT_PICKS
        self.cfg.num_accounts = max(1, min(MAX_ACCOUNTS, count))
        self.cfg.selected_profiles = list(self.picked)
        self.cfg.mode = self.mode_var.get()
        self.cfg.import_logins = bool(self.import_var.get())
        self.save(self.cfg)
        return self.cfg.num_accounts

    # ----- opening windows ------------------------------------------------------------------

    def continue_clicked(self) -> None:
        """Open the account windows in the chosen mode."""
        count = self.save_selection()
        if count is None:
            return
        profiles = self._picked_profiles()
        if self.mode_var.get() == "reuse_profiles":
            if running_brave_cmdlines():
                choice = self._ask_brave_open()
                if choice == "cancel":
                    return
                if choice == "close":
                    self.pending_profiles = profiles
                    self.show_splash()
                    self._submit("close_brave")
                    return
                self.mode_var.set("app_managed")
                self.cfg.mode = "app_managed"
                self.save(self.cfg)
            else:
                self._confirm_and_reuse(profiles)
                return
        sources = [p.path for p in profiles] if self.cfg.import_logins else None
        self._open(app_managed_specs(count, sources))

    def _confirm_and_reuse(self, profiles: list[BraveProfile]) -> None:
        """Warn that real profiles will be changed, then open them."""
        ok = messagebox.askokcancel(
            "Use my real Brave profiles",
            "Changes you make in these windows (logins, history, settings) affect your real Brave "
            "profiles. FanOut never reads or stores your login details.\n\nContinue?",
        )
        if not ok:
            self.show_scan()
            return
        self.temp_dir = Path(tempfile.mkdtemp(prefix="fanout-"))
        self._open(plan_reuse_profiles(profiles, self.temp_dir))

    def _ask_brave_open(self) -> str:
        """Blocking three-way dialog. Returns 'close', 'managed' or 'cancel'."""
        choice = tk.StringVar(value="cancel")
        dialog = tk.Toplevel(self.root)
        dialog.title("Brave is open")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        ttk.Label(
            dialog,
            text=(
                "Brave looks like it's running. To reuse your real profiles, Brave has to be closed first.\n"
                "FanOut can ask it to close politely (it never forces it), or use its own windows instead."
            ),
            wraplength=460,
            padding=16,
        ).pack()
        row = ttk.Frame(dialog, padding=(16, 0, 16, 16))
        row.pack()
        for label, value in (
            ("Close Brave for me", "close"),
            ("Use app-managed windows instead", "managed"),
            ("Cancel", "cancel"),
        ):
            ttk.Button(row, text=label, command=lambda v=value: (choice.set(v), dialog.destroy())).pack(
                side="left", padx=4
            )
        dialog.protocol("WM_DELETE_WINDOW", lambda: (choice.set("cancel"), dialog.destroy()))
        dialog.grab_set()
        self.root.wait_window(dialog)
        return choice.get()

    def _exe(self) -> Path | None:
        """Brave, unless the person chose the built-in Chromium (or there is no Brave)."""
        if self.cfg.use_bundled_chromium or self.result is None:
            return None
        return self.result.exe

    def _submit(self, command: str, **payload: object) -> None:
        """Send a command to the worker, starting it on first use."""
        if self.worker is None:
            self.worker = self.worker_factory(self.events)
            self.worker.start()
        self.worker.submit(command, **payload)

    def _open(self, specs: list[SlotSpec]) -> None:
        self.show_login(len(specs))
        self._submit(
            "open_windows",
            specs=specs,
            exe=self._exe(),
            screen=(self.root.winfo_screenwidth(), self.root.winfo_screenheight()),
            geometry=dict(self.cfg.window_geometry),
            last_urls=dict(self.cfg.last_urls),
        )

    # ----- login screen ---------------------------------------------------------------------

    def show_login(self, count: int) -> None:
        """One row per account; READY appears when that window shows a chat input."""
        self._clear()
        self.slot_vars, self.slot_ready, self.slot_notes = {}, {}, {}
        self.slot_map, self.slot_host, self.slot_buttons = {}, {}, {}
        ttk.Label(self.body, text="Log in to a chat site in each window", font=("TkDefaultFont", 16, "bold")).pack(
            anchor="w"
        )
        ttk.Label(
            self.body,
            text="Use a different site in each window and log in yourself. 'READY' appears when the chat box is found.",
            wraplength=800,
        ).pack(anchor="w", pady=(0, 12))
        for number in range(1, count + 1):
            row = ttk.Frame(self.body)
            row.pack(fill="x", pady=4)
            tk.Label(row, text=f" ACCOUNT {number} ", bg=account_color(number), fg="white", width=14).pack(side="left")
            var = tk.StringVar(value="Opening window...")
            self.slot_vars[number] = var
            self.slot_ready[number] = False
            ttk.Label(row, textvariable=var, wraplength=480, justify="left").pack(side="left", padx=12)
            fix_button = ttk.Button(row, text="Fix by clicking", command=lambda n=number: self.open_fix(n), state="disabled")
            fix_button.pack(side="right", padx=(6, 0))
            map_button = ttk.Button(row, text="Map this account", command=lambda n=number: self.map_clicked(n), state="disabled")
            map_button.pack(side="right")
            self.slot_buttons[number] = [map_button, fix_button]
        bar = ttk.Frame(self.body)
        bar.pack(fill="x", pady=(18, 0))
        self.continue_button = ttk.Button(bar, text="All ready - continue", command=self.finish_login, state="disabled")
        self.continue_button.pack(side="right")
        ttk.Button(bar, text="Close windows and go back", command=self.back_to_scan).pack(side="right", padx=8)
        self._announce("login", f"{count} windows")

    def _set_slot(self, number: int, text: str) -> None:
        var = self.slot_vars.get(number)
        if var is not None:
            extras = [t for t in (self.slot_notes.get(number), self.slot_map.get(number)) if t]
            var.set("\n".join([text, *extras]))

    def _enable_mapping(self, number: int, enabled: bool) -> None:
        for button in self.slot_buttons.get(number, []):
            button.configure(state="normal" if enabled else "disabled")

    def map_clicked(self, number: int) -> None:
        """Find this account's chat box and prove it with a harmless test (never sends anything)."""
        self.slot_map[number] = "Checking the chat box..."
        self._set_slot(number, self.slot_vars[number].get().split("\n")[0])
        self._submit("map_account", slot=number)

    def open_fix(self, number: int) -> None:
        """Open the small window for pointing at things by clicking."""
        if self.fix is not None:
            self.fix.close()
        saved = find_by_host(self.cfg.mappings, number, self.slot_host.get(number, ""))
        self.fix = FixDialog(self, number, saved or Mapping())

    def _on_map_result(self, slot: int, data: dict[str, object]) -> None:
        ok, origin = bool(data["ok"]), str(data.get("origin") or "")
        mapping = Mapping.from_dict(data.get("mapping"))  # type: ignore[arg-type]
        notes = [str(n) for n in data.get("notes") or []]  # type: ignore[attr-defined]
        if ok and origin:
            store_mapping(self.cfg.mappings, slot, origin, mapping)
            self.save(self.cfg)
        lines = [str(data["message"])] if ok else [str(data["message"]), f"What to try: {data['fix']}"]
        self.slot_map[slot] = "\n".join(lines + notes)
        if slot in self.slot_vars:
            self._set_slot(slot, self.slot_vars[slot].get().split("\n")[0])
        if self.fix is not None and self.fix.slot == slot:
            self.fix.on_map(ok, self.slot_map[slot], mapping)

    def _refresh_continue(self) -> None:
        if self.continue_button is not None:
            ready = any(self.slot_ready.values())
            self.continue_button.configure(state="normal" if ready else "disabled")

    def on_worker_event(self, kind: str, data: dict[str, object]) -> None:
        """Apply an event from the browser helper thread (runs on the Tk thread)."""
        slot = int(data.get("slot", 0))  # type: ignore[call-overload]
        if kind.startswith("run_"):
            if self.run_view is not None:
                self.run_view.on_event(kind, data)
            return
        if kind == "slot_open":
            self._set_slot(slot, "Window open. Go to your chat site there and log in.")
        elif kind == "slot_note":
            self.slot_notes[slot] = str(data["message"])
            var = self.slot_vars.get(slot)
            if var is not None:
                self._set_slot(slot, var.get().split("\n")[0])
        elif kind == "slot_state":
            ready, host = bool(data["ready"]), str(data.get("host") or "")
            self.slot_ready[slot] = ready
            self.slot_host[slot] = host
            self._enable_mapping(slot, ready)
            if ready and slot not in self.slot_map and find_by_host(self.cfg.mappings, slot, host):
                self.slot_map[slot] = "Chat box already mapped from last time."
            if ready:
                self._set_slot(slot, f"READY - {host}" if host else "READY")
            else:
                self._set_slot(slot, f"Waiting for you to log in... ({host})" if host else "Waiting for you to log in...")
            self._refresh_continue()
        elif kind == "map_result":
            self._on_map_result(slot, data)
        elif kind == "pick_result":
            if self.fix is not None and self.fix.slot == slot:
                self.fix.on_pick(str(data["pick_kind"]), bool(data["ok"]), str(data["selector"]))
        elif kind == "slot_error":
            self.slot_ready[slot] = False
            self._enable_mapping(slot, False)
            self._set_slot(slot, f"Problem: {data['message']}\nWhat to try: {data['fix']}")
            self._refresh_continue()
        elif kind == "slot_closed":
            self.slot_ready[slot] = False
            self._enable_mapping(slot, False)
            self._set_slot(slot, "This window was closed.")
            self._refresh_continue()
        elif kind == "snapshot":
            self._apply_snapshot(data)
        elif kind == "brave_closed":
            self._on_brave_closed(bool(data["ok"]))
        elif kind == "windows_closed":
            self._remove_temp_dir()
            callback, self.after_windows_closed = self.after_windows_closed, None
            if callback:
                callback()
        elif kind == "worker_error":
            messagebox.showerror("FanOut", f"{data['message']}\n\nWhat to try: {data['fix']}")
            self.show_scan()

    def _on_brave_closed(self, ok: bool) -> None:
        if ok:
            self._confirm_and_reuse(self.pending_profiles)
        else:
            messagebox.showinfo(
                "Brave is still open",
                "Brave didn't close. Please close it yourself (check the system tray), then click Continue again.",
            )
            self.show_scan()

    def _apply_snapshot(self, data: dict[str, object]) -> None:
        """Remember window positions and sites (origin only) for next launch."""
        geometry, urls = data.get("window_geometry"), data.get("last_urls")
        if isinstance(geometry, dict):
            self.cfg.window_geometry.update(geometry)
        if isinstance(urls, dict):
            self.cfg.last_urls.update(urls)
        self.save(self.cfg)

    def _remove_temp_dir(self) -> None:
        if self.temp_dir is not None:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            self.temp_dir = None

    def finish_login(self) -> None:
        """Continue with however many accounts are READY."""
        ready = sorted(n for n, ok in self.slot_ready.items() if ok)
        self._submit("snapshot")
        self._clear()
        self.run_view = RunView(self, ready)
        self._announce("logins-ready", f"{len(ready)} ready")

    def back_to_scan(self) -> None:
        """Close the browser windows, then return to the Scan screen."""
        if self.worker is None:
            self.show_scan()
            return
        self.after_windows_closed = self.show_scan
        self.show_splash()
        self._submit("close_windows")


class FixDialog:
    """Small window for pointing at the chat box, send button and reply text by clicking them."""

    FIELDS = (
        ("input_selector", "input", "chat box"),
        ("send_selector", "send", "send button"),
        ("message_selector", "message", "reply text"),
    )

    def __init__(self, app: FanOutApp, slot: int, current: Mapping) -> None:
        self.app, self.slot = app, slot
        self.window = tk.Toplevel(app.root)
        self.window.title(f"Fix ACCOUNT {slot}")
        self.window.transient(app.root)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.vars = {key: tk.StringVar(value=getattr(current, key)) for key, _, _ in self.FIELDS}
        self.marks = {key: tk.StringVar() for key, _, _ in self.FIELDS}
        self.status = tk.StringVar(value="Press a button, then click that thing in the browser window.")
        frame = ttk.Frame(self.window, padding=14)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text=f" ACCOUNT {slot} ", bg=account_color(slot), fg="white").pack(anchor="w")
        ttk.Label(frame, textvariable=self.status, wraplength=420, justify="left").pack(anchor="w", pady=(8, 10))
        for key, kind, label in self.FIELDS:
            row = ttk.Frame(frame)
            row.pack(fill="x", pady=3)
            ttk.Button(row, text=f"Pick the {label}", width=22, command=lambda k=kind: self.pick(k)).pack(side="left")
            ttk.Label(row, textvariable=self.marks[key]).pack(side="left", padx=10)
            self.vars[key].trace_add("write", lambda *_: self._refresh())
        self.advanced = ttk.Frame(frame)
        for key, _, label in self.FIELDS:
            line = ttk.Frame(self.advanced)
            line.pack(fill="x", pady=2)
            ttk.Label(line, text=label.capitalize(), width=12).pack(side="left")
            ttk.Entry(line, textvariable=self.vars[key], width=44).pack(side="left")
        self.toggle = ttk.Button(frame, text="Advanced (type it in)", command=self._toggle_advanced)
        self.toggle.pack(anchor="w", pady=(8, 0))
        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=(14, 0))
        ttk.Button(bar, text="Close", command=self.close).pack(side="right")
        ttk.Button(bar, text="Test and save", command=self.test).pack(side="right", padx=8)
        self._refresh()

    def _refresh(self) -> None:
        for key, var in self.vars.items():
            self.marks[key].set("set" if var.get().strip() else "not set")

    def _toggle_advanced(self) -> None:
        if self.advanced.winfo_ismapped():
            self.advanced.pack_forget()
        else:
            self.advanced.pack(fill="x", after=self.toggle)

    def current(self) -> Mapping:
        """What is typed or picked right now."""
        return Mapping(**{key: var.get().strip() for key, var in self.vars.items()})

    def pick(self, kind: str) -> None:
        """Ask the browser window to let the person click the element."""
        label = next(label for _, k, label in self.FIELDS if k == kind)
        self.status.set(f"Now click the {label} in the ACCOUNT {self.slot} window. Press Esc there to cancel.")
        self.app._submit("pick_element", slot=self.slot, kind=kind)

    def on_pick(self, kind: str, ok: bool, selector: str) -> None:
        """The person clicked (or cancelled)."""
        if ok and selector:
            key = next(key for key, k, _ in self.FIELDS if k == kind)
            self.vars[key].set(selector)
            self.status.set("Got it. Pick the next one, or press 'Test and save'.")
        else:
            self.status.set("Nothing was picked.")

    def test(self) -> None:
        """Try the current selectors with a harmless test (types a test word, then clears it)."""
        self.status.set("Testing...")
        self.app._submit("map_account", slot=self.slot, mapping=self.current().to_dict())

    def on_map(self, ok: bool, text: str, mapping: Mapping) -> None:
        """Show the test result; keep any selectors the test filled in."""
        self.status.set(text)
        if ok:
            for key, var in self.vars.items():
                var.set(getattr(mapping, key))

    def close(self) -> None:
        """Close the window."""
        if self.app.fix is self:
            self.app.fix = None
        if self.window.winfo_exists():
            self.window.destroy()
