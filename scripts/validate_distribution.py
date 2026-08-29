#!/usr/bin/env python3
"""Validate cross-agent plugin metadata and generated artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "modern-python-guidelines"
PLUGIN_NAME = "modern-python-guidelines"
VERSION = "0.1.0"
PUBLISHER = "arwtyxouymz"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def validate() -> None:
    codex = load_json(PLUGIN / ".codex-plugin" / "plugin.json")
    claude = load_json(PLUGIN / ".claude-plugin" / "plugin.json")
    cursor = load_json(PLUGIN / ".cursor-plugin" / "plugin.json")
    for manifest in (codex, claude, cursor):
        require(manifest["name"] == PLUGIN_NAME, "plugin names must match")
        require(manifest["version"] == VERSION, "plugin versions must match")
        require(manifest["author"]["name"] == PUBLISHER, "plugin publishers must match")
    require(
        codex["interface"]["developerName"] == PUBLISHER,
        "Codex developer name must match the publisher",
    )

    for path in (
        ROOT / ".claude-plugin" / "marketplace.json",
        ROOT / ".cursor-plugin" / "marketplace.json",
        ROOT / ".junie-extension" / "marketplace.json",
    ):
        require(load_json(path)["owner"]["name"] == PUBLISHER, f"{path}: invalid owner")

    junie = load_json(ROOT / ".junie-extension" / "marketplace.json")
    require(
        junie["extensions"][0]["author"]["name"] == PUBLISHER,
        "Junie author must match the publisher",
    )
    require(
        f"Copyright (c) 2026 {PUBLISHER}" in (ROOT / "LICENSE").read_text(encoding="utf-8"),
        "license holder must match the publisher",
    )

    marketplace_specs = (
        (ROOT / ".agents" / "plugins" / "marketplace.json", "plugins"),
        (ROOT / ".claude-plugin" / "marketplace.json", "plugins"),
        (ROOT / ".cursor-plugin" / "marketplace.json", "plugins"),
        (ROOT / ".junie-extension" / "marketplace.json", "extensions"),
    )
    for path, collection in marketplace_specs:
        document = load_json(path)
        entries = document[collection]
        require(len(entries) == 1, f"{path}: expected one entry")
        source = entries[0]["source"]
        if isinstance(source, dict):
            source = source["path"]
        require(source == "./plugins/modern-python-guidelines", f"{path}: invalid source")

    skill = PLUGIN / "skills" / "use-modern-python" / "SKILL.md"
    skill_text = skill.read_text(encoding="utf-8")
    require(skill_text.startswith("---\n"), "skill frontmatter is missing")
    require("name: use-modern-python" in skill_text, "skill name is missing")
    require("description:" in skill_text, "skill description is missing")

    fallback_requirement = (
        (PLUGIN / "skills" / "use-modern-python" / "scripts" / "ruff-fallback.txt")
        .read_text(encoding="utf-8")
        .strip()
    )
    require(
        re.fullmatch(r"ruff==\d+\.\d+\.\d+", fallback_requirement) is not None,
        "invalid Ruff fallback requirement",
    )

    placeholder = "[" + "TODO:"
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
            continue
        try:
            contents = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        require(placeholder not in contents, f"placeholder in {path}")


def main() -> int:
    validate()
    print("Distribution validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
