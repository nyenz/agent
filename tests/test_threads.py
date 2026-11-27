"""Helper threads must not hold the Tk app object.

If a background thread drops the last reference to the app, its Tk variables are freed on the wrong thread and
Tcl aborts the whole program (seen on macOS CI). So the small `work` functions that threads run may use plain
values captured beforehand, but never `self`.
"""

from __future__ import annotations

import ast
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "fanout" / "ui.py"


def test_thread_functions_in_ui_never_touch_self() -> None:
    tree = ast.parse(UI.read_text(encoding="utf-8"))
    checked = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "work":
            checked.append(node.lineno)
            used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            assert "self" not in used, f"work() at ui.py line {node.lineno} uses self"
    assert len(checked) >= 4, "expected the scan, update-check, update and repair threads"
