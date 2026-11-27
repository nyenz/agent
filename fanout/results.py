"""Results: the answer model, HTML -> Markdown, Copy All / export text, auto-save and prompt history.

Nothing here touches Tk or the browser, so it is all unit-testable.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

HISTORY_LIMIT = 200
CHROME_LINES = {
    "copy", "copy code", "copied", "copied!", "regenerate", "regenerate response", "like", "dislike",
    "good response", "bad response", "share", "read aloud", "edit", "retry", "thumbs up", "thumbs down",
}
SKIPPED_TAGS = {"script", "style", "button", "svg", "noscript", "template"}
HEADINGS = {f"h{n}": n for n in range(1, 7)}


@dataclass
class AccountResult:
    """What one account produced for one prompt."""

    slot: int
    site: str
    prompt: str
    state: str  # done | error | timeout | cancelled
    text: str = ""
    markdown: str = ""
    message: str = ""
    fix: str = ""
    elapsed: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0
    send_method: str = ""

    @property
    def words(self) -> int:
        """Number of words in the answer."""
        return word_count(self.text)

    @property
    def ok(self) -> bool:
        """True when the reply finished normally."""
        return self.state == "done"

    def to_dict(self) -> dict[str, Any]:
        """Plain dict for events and run.json."""
        data = asdict(self)
        data["words"] = self.words
        return data

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AccountResult:
        """Rebuild from an event payload."""
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in raw.items() if k in fields})


def word_count(text: str) -> int:
    """Words in a piece of text."""
    return len(text.split())


def clean_text(text: str) -> str:
    """Drop the site's own buttons (Copy, Regenerate, Like...) that were read along with the answer."""
    lines = [ln for ln in text.splitlines() if ln.strip().lower() not in CHROME_LINES]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


class _Markdown(HTMLParser):
    """Small HTML -> Markdown converter (headings, lists, code, links, quotes, tables)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[list[str]] = []  # saved buffers while inside a quote or table cell
        self.skip = 0
        self.lists: list[list[Any]] = []  # [kind, counter]
        self.pre_at: int | None = None
        self.links: list[str] = []
        self.rows: list[list[str]] = []
        self.row: list[str] = []
        self.header_row = False

    def nl(self, count: int = 2) -> None:
        """Make the output end with exactly `count` line breaks (nothing at the very start)."""
        if not self.out:
            return
        joined = "".join(self.out[-3:])
        have = len(joined) - len(joined.rstrip("\n"))
        if have < count:
            self.out.append("\n" * (count - have))

    def push(self) -> None:
        self.stack.append(self.out)
        self.out = []

    def pop(self) -> str:
        inner = "".join(self.out)
        self.out = self.stack.pop()
        return inner

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: C901 - one branch per tag
        a = dict(attrs)
        if tag in SKIPPED_TAGS:
            self.skip += 1
        elif self.skip:
            return
        elif tag in ("p", "section", "article", "header", "footer", "figure"):
            self.nl(2)
        elif tag == "div":
            self.nl(1)
        elif tag in HEADINGS:
            self.nl(2)
            self.out.append("#" * HEADINGS[tag] + " ")
        elif tag == "br":
            self.out.append("\n")
        elif tag in ("strong", "b"):
            self.out.append("**")
        elif tag in ("em", "i"):
            self.out.append("*")
        elif tag == "pre":
            self.nl(2)
            self.pre_at = len(self.out)
            self.out.append("```\n")
        elif tag == "code":
            if self.pre_at is None:
                self.out.append("`")
            else:
                match = re.search(r"language-([\w+#-]+)", a.get("class") or "")
                if match:
                    self.out[self.pre_at] = f"```{match.group(1)}\n"
        elif tag in ("ul", "ol"):
            self.nl(1 if self.lists else 2)
            self.lists.append([tag, 0])
        elif tag == "li" and self.lists:
            self.nl(1)
            kind = self.lists[-1]
            kind[1] += 1
            self.out.append("  " * (len(self.lists) - 1) + ("- " if kind[0] == "ul" else f"{kind[1]}. "))
        elif tag == "a":
            self.links.append(a.get("href") or "")
            self.out.append("[")
        elif tag == "blockquote":
            self.nl(2)
            self.push()
        elif tag == "hr":
            self.nl(2)
            self.out.append("---")
            self.nl(2)
        elif tag == "img" and a.get("alt"):
            self.out.append(f"![{a['alt']}]({a.get('src') or ''})")
        elif tag == "table":
            self.nl(2)
            self.rows = []
        elif tag == "tr":
            self.row = []
            self.header_row = False
        elif tag in ("td", "th"):
            self.header_row = self.header_row or tag == "th"
            self.push()

    def handle_endtag(self, tag: str) -> None:  # noqa: C901 - one branch per tag
        if tag in SKIPPED_TAGS:
            self.skip = max(0, self.skip - 1)
        elif self.skip:
            return
        elif tag in ("p", "section", "article", "header", "footer", "figure") or tag in HEADINGS:
            self.nl(2)
        elif tag == "div":
            self.nl(1)
        elif tag in ("strong", "b"):
            self.out.append("**")
        elif tag in ("em", "i"):
            self.out.append("*")
        elif tag == "pre":
            self.nl(1)
            self.out.append("```")
            self.nl(2)
            self.pre_at = None
        elif tag == "code" and self.pre_at is None:
            self.out.append("`")
        elif tag in ("ul", "ol") and self.lists:
            self.lists.pop()
            self.nl(2 if not self.lists else 1)
        elif tag == "a" and self.links:
            self.out.append(f"]({self.links.pop()})")
        elif tag == "blockquote" and self.stack:
            inner = self.pop().strip("\n")
            self.out.append("\n".join("> " + ln if ln else ">" for ln in inner.splitlines()))
            self.nl(2)
        elif tag in ("td", "th") and self.stack:
            cell = " ".join(self.pop().split()).replace("|", "\\|")
            self.row.append(cell)
        elif tag == "tr" and self.row:
            self.rows.append(self.row)
            if self.header_row and len(self.rows) == 1:
                self.rows.append(["---"] * len(self.row))
            self.row = []
        elif tag == "table" and self.rows:
            if self.rows[0] and not (len(self.rows) > 1 and set(self.rows[1]) == {"---"}):
                self.rows.insert(1, ["---"] * len(self.rows[0]))
            self.out.append("\n".join("| " + " | ".join(r) + " |" for r in self.rows))
            self.nl(2)
            self.rows = []

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        if self.pre_at is not None:
            self.out.append(data)
            return
        text = re.sub(r"\s+", " ", data)
        if not self.out or "".join(self.out[-2:]).endswith("\n"):
            text = text.lstrip()
        self.out.append(text)


def html_to_markdown(html: str) -> str:
    """Turn a reply's HTML into Markdown."""
    parser = _Markdown()
    parser.feed(html)
    parser.close()
    while parser.stack:
        parser.out = parser.stack.pop() + parser.out
    text = "".join(parser.out)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def best_answer(result: AccountResult) -> str:
    """The text to copy or export for one account: Markdown if we have it, else plain text."""
    return result.markdown or result.text


def copy_all_text(results: list[AccountResult]) -> str:
    """Every answer, each under '=== Account N - site ===' (what Copy All puts on the clipboard)."""
    blocks = []
    for r in sorted(results, key=lambda x: x.slot):
        body = best_answer(r) if r.ok or r.text else f"({r.message or r.state})"
        blocks.append(f"=== Account {r.slot} - {r.site or 'unknown site'} ===\n{body}")
    return "\n\n".join(blocks) + "\n"


def export_markdown(results: list[AccountResult]) -> str:
    """A readable .md file: the prompt and the answer for each account."""
    lines = ["# FanOut results", "", f"Saved {time.strftime('%Y-%m-%d %H:%M')}", ""]
    for r in sorted(results, key=lambda x: x.slot):
        lines += [f"## Account {r.slot} - {r.site or 'unknown site'}", ""]
        lines += [f"**Prompt:** {r.prompt}", ""]
        lines += [f"*Status: {r.state}, {r.words} words, {r.elapsed:.0f} s*", ""]
        lines += [best_answer(r) if r.text or r.ok else f"({r.message or r.state})", ""]
    return "\n".join(lines).rstrip() + "\n"


def export_json(results: list[AccountResult]) -> str:
    """A .json file with one entry per account."""
    payload = {
        "exported": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "accounts": [r.to_dict() for r in sorted(results, key=lambda x: x.slot)],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def write_text_atomic(path: Path, text: str) -> None:
    """Write a file by writing a temp file and replacing, so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save_run(base: Path, results: list[AccountResult], started_at: float | None = None) -> Path:
    """Auto-save one run to <base>/<timestamp>/account_N.md plus run.json. Returns the folder."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(started_at or time.time()))
    folder = base / stamp
    n = 1
    while folder.exists():
        n += 1
        folder = base / f"{stamp}-{n}"
    folder.mkdir(parents=True)
    for r in results:
        write_text_atomic(folder / f"account_{r.slot}.md", f"# Account {r.slot} - {r.site}\n\n{best_answer(r) or r.message}\n")
    write_text_atomic(folder / "run.json", export_json(results))
    return folder


class PromptHistory:
    """The last prompts sent, searchable, kept in one small json file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.items: list[dict[str, str]] = []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                self.items = [i for i in raw if isinstance(i, dict) and isinstance(i.get("prompt"), str)][-HISTORY_LIMIT:]
        except (OSError, ValueError):
            self.items = []

    def add(self, prompt: str) -> None:
        """Remember a prompt (newest last). The same prompt twice in a row is kept once."""
        prompt = prompt.strip()
        if not prompt or (self.items and self.items[-1]["prompt"] == prompt):
            return
        self.items = [*self.items, {"ts": time.strftime("%Y-%m-%d %H:%M"), "prompt": prompt}][-HISTORY_LIMIT:]
        write_text_atomic(self.path, json.dumps(self.items, ensure_ascii=False))

    def search(self, text: str = "") -> list[dict[str, str]]:
        """Prompts containing `text` (any case), newest first."""
        needle = text.strip().lower()
        return [i for i in reversed(self.items) if needle in i["prompt"].lower()]
