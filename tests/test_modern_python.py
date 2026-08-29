from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

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
SPEC = importlib.util.spec_from_file_location("modern_python_tool", TOOL)
assert SPEC and SPEC.loader
modern_python = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = modern_python
SPEC.loader.exec_module(modern_python)


FAKE_RUFF = r"""
import json
import sys

args = sys.argv[1:]
if args == ["--version"]:
    print("ruff 9.8.7")
elif args[:2] == ["check", "--show-settings"]:
    print("linter.unresolved_target_version = 3.12")
    print("linter.preview = disabled")
elif args and args[0] == "rule":
    print(json.dumps({"code": args[1], "summary": "details"}))
elif args and args[0] == "check":
    print(json.dumps({"arguments": args}))
else:
    print("unexpected arguments", file=sys.stderr)
    raise SystemExit(2)
"""


class ModernPythonToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.fake_ruff = self.directory / "fake_ruff.py"
        self.fake_ruff.write_text(textwrap.dedent(FAKE_RUFF), encoding="utf-8")

    def run_tool(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["MODERN_PYTHON_RUFF_COMMAND"] = f'"{sys.executable}" "{self.fake_ruff}"'
        return subprocess.run(
            [sys.executable, str(TOOL), *arguments],
            cwd=self.directory,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_probe_reports_target_and_runner(self) -> None:
        result = self.run_tool("probe", "--file", "example.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["target_python"], "3.12")
        self.assertEqual(payload["ruff_version"], "9.8.7")
        self.assertEqual(payload["runner"], "MODERN_PYTHON_RUFF_COMMAND")

    def test_check_uses_modern_profile_and_json_output(self) -> None:
        result = self.run_tool("check", "example.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = json.loads(result.stdout)["arguments"]
        self.assertIn("UP,FURB,SIM,C4,PIE,PTH,FLY,PERF,F401", arguments)
        self.assertIn("--output-format", arguments)
        self.assertIn("--exit-zero", arguments)

    def test_fix_is_safe_by_default(self) -> None:
        result = self.run_tool("fix", "example.py")
        arguments = json.loads(result.stdout)["arguments"]
        self.assertIn("--fix", arguments)
        self.assertNotIn("--unsafe-fixes", arguments)

    def test_unsafe_fix_requires_explicit_flag(self) -> None:
        result = self.run_tool("fix", "--unsafe-fixes", "example.py")
        arguments = json.loads(result.stdout)["arguments"]
        self.assertIn("--unsafe-fixes", arguments)

    def test_explain_returns_only_requested_rules(self) -> None:
        result = self.run_tool("explain", "UP001", "FURB001")
        payload = json.loads(result.stdout)
        self.assertEqual([rule["code"] for rule in payload["rules"]], ["UP001", "FURB001"])

    def test_invalid_rule_code_fails(self) -> None:
        result = self.run_tool("explain", "not-a-code")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid Ruff rule code", result.stderr)

    def test_pixi_project_uses_pixi_runner(self) -> None:
        (self.directory / "pixi.toml").write_text(
            '[workspace]\nname = "example"\n', encoding="utf-8"
        )
        with mock.patch.object(
            modern_python.shutil,
            "which",
            side_effect=lambda name: "/usr/bin/pixi" if name == "pixi" else None,
        ):
            candidates = modern_python.candidate_runners(self.directory)

        self.assertIn(("Pixi environment", ("pixi", "run", "ruff")), candidates)

    def test_pixi_pyproject_is_detected(self) -> None:
        (self.directory / "pyproject.toml").write_text(
            '[tool.pixi.workspace]\nname = "example"\n', encoding="utf-8"
        )
        self.assertTrue(modern_python.is_pixi_project(self.directory))

    def test_bundled_ruff_requirement_is_exactly_pinned(self) -> None:
        self.assertRegex(modern_python.pinned_ruff_version(), r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
