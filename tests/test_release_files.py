"""The release pipeline, docs and packaging files agree with each other and with the updater."""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import yaml

from fanout import __version__, updater

REPO = Path(__file__).resolve().parent.parent


def load_release_notes():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("release_notes", REPO / "packaging" / "release_notes.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_workflows_are_valid_yaml_with_the_expected_jobs() -> None:
    ci = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    assert set(ci["jobs"]) == {"lint", "test"}
    assert ci["jobs"]["test"]["strategy"]["matrix"]["os"] == ["ubuntu-latest", "windows-latest", "macos-latest"]
    release = yaml.safe_load((REPO / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    assert set(release["jobs"]) == {"build", "publish"}
    assert release[True]["push"]["tags"] == ["v*"], "YAML reads the key `on` as True"
    assert [m["name"] for m in release["jobs"]["build"]["strategy"]["matrix"]["include"]] == ["windows", "macos", "linux"]


def test_release_uses_the_asset_names_the_updater_looks_for() -> None:
    text = (REPO / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert f"{updater.SOURCE_PREFIX}${{GITHUB_REF_NAME}}.zip" in text
    assert f"{updater.SOURCE_PREFIX}${{GITHUB_REF_NAME}}.zip.sha256" in text
    assert "PLAYWRIGHT_BROWSERS_PATH=0" in text and "python -m playwright install chromium" in text


def test_every_spec_builds_a_folder_not_a_single_file_and_carries_the_scripts() -> None:
    for name in ("windows", "macos", "linux"):
        text = (REPO / "packaging" / f"{name}.spec").read_text(encoding="utf-8")
        ast.parse(text)
        assert "COLLECT(" in text and "exclude_binaries=True" in text, name
        assert "onefile" not in text
    assert "BUNDLE(" in (REPO / "packaging" / "macos.spec").read_text(encoding="utf-8")
    common = (REPO / "packaging" / "_common.py").read_text(encoding="utf-8")
    assert '"fanout" / "js"' in common


def test_changelog_has_the_current_version_and_the_notes_script_finds_it() -> None:
    notes = load_release_notes()
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{__version__}]" in changelog
    section = notes.section(changelog, f"v{__version__}")
    assert "Installers" in section and "Known limits" in section and "## [" not in section
    assert notes.section(changelog, "v99.0.0") == ""


def test_release_notes_stop_at_the_next_heading() -> None:
    notes = load_release_notes()
    text = "# Changelog\n\n## [2.0.0] - x\nnew stuff\n\n## [1.0.0] - y\nold stuff\n"
    assert notes.section(text, "2.0.0") == "new stuff"
    assert notes.section(text, "v1.0.0") == "old stuff"


def test_required_files_exist() -> None:
    for name in ("LICENSE", "README.md", "CHANGELOG.md", "docs/TROUBLESHOOTING.md", ".github/ISSUE_TEMPLATE/bug_report.md",
                 "pyproject.toml", "requirements.txt", "requirements-dev.txt", ".gitattributes"):
        assert (REPO / name).is_file(), name
    assert (REPO / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")
    assert "logs" in (REPO / ".github/ISSUE_TEMPLATE/bug_report.md").read_text(encoding="utf-8").lower()


def test_readme_covers_every_required_section_in_order() -> None:
    text = (REPO / "README.md").read_text(encoding="utf-8")
    order = ["## Install (Windows)", "## First run, step by step", "## Troubleshooting", "## Privacy", "## Limits you should know about",
             "## Terms of service", "## Why each package is needed", "## Packaged downloads and their size", "## Licence"]
    positions = [text.index(h) for h in order]
    assert positions == sorted(positions)
    assert "INSTALL.bat" in text and "Profile locks" in text and "appears" in text.lower()
    for package in re.findall(r"^(?:playwright|psutil|pyperclip)", (REPO / "requirements.txt").read_text(encoding="utf-8"), re.M):
        assert package.lower() in text.lower(), f"README must justify {package}"


def test_mentioned_buttons_exist_in_the_ui_code() -> None:
    ui = "".join((REPO / "fanout" / n).read_text(encoding="utf-8") for n in ("ui.py", "runview.py", "settingsdialog.py"))
    text = (REPO / "README.md").read_text(encoding="utf-8")
    for must in ("Map this account", "Fix by clicking", "All ready - continue", "Retry failed", "Copy all", "Open logs folder",
                 "Repair installation", "Update now", "Skip this version", "History"):
        assert must in ui, f"the README mentions a button that doesn't exist: {must}"
        assert must in text or must.lower() in text.lower()
