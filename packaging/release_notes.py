"""Print the CHANGELOG.md section for one version (used by the release workflow).

    python packaging/release_notes.py v0.2.0
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def section(changelog: str, version: str) -> str:
    """Text under '## [version]' up to the next '## ' heading. Empty if there is none."""
    wanted = version.lstrip("v")
    lines = changelog.splitlines()
    out: list[str] = []
    inside = False
    for line in lines:
        heading = re.match(r"^## \[?([^\]\s]+)\]?", line)
        if heading:
            if inside:
                break
            inside = heading.group(1) == wanted
            continue
        if inside:
            out.append(line)
    return "\n".join(out).strip()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: release_notes.py vX.Y.Z", file=sys.stderr)
        return 2
    text = section((Path(__file__).resolve().parent.parent / "CHANGELOG.md").read_text(encoding="utf-8"), argv[1])
    print(text or f"FanOut {argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
