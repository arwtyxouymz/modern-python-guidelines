#!/usr/bin/env python3
"""Regenerate the auditable Ruff rule snapshot and fallback version."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = (
    ROOT
    / "plugins"
    / "modern-python-guidelines"
    / "skills"
    / "use-modern-python"
    / "scripts"
    / "modern_python.py"
)
VERSION_FILE = TOOL.with_name("RUFF_VERSION")
DEFAULT_OUTPUT = ROOT / "generated" / "ruff-rules.json"


def render_snapshot(*, profile: str, ruff_command: str) -> tuple[str, str]:
    environment = os.environ.copy()
    environment["MODERN_PYTHON_RUFF_COMMAND"] = ruff_command
    result = subprocess.run(
        [sys.executable, str(TOOL), "catalog", "--profile", profile],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())

    payload = json.loads(result.stdout)
    version = str(payload["ruff_version"])
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimeError(f"unexpected Ruff version: {version!r}")
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", version


def update_file(path: Path, content: str, *, check: bool) -> bool:
    previous = path.read_text(encoding="utf-8") if path.exists() else None
    if previous == content:
        return False
    if not check:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="modern", choices=("core", "modern"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--ruff-command", default="uvx --refresh ruff")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    try:
        snapshot, version = render_snapshot(
            profile=args.profile,
            ruff_command=args.ruff_command,
        )
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"sync_rules: {exc}", file=sys.stderr)
        return 2

    changed = update_file(args.output, snapshot, check=args.check)
    changed |= update_file(VERSION_FILE, version + "\n", check=args.check)
    if args.check and changed:
        print("Ruff rule snapshot is out of date", file=sys.stderr)
        return 1
    print(f"Ruff {version}: {'changes detected' if changed else 'already current'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
