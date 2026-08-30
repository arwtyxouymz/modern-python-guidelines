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
import os
import sys
from pathlib import Path

args = sys.argv[1:]
rules = [
    {
        "name": "non-pep585-annotation",
        "code": "UP006",
        "summary": "Use modern generics",
        "explanation": "## What it does\nChecks old generics.\n\n## Example\n```python\nfrom typing import List\nx: List[int]\n```\n\nUse instead:\n```python\nx: list[int]\n```",
        "preview": False,
        "status": {"Stable": {"since": "0.1.0"}},
    },
    {
        "name": "non-pep604-annotation-optional",
        "code": "UP045",
        "summary": "Use PEP 604",
        "explanation": "## What it does\nChecks Optional annotations.\n\nWith __future__ annotations this applies earlier.\n\n## Example\n```python\nfrom typing import Optional\nx: Optional[int]\n```\n\nUse instead:\n```python\nx: int | None\n```",
        "preview": False,
        "status": {"Stable": {"since": "0.1.0"}},
    },
    {
        "name": "removed-rule",
        "code": "UP038",
        "summary": "Removed",
        "explanation": "## Removed\nDo not use.",
        "preview": False,
        "status": {"Removed": {"since": "0.2.0"}},
    },
    {
        "name": "preview-rule",
        "code": "UP051",
        "summary": "Preview",
        "explanation": "## What it does\nChecks preview syntax.\n\n## Example\n```python\npreview()\n```",
        "preview": True,
        "status": {"Preview": {"since": "0.3.0"}},
    },
    {
        "name": "print-empty-string",
        "code": "FURB105",
        "summary": "Avoid empty print args",
        "explanation": "## What it does\nChecks empty print arguments.\n\n## Example\n```python\nprint(\"\")\n```\n\nUse instead:\n```python\nprint()\n```",
        "preview": False,
        "status": {"Stable": {"since": "0.1.0"}},
    },
]

if args == ["--version"]:
    print("ruff 9.8.7")
elif args[:2] == ["check", "--show-settings"]:
    print("linter.unresolved_target_version = 3.12")
    print("linter.per_file_target_version = {}")
    print("linter.preview = disabled")
    print("linter.explicit_preview_rules = false")
    print("linter.rules.enabled = [")
    for code in ("UP006", "UP045", "FURB105", "SIM101", "C400", "PIE790", "PTH100", "FLY002", "PERF101", "F401"):
        print(f"\tfake-rule ({code}),")
    print("]")
    print("linter.per_file_ignores = {}")
elif args[:3] == ["rule", "--all", "--output-format"] and os.environ.get("FAKE_NO_METADATA"):
    print("unsupported", file=sys.stderr)
    raise SystemExit(2)
elif args[:3] == ["rule", "--all", "--output-format"]:
    print(json.dumps(rules))
elif args and args[0] == "rule":
    rule = next((rule for rule in rules if rule["code"] == args[1]), None)
    print(json.dumps(rule or {"code": args[1], "summary": "details"}))
elif args and args[0] == "check" and "--isolated" in args:
    root = Path(args[1])
    target = args[args.index("--target-version") + 1]
    diagnostics = []
    for path in root.rglob("*.py"):
        code = path.parent.name
        contents = path.read_text(encoding="utf-8")
        future = "from __future__ import annotations" in contents
        applies = (
            code == "FURB105"
            or code == "UP006" and (target != "py38" or future)
            or code == "UP045" and (target not in {"py38", "py39"} or future)
            or code == "UP051" and "--preview" in args
        )
        if applies:
            diagnostics.append({"code": code, "filename": str(path)})
    print(json.dumps(diagnostics))
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

    def test_list_returns_guidance_before_editing(self) -> None:
        result = self.run_tool("list", "--file", "example.py", "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["target_python"], "3.12")
        self.assertEqual(
            [rule["code"] for rule in payload["baseline"]],
            ["UP006", "UP045", "FURB105"],
        )
        self.assertNotIn("UP038", [rule["code"] for rule in payload["baseline"]])

    def test_future_annotations_rules_are_marked_conditional(self) -> None:
        result = self.run_tool(
            "list",
            "--file",
            "example.py",
            "--target-version",
            "py39",
            "--format",
            "json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual([rule["code"] for rule in payload["conditional"]], ["UP045"])

    def test_missing_metadata_requires_update_or_explicit_stale_fallback(self) -> None:
        with mock.patch.dict(os.environ, {"FAKE_NO_METADATA": "1"}):
            blocked = self.run_tool("list", "--file", "example.py", "--format", "json")
            allowed = self.run_tool(
                "list",
                "--file",
                "example.py",
                "--allow-stale",
                "--format",
                "json",
            )
        self.assertEqual(blocked.returncode, 3)
        self.assertIn("Ask the user whether to update", blocked.stderr)
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertEqual(json.loads(allowed.stdout)["status"], "degraded")

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

        self.assertIn(
            modern_python.RunnerCandidate("Pixi environment", ("pixi", "run", "ruff"), "project"),
            candidates,
        )

    def test_pixi_pyproject_is_detected(self) -> None:
        (self.directory / "pyproject.toml").write_text(
            '[tool.pixi.workspace]\nname = "example"\n', encoding="utf-8"
        )
        self.assertTrue(modern_python.is_pixi_project(self.directory))

    def test_bundled_ruff_requirement_is_exactly_pinned(self) -> None:
        self.assertRegex(modern_python.pinned_ruff_version(), r"^\d+\.\d+\.\d+$")

    def test_per_file_target_and_ignore_match_the_intended_file(self) -> None:
        settings = f'''linter.unresolved_target_version = 3.9
linter.per_file_target_version = {{
    absolute_matcher = "{(self.directory / "special.py").as_posix()}"
basename_matcher = "special.py"
negated = false
data = 3.12

}}
linter.per_file_ignores = {{
    absolute_matcher = "{(self.directory / "special.py").as_posix()}"
basename_matcher = "special.py"
negated = false
data = [
    non-pep604-annotation-optional (UP045),
]

}}
'''
        target, source = modern_python.target_from_settings(settings, self.directory / "special.py")
        self.assertEqual((target, source), ("3.12", "per-file-target-version"))
        self.assertEqual(
            modern_python.per_file_ignored_codes(settings, self.directory / "special.py"),
            frozenset({"UP045"}),
        )

    def test_legacy_removed_heading_is_excluded(self) -> None:
        rule = {"code": "UP038", "preview": False, "explanation": "## Removed\nRetired."}
        self.assertEqual(modern_python.rule_status(rule), "Removed")

    def test_reference_diff_detects_new_and_retired_guidelines(self) -> None:
        def guideline_set(active: tuple[str, ...], available: tuple[str, ...]):
            rules = tuple({"code": code} for code in active)
            return modern_python.GuidelineSet(
                rules=rules,
                examples={},
                baseline_codes=frozenset(available),
                conditional_codes=frozenset(),
                missing_example_codes=(),
            )

        project = guideline_set(("UP001", "UP038"), ("UP001", "UP038"))
        reference = guideline_set(("UP001", "UP051"), ("UP001", "UP051"))
        project_policy = modern_python.Policy(project.active_codes, frozenset())
        reference_policy = modern_python.Policy(reference.active_codes, frozenset())
        missing, retired = modern_python.reference_differences(
            project, project_policy, reference, reference_policy
        )
        self.assertEqual(missing, ("UP051",))
        self.assertEqual(retired, ("UP038",))

    def test_explicit_preview_rules_only_allow_native_exact_selection(self) -> None:
        settings = modern_python.ResolvedSettings(
            intended_file=self.directory / "example.py",
            subject=self.directory,
            target_python="3.12",
            target_version="py312",
            target_source="test",
            preview=True,
            explicit_preview_rules=True,
            config_path=None,
            inferred_file_context=False,
        )
        settings_text = """linter.rules.enabled = [
    fake-rule (UP048),
]
linter.per_file_ignores = {}
"""
        policy = modern_python.resolve_policy(
            settings,
            settings_text,
            frozenset({"UP048", "UP051"}),
            preview_codes_requiring_native_selection=frozenset({"UP048", "UP051"}),
        )
        self.assertEqual(policy.allowed, frozenset({"UP048"}))

    def test_global_ignore_selectors_are_read_from_pyproject(self) -> None:
        config = self.directory / "pyproject.toml"
        config.write_text(
            '[tool.ruff.lint]\nignore = ["UP006"]\nextend-ignore = ["FURB"]\n',
            encoding="utf-8",
        )
        self.assertEqual(
            modern_python.resolved_ignore_selectors(config),
            frozenset({"UP006", "FURB"}),
        )


if __name__ == "__main__":
    unittest.main()
