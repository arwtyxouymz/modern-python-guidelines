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

        guidelines = json.loads(run_tool("list", "--file", str(example), "--format", "json"))
        baseline_codes = {rule["code"] for rule in guidelines["baseline"]}
        if "UP006" not in baseline_codes:
            raise RuntimeError("the pre-edit list omitted UP006 for Python 3.10")

        explanation = json.loads(run_tool("explain", "--file", str(example), "UP006"))
        if [rule["code"] for rule in explanation["rules"]] != ["UP006"]:
            raise RuntimeError("Ruff returned an unexpected rule explanation")

        run_tool("fix", str(example))
        diagnostics = json.loads(run_tool("check", str(example)))
        if diagnostics:
            raise RuntimeError(f"modernization diagnostics remain: {diagnostics!r}")
        if "list[str]" not in example.read_text(encoding="utf-8"):
            raise RuntimeError("safe modernization fix was not applied")

        for target in ("py38", "py39", "py310", "py311", "py312", "py313", "py314"):
            payload = json.loads(
                run_tool(
                    "list",
                    "--file",
                    str(example),
                    "--target-version",
                    target,
                    "--format",
                    "json",
                )
            )
            expected_python = f"3.{target.removeprefix('py3')}"
            if payload["target_python"] != expected_python:
                raise RuntimeError(f"{target}: incorrect target resolution")
            listed = [
                rule["code"]
                for group in (payload["baseline"], payload["conditional"])
                for rule in group
            ]
            if not listed or len(listed) != len(set(listed)):
                raise RuntimeError(f"{target}: invalid guideline list")
            if any("no executable Python example" in warning for warning in payload["warnings"]):
                raise RuntimeError(f"{target}: Ruff documentation example extraction failed")

    with tempfile.TemporaryDirectory(prefix="modern-python-policy-") as directory:
        project = Path(directory)
        pyproject = project / "pyproject.toml"
        pyproject.write_text(
            "[tool.ruff]\n"
            'target-version = "py39"\n\n'
            "[tool.ruff.per-file-target-version]\n"
            '"special.py" = "py312"\n\n'
            "[tool.ruff.lint]\n"
            'ignore = ["UP006"]\n\n'
            "[tool.ruff.lint.per-file-ignores]\n"
            '"special.py" = ["UP007"]\n',
            encoding="utf-8",
        )
        special = project / "special.py"
        special.write_text(
            "from typing import List, Optional, Union\n\n"
            "values: List[Union[str, None]]\n"
            "item: Optional[int]\n",
            encoding="utf-8",
        )

        payload = json.loads(run_tool("list", "--file", str(special), "--format", "json"))
        listed_codes = {
            rule["code"]
            for group in (payload["baseline"], payload["conditional"])
            for rule in group
        }
        if payload["target_python"] != "3.12":
            raise RuntimeError("per-file target version was not applied")
        if {"UP006", "UP007"} & listed_codes:
            raise RuntimeError("project global/per-file ignores were not applied to list")

        diagnostics = json.loads(run_tool("check", str(special)))
        diagnostic_codes = {diagnostic["code"] for diagnostic in diagnostics}
        if {"UP006", "UP007"} & diagnostic_codes:
            raise RuntimeError("project global/per-file ignores were not applied to check")

    with tempfile.TemporaryDirectory(prefix="modern-python-preview-") as directory:
        project = Path(directory)
        (project / "pyproject.toml").write_text(
            "[tool.ruff]\n"
            'target-version = "py312"\n'
            "preview = true\n\n"
            "[tool.ruff.lint]\n"
            "explicit-preview-rules = true\n"
            'extend-select = ["UP048"]\n',
            encoding="utf-8",
        )
        example = project / "example.py"
        example.write_text("value = 1\n", encoding="utf-8")

        payload = json.loads(run_tool("list", "--file", str(example), "--format", "json"))
        preview_codes = {
            rule["code"]
            for group in (payload["baseline"], payload["conditional"])
            for rule in group
            if rule["status"] == "Preview"
        }
        if preview_codes != {"UP048"}:
            raise RuntimeError(
                "explicit-preview-rules did not limit project preview guidance: "
                f"{sorted(preview_codes)!r}"
            )

        payload = json.loads(
            run_tool("list", "--file", str(example), "--preview", "--format", "json")
        )
        opted_in_preview_codes = {
            rule["code"]
            for group in (payload["baseline"], payload["conditional"])
            for rule in group
            if rule["status"] == "Preview"
        }
        if not preview_codes < opted_in_preview_codes:
            raise RuntimeError("--preview did not opt in to all applicable preview guidance")

    print("Real Ruff integration smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
