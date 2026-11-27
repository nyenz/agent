"""The prompt screen and the results screens (a tab per account plus a side-by-side Compare tab).

Runs on the Tk thread only. It never touches a browser: it sends commands through the app
(`send_all`, `cancel_run`) and shows the events that come back.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any

from .config import app_data_dir
from .engine import RunSettings
from .mapping import find_by_host
from .results import (
    AccountResult,
    PromptHistory,
    best_answer,
    copy_all_text,
    export_json,
    export_markdown,
    write_text_atomic,
)
from .sessions import account_color

if TYPE_CHECKING:
    from .ui import FanOutApp

BG, PANEL, FIELD, FG, MUTED, ACCENT = "#1d1f24", "#262930", "#14161a", "#ececf0", "#9a9da8", "#2f6fed"
BAD = "#ff8a80"
FAILED = ("error", "timeout", "cancelled")
STATE_WORDS = {
    "waiting": "waiting", "idle": "idle", "typing": "typing", "sent": "sent", "streaming": "streaming",
    "done": "done", "error": "error", "timeout": "timed out", "cancelled": "cancelled",
}
COLUMN_WIDTH = 360


def copy_to_clipboard(root: tk.Misc, text: str) -> None:
    """Put text on the clipboard: pyperclip if it is installed, else Tk's own clipboard."""
    try:
        import pyperclip

        pyperclip.copy(text)
    except Exception:
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()


def set_text(widget: tk.Text, text: str, bad: bool = False) -> None:
    """Replace the text of a read-only Text widget."""
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", text, ("bad",) if bad else ())
    widget.configure(state="disabled")


def result_text(result: AccountResult) -> str:
    """What to show for one account: the answer, or what went wrong and what to try."""
    if result.ok:
        return best_answer(result)
    parts = [result.message or result.state]
    if result.fix:
        parts.append(f"What to try: {result.fix}")
    if result.text:
        parts.append(f"--- What arrived before it stopped ---\n{result.text}")
    return "\n\n".join(parts)


class RunView:
    """Prompt boxes on top, results underneath."""

    def __init__(self, app: FanOutApp, slots: list[int]) -> None:
        self.app, self.root, self.slots = app, app.root, slots
        self.results: dict[int, AccountResult] = {}
        self.running = False
        self.history = PromptHistory(app_data_dir() / "history.json")
        self.status: dict[int, tk.StringVar] = {}
        self.prompts: dict[int, tk.Text] = {}
        self.tab_text: dict[int, tk.Text] = {}
        self.tab_info: dict[int, tk.StringVar] = {}
        self.column_text: dict[int, tk.Text] = {}
        self.column_info: dict[int, tk.StringVar] = {}
        self.note = tk.StringVar()
        self.summary = tk.StringVar(value="Type a prompt for each account, or one for everyone, then press SEND TO ALL.")
        self._note_job: str | None = None
        self._keys: list[str] = []
        self.style = ttk.Style()
        self.original_theme = self.style.theme_use()
        self._apply_theme()
        self.root.geometry("1120x820")
        self.root.minsize(900, 640)
        self._build()
        self._bind_keys()

    # ----- look -----------------------------------------------------------------------------

    def _apply_theme(self) -> None:
        s = self.style
        s.theme_use("clam")
        s.configure("Run.TFrame", background=BG)
        s.configure("Run.TLabel", background=BG, foreground=FG)
        s.configure("Run.Muted.TLabel", background=BG, foreground=MUTED)
        s.configure("Run.TButton", background=PANEL, foreground=FG, bordercolor=PANEL, focuscolor=PANEL, padding=(10, 5))
        s.map("Run.TButton", background=[("active", "#33363f"), ("disabled", BG)], foreground=[("disabled", MUTED)])
        s.configure("Run.Accent.TButton", background=ACCENT, foreground="white", bordercolor=ACCENT, padding=(16, 7))
        s.map("Run.Accent.TButton", background=[("active", "#3d7bff"), ("disabled", "#3a3f4d")], foreground=[("disabled", MUTED)])
        s.configure("Run.TNotebook", background=BG, borderwidth=0)
        s.configure("Run.TNotebook.Tab", background=PANEL, foreground=FG, padding=(12, 5))
        s.map("Run.TNotebook.Tab", background=[("selected", "#3a3d47")])
        s.configure("Run.Vertical.TScrollbar", background=PANEL, troughcolor=BG, bordercolor=BG, arrowcolor=FG)
        s.configure("Run.Horizontal.TScrollbar", background=PANEL, troughcolor=BG, bordercolor=BG, arrowcolor=FG)
        self.app.body.configure(style="Run.TFrame")

    def _text(self, parent: tk.Misc, height: int, readonly: bool = False) -> tk.Text:
        box = tk.Text(
            parent, height=height, wrap="word", bg=FIELD, fg=FG, insertbackground=FG, relief="flat", padx=8, pady=6,
            highlightthickness=1, highlightbackground=PANEL, highlightcolor=ACCENT, undo=not readonly,
            selectbackground=ACCENT, font=("TkDefaultFont", 10),
        )
        box.tag_configure("bad", foreground=BAD)
        if readonly:
            box.configure(state="disabled")
        return box

    def _button(self, parent: tk.Misc, text: str, command: Any, accent: bool = False) -> ttk.Button:
        return ttk.Button(parent, text=text, command=command, style="Run.Accent.TButton" if accent else "Run.TButton")

    # ----- building -------------------------------------------------------------------------

    def _build(self) -> None:
        body = self.app.body
        head = ttk.Frame(body, style="Run.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text="Ask every account", style="Run.TLabel", font=("TkDefaultFont", 15, "bold")).pack(side="left")
        self._button(head, "Close windows and go back", self.app.back_to_scan).pack(side="right")
        self._button(head, "History", self.open_history).pack(side="right", padx=8)

        ttk.Label(body, text="Same prompt for everyone (optional)", style="Run.Muted.TLabel").pack(anchor="w", pady=(10, 2))
        row = ttk.Frame(body, style="Run.TFrame")
        row.pack(fill="x")
        self.broadcast = self._text(row, 2)
        self.broadcast.pack(side="left", fill="x", expand=True)
        self.broadcast.bind("<Control-Return>", lambda _e: self.send())
        self._button(row, "Fill every box", self.fill_all).pack(side="left", padx=(8, 0))

        grid = ttk.Frame(body, style="Run.TFrame")
        grid.pack(fill="x", pady=10)
        for index, slot in enumerate(self.slots):
            grid.columnconfigure(index % 3, weight=1, uniform="cell")
            cell = tk.Frame(grid, bg=PANEL)
            cell.grid(row=index // 3, column=index % 3, sticky="nsew", padx=4, pady=4)
            site = self.app.slot_host.get(slot, "")
            tk.Label(cell, text=f" ACCOUNT {slot} - {site} " if site else f" ACCOUNT {slot} ", bg=account_color(slot),
                     fg="white", anchor="w").pack(fill="x")
            box = self._text(cell, 3)
            box.pack(fill="x", padx=6, pady=(6, 2))
            box.bind("<Control-Return>", lambda _e: self.send())
            self.prompts[slot] = box
            self.status[slot] = tk.StringVar(value="idle")
            tk.Label(cell, textvariable=self.status[slot], bg=PANEL, fg=MUTED, anchor="w").pack(fill="x", padx=8, pady=(0, 4))

        bar = ttk.Frame(body, style="Run.TFrame")
        bar.pack(fill="x")
        self.send_button = self._button(bar, "SEND TO ALL  (Ctrl+Enter)", self.send, accent=True)
        self.send_button.pack(side="left")
        self.cancel_button = self._button(bar, "Cancel  (Esc)", self.cancel)
        self.cancel_button.pack(side="left", padx=8)
        self.cancel_button.state(["disabled"])
        ttk.Label(bar, textvariable=self.summary, style="Run.Muted.TLabel").pack(side="left", padx=8)

        self.tabs = ttk.Notebook(body, style="Run.TNotebook")
        self.tabs.pack(fill="both", expand=True, pady=(10, 0))
        for slot in self.slots:
            self._build_account_tab(slot)
        self._build_compare_tab()

        foot = ttk.Frame(body, style="Run.TFrame")
        foot.pack(fill="x", pady=(8, 0))
        self._button(foot, "Copy all", self.copy_all).pack(side="left")
        self._button(foot, "Export .md", lambda: self.export("md")).pack(side="left", padx=6)
        self._button(foot, "Export .json", lambda: self.export("json")).pack(side="left")
        self.retry_button = self._button(foot, "Retry failed", self.retry_failed)
        self.retry_button.pack(side="left", padx=6)
        self.retry_button.state(["disabled"])
        self._button(foot, "Clear", self.clear).pack(side="left")
        ttk.Label(foot, textvariable=self.note, style="Run.Muted.TLabel").pack(side="left", padx=12)

    def _build_account_tab(self, slot: int) -> None:
        frame = ttk.Frame(self.tabs, style="Run.TFrame", padding=8)
        self.tabs.add(frame, text=f"Account {slot}")
        self.tab_info[slot] = tk.StringVar(value="No answer yet.")
        top = ttk.Frame(frame, style="Run.TFrame")
        top.pack(fill="x", pady=(0, 6))
        ttk.Label(top, textvariable=self.tab_info[slot], style="Run.Muted.TLabel").pack(side="left")
        self._button(top, "Copy this", lambda s=slot: self.copy_one(s)).pack(side="right")
        holder = ttk.Frame(frame, style="Run.TFrame")
        holder.pack(fill="both", expand=True)
        box = self._text(holder, 8, readonly=True)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=box.yview, style="Run.Vertical.TScrollbar")
        box.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        box.pack(side="left", fill="both", expand=True)
        self.tab_text[slot] = box

    def _build_compare_tab(self) -> None:
        frame = ttk.Frame(self.tabs, style="Run.TFrame", padding=8)
        self.tabs.add(frame, text="Compare")
        canvas = tk.Canvas(frame, bg=BG, highlightthickness=0)
        across = ttk.Scrollbar(frame, orient="horizontal", command=canvas.xview, style="Run.Horizontal.TScrollbar")
        canvas.configure(xscrollcommand=across.set)
        across.pack(side="bottom", fill="x")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg=BG)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, height=e.height))
        for slot in self.slots:
            column = tk.Frame(inner, bg=PANEL, width=COLUMN_WIDTH)
            column.pack(side="left", fill="y", padx=4)
            column.pack_propagate(False)
            site = self.app.slot_host.get(slot, "")
            tk.Label(column, text=f" ACCOUNT {slot} - {site} " if site else f" ACCOUNT {slot} ", bg=account_color(slot),
                     fg="white", anchor="w").pack(fill="x")
            self.column_info[slot] = tk.StringVar(value="No answer yet.")
            tk.Label(column, textvariable=self.column_info[slot], bg=PANEL, fg=MUTED, anchor="w").pack(fill="x", padx=8, pady=4)
            self._button(column, "Copy this", lambda s=slot: self.copy_one(s)).pack(side="bottom", pady=6)
            holder = tk.Frame(column, bg=PANEL)
            holder.pack(fill="both", expand=True, padx=6)
            box = self._text(holder, 8, readonly=True)
            scroll = ttk.Scrollbar(holder, orient="vertical", command=box.yview, style="Run.Vertical.TScrollbar")
            box.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            box.pack(side="left", fill="both", expand=True)
            self.column_text[slot] = box

    def _bind_keys(self) -> None:
        for sequence, action in (
            ("<Control-Return>", self.send),
            ("<Control-Shift-C>", self.copy_all),
            ("<Control-Shift-c>", self.copy_all),
            ("<Escape>", self.cancel),
        ):
            self.root.bind(sequence, lambda _e, a=action: a())
            self._keys.append(sequence)

    def close(self) -> None:
        """Leave this screen: drop the key shortcuts and put the normal look back."""
        for sequence in self._keys:
            self.root.unbind(sequence)
        self._keys = []
        if self._note_job is not None:
            self.root.after_cancel(self._note_job)
        self.app.body.configure(style="TFrame")
        self.style.theme_use(self.original_theme)

    # ----- actions --------------------------------------------------------------------------

    def fill_all(self) -> None:
        """Copy the 'same prompt for everyone' text into every account's box."""
        text = self.broadcast.get("1.0", "end").strip()
        for box in self.prompts.values():
            box.delete("1.0", "end")
            box.insert("1.0", text)

    def _job(self, slot: int, prompt: str) -> dict[str, Any]:
        host = self.app.slot_host.get(slot, "")
        mapping = find_by_host(self.app.cfg.mappings, slot, host)
        return {"slot": slot, "prompt": prompt, "site": host, "mapping": mapping.to_dict() if mapping else {}}

    def send(self) -> str:
        """SEND TO ALL: every account that has a prompt (its own, else the shared one)."""
        if self.running:
            return "break"
        shared = self.broadcast.get("1.0", "end").strip()
        jobs = []
        for slot in self.slots:
            prompt = self.prompts[slot].get("1.0", "end").strip() or shared
            if prompt:
                jobs.append(self._job(slot, prompt))
        if not jobs:
            messagebox.showinfo("FanOut", "Type a prompt first: one for each account, or one for everyone.")
            return "break"
        self._start(jobs)
        return "break"

    def _start(self, jobs: list[dict[str, Any]]) -> None:
        for job in jobs:
            self.history.add(job["prompt"])
        self.begin([int(j["slot"]) for j in jobs])
        self.app._submit("send_all", jobs=jobs, settings=asdict(RunSettings.from_config(self.app.cfg)))

    def cancel(self) -> str:
        """Stop waiting for the answers (the windows stay open)."""
        if self.running:
            self.summary.set("Cancelling...")
            self.app._submit("cancel_run")
        return "break"

    def retry_failed(self) -> None:
        """Send again to only the accounts that failed, timed out or were cancelled."""
        jobs = [self._job(r.slot, r.prompt) for r in self.results.values() if r.state in FAILED]
        if jobs and not self.running:
            self._start(jobs)

    def clear(self) -> None:
        """Forget the shown answers (the prompts stay)."""
        if self.running:
            return
        self.results.clear()
        for slot in self.slots:
            self._show_empty(slot)
            self.status[slot].set("idle")
        self.retry_button.state(["disabled"])
        self.summary.set("Cleared.")

    def _flash(self, text: str) -> None:
        self.note.set(text)
        if self._note_job is not None:
            self.root.after_cancel(self._note_job)
        self._note_job = self.root.after(3000, lambda: self.note.set(""))

    def copy_one(self, slot: int) -> None:
        """Copy one account's answer."""
        result = self.results.get(slot)
        if result is None or not (result.text or result.markdown):
            self._flash(f"Account {slot} has no answer to copy yet.")
            return
        copy_to_clipboard(self.root, best_answer(result))
        self._flash(f"Copied Account {slot}.")

    def copy_all(self) -> str:
        """Copy every answer, each under '=== Account N - site ==='."""
        if not self.results:
            self._flash("Nothing to copy yet.")
            return "break"
        copy_to_clipboard(self.root, copy_all_text(list(self.results.values())))
        self._flash("Copied all answers.")
        return "break"

    def export(self, kind: str) -> None:
        """Save every answer as .md or .json (asks where)."""
        if not self.results:
            messagebox.showinfo("FanOut", "There is nothing to save yet.")
            return
        results = list(self.results.values())
        text = export_markdown(results) if kind == "md" else export_json(results)
        chosen = filedialog.asksaveasfilename(
            defaultextension=f".{kind}", initialfile=f"fanout-results.{kind}",
            filetypes=[("Markdown" if kind == "md" else "JSON", f"*.{kind}")],
        )
        if chosen:
            write_text_atomic(Path(chosen), text)
            self._flash(f"Saved {Path(chosen).name}.")

    def open_history(self) -> None:
        """A small searchable list of earlier prompts; pick one to put it in the shared box."""
        window = tk.Toplevel(self.root)
        window.title("Earlier prompts")
        window.transient(self.root)
        window.configure(bg=BG)
        query = tk.StringVar()
        entry = ttk.Entry(window, textvariable=query, width=70)
        entry.pack(fill="x", padx=10, pady=(10, 4))
        listing = tk.Listbox(window, width=90, height=14, bg=FIELD, fg=FG, selectbackground=ACCENT, relief="flat",
                             highlightthickness=0)
        listing.pack(fill="both", expand=True, padx=10, pady=4)
        shown: list[str] = []

        def refresh(*_: object) -> None:
            listing.delete(0, "end")
            shown.clear()
            for item in self.history.search(query.get()):
                shown.append(item["prompt"])
                listing.insert("end", f"{item['ts']}   {' '.join(item['prompt'].split())[:90]}")

        def use(*_: object) -> None:
            picked = listing.curselection()
            if picked:
                self.broadcast.delete("1.0", "end")
                self.broadcast.insert("1.0", shown[picked[0]])
                window.destroy()

        query.trace_add("write", refresh)
        listing.bind("<Double-Button-1>", use)
        self._button(window, "Use this prompt", use).pack(pady=(4, 10))
        refresh()
        entry.focus_set()

    # ----- events from the worker -----------------------------------------------------------

    def begin(self, slots: list[int]) -> None:
        """A run is starting for these accounts."""
        self.running = True
        self.send_button.state(["disabled"])
        self.cancel_button.state(["!disabled"])
        self.retry_button.state(["disabled"])
        self.summary.set("Running... every account is working at the same time.")
        for slot in slots:
            self.results.pop(slot, None)
            self._show_empty(slot)
            self.status[slot].set("waiting")

    def _show_empty(self, slot: int) -> None:
        for box in (self.tab_text[slot], self.column_text[slot]):
            set_text(box, "")
        self.tab_info[slot].set("No answer yet.")
        self.column_info[slot].set("No answer yet.")

    def on_event(self, kind: str, data: dict[str, Any]) -> None:
        """Apply one run event (called from the app's pump on the Tk thread)."""
        slot = int(data.get("slot", 0))
        if kind == "run_started":
            if not self.running:
                self.begin([int(s) for s in data.get("slots", [])])
        elif kind == "run_status" and slot in self.status:
            word = STATE_WORDS.get(str(data["state"]), str(data["state"]))
            detail = str(data.get("detail") or "")
            self.status[slot].set(f"{word} - {float(data['elapsed']):.0f} s" + (f" - {detail}" if detail else ""))
        elif kind == "run_progress" and slot in self.tab_text:
            preview = f"(live preview, newest part)\n{data['preview']}"
            for box in (self.tab_text[slot], self.column_text[slot]):
                set_text(box, preview)
            self.tab_info[slot].set(f"Writing... {data['words']} words so far")
            self.column_info[slot].set(f"Writing... {data['words']} words")
        elif kind == "run_result" and slot in self.tab_text:
            self._show_result(AccountResult.from_dict(data))
        elif kind == "run_finished":
            self._finished(data)

    def _show_result(self, result: AccountResult) -> None:
        self.results[result.slot] = result
        slot, bad = result.slot, not result.ok
        for box in (self.tab_text[slot], self.column_text[slot]):
            set_text(box, result_text(result), bad=bad)
        line = f"{STATE_WORDS.get(result.state, result.state)} - {result.words} words - {result.elapsed:.0f} s"
        self.tab_info[slot].set(line)
        self.column_info[slot].set(line)
        self.status[slot].set(STATE_WORDS.get(result.state, result.state))

    def _finished(self, data: dict[str, Any]) -> None:
        self.running = False
        self.send_button.state(["!disabled"])
        self.cancel_button.state(["disabled"])
        if any(r.state in FAILED for r in self.results.values()):
            self.retry_button.state(["!disabled"])
        text = f"{data.get('done', 0)} of {data.get('total', 0)} finished."
        if data.get("cancelled"):
            text = "Cancelled. " + text
        if data.get("folder"):
            text += f" Saved in {data['folder']}"
        self.summary.set(text)
