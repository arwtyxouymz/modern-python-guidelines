#!/usr/bin/env python3
"""Exercise the bundled adapter against a real Ruff installation."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
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


def run_tool(*arguments: str) -> str:
    result = subprocess.run(
        [sys.executable, str(TOOL), *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def main() -> int:
    with tempfile.TemporaryDirectory(prefix=".modern-python-smoke-", dir=ROOT) as directory:
        example = Path(directory) / "example.py"
        example.write_text(
            "from typing import List\n\n"
            "def identity(values: List[str]) -> List[str]:\n"
            "    return values\n",
            encoding="utf-8",
        )

        probe = json.loads(run_tool("probe", "--file", str(example)))
        if probe["target_python"] != "3.10":
            raise RuntimeError(f"unexpected target Python: {probe['target_python']!r}")

        explanation = json.loads(run_tool("explain", "UP006"))
        if [rule["code"] for rule in explanation["rules"]] != ["UP006"]:
            raise RuntimeError("Ruff returned an unexpected rule explanation")

        run_tool("fix", str(example))
        diagnostics = json.loads(run_tool("check", str(example)))
        if diagnostics:
            raise RuntimeError(f"modernization diagnostics remain: {diagnostics!r}")
        if "list[str]" not in example.read_text(encoding="utf-8"):
            raise RuntimeError("safe modernization fix was not applied")

    print("Real Ruff integration smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
