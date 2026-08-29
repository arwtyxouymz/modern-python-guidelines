#!/usr/bin/env python3
"""Expose Ruff's modernization guidance to coding agents."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROFILES = {
    "core": ("UP", "FURB", "F401"),
    "modern": ("UP", "FURB", "SIM", "C4", "PIE", "PTH", "FLY", "PERF", "F401"),
}
DEFAULT_PROFILE = "modern"
COMMAND_ENV = "MODERN_PYTHON_RUFF_COMMAND"
RULE_CODE = re.compile(r"^[A-Z]+[0-9]+$")


class ToolError(RuntimeError):
    """A user-facing tool failure."""


@dataclass(frozen=True)
class Runner:
    label: str
    argv: tuple[str, ...]
    version: str


def find_project_root(start: Path) -> Path:
    markers = (
        "pyproject.toml",
        "ruff.toml",
        ".ruff.toml",
        "uv.lock",
        "poetry.lock",
        "pdm.lock",
        "Pipfile",
        ".git",
    )
    current = start.resolve()
    for directory in (current, *current.parents):
        if any((directory / marker).exists() for marker in markers):
            return directory
    return current


def pinned_ruff_version() -> str:
    value = (Path(__file__).with_name("RUFF_VERSION")).read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ToolError(f"invalid bundled Ruff version: {value!r}")
    return value


def executable_in_venv(root: Path) -> Path | None:
    names = (Path(".venv/bin/ruff"), Path(".venv/Scripts/ruff.exe"))
    for relative in names:
        candidate = root / relative
        if candidate.is_file():
            return candidate
    return None


def candidate_runners(root: Path) -> list[tuple[str, tuple[str, ...]]]:
    candidates: list[tuple[str, tuple[str, ...]]] = []

    override = os.environ.get(COMMAND_ENV, "").strip()
    if override:
        command = tuple(shlex.split(override, posix=os.name != "nt"))
        if not command:
            raise ToolError(f"{COMMAND_ENV} is empty after parsing")
        candidates.append((COMMAND_ENV, command))

    venv_ruff = executable_in_venv(root)
    if venv_ruff:
        candidates.append(("project .venv", (str(venv_ruff),)))

    manager_candidates = (
        ("Poetry environment", "poetry.lock", ("poetry", "run", "ruff")),
        ("PDM environment", "pdm.lock", ("pdm", "run", "ruff")),
        ("Pipenv environment", "Pipfile", ("pipenv", "run", "ruff")),
    )
    for label, marker, command in manager_candidates:
        if (root / marker).exists() and shutil.which(command[0]):
            candidates.append((label, command))

    if path_ruff := shutil.which("ruff"):
        candidates.append(("PATH", (path_ruff,)))

    candidates.append(("current Python environment", (sys.executable, "-m", "ruff")))

    version = pinned_ruff_version()
    if uv := shutil.which("uvx"):
        candidates.append(("bundled uvx fallback", (uv, "--from", f"ruff=={version}", "ruff")))
    if pipx := shutil.which("pipx"):
        candidates.append(("bundled pipx fallback", (pipx, "run", f"ruff=={version}")))

    return candidates


def run_process(argv: Sequence[str], *, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolError(f"could not execute {shlex.join(argv)}: {exc}") from exc


def discover_runner() -> Runner:
    root = find_project_root(Path.cwd())
    failures: list[str] = []
    for label, command in candidate_runners(root):
        result = run_process((*command, "--version"))
        version_text = result.stdout.strip()
        if result.returncode == 0 and version_text.startswith("ruff "):
            return Runner(label=label, argv=command, version=version_text)
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        failures.append(f"{label}: {detail.splitlines()[0]}")

    attempted = "\n  - ".join(failures)
    raise ToolError(
        "Ruff is unavailable. Install Ruff in the project or install uv/pipx for the "
        f"bundled fallback. Attempts:\n  - {attempted}"
    )


def invoke_ruff(runner: Runner, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    result = run_process((*runner.argv, *arguments))
    if result.returncode not in (0, 1):
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise ToolError(f"Ruff failed: {detail}")
    return result


def selected_rules(args: argparse.Namespace) -> tuple[str, ...]:
    if args.rules:
        rules = tuple(dict.fromkeys(part.strip() for part in args.rules.split(",") if part.strip()))
        if not rules:
            raise ToolError("--rules must contain at least one Ruff rule prefix")
        return rules
    return PROFILES[args.profile]


def check_arguments(args: argparse.Namespace, *, fix: bool) -> list[str]:
    paths = args.paths or ["."]
    command = [
        "check",
        *paths,
        "--select",
        ",".join(selected_rules(args)),
        "--output-format",
        "json",
        "--exit-zero",
    ]
    if args.preview:
        command.append("--preview")
    if fix:
        command.append("--fix")
        if args.unsafe_fixes:
            command.append("--unsafe-fixes")
    return command


def command_probe(args: argparse.Namespace, runner: Runner) -> None:
    result = invoke_ruff(runner, ("check", "--show-settings", args.file))
    settings = result.stdout
    target_match = re.search(r"^linter\.unresolved_target_version = (.+)$", settings, re.MULTILINE)
    preview_match = re.search(r"^linter\.preview = (.+)$", settings, re.MULTILINE)
    payload = {
        "runner": runner.label,
        "command": list(runner.argv),
        "ruff_version": runner.version.removeprefix("ruff "),
        "target_python": target_match.group(1).strip() if target_match else None,
        "preview": preview_match.group(1).strip() if preview_match else None,
        "default_profile": DEFAULT_PROFILE,
        "default_rule_prefixes": list(PROFILES[DEFAULT_PROFILE]),
    }
    print_json(payload)


def command_check(args: argparse.Namespace, runner: Runner, *, fix: bool) -> None:
    result = invoke_ruff(runner, check_arguments(args, fix=fix))
    print(result.stdout.strip() or "[]")


def command_explain(args: argparse.Namespace, runner: Runner) -> None:
    rules: list[dict[str, Any]] = []
    for code in dict.fromkeys(args.codes):
        normalized = code.strip().upper()
        if not RULE_CODE.fullmatch(normalized):
            raise ToolError(f"invalid Ruff rule code: {code!r}")
        result = invoke_ruff(runner, ("rule", normalized, "--output-format", "json"))
        try:
            rule = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ToolError(f"Ruff returned invalid JSON for {normalized}: {exc}") from exc
        rules.append(rule)
    print_json({"ruff_version": runner.version.removeprefix("ruff "), "rules": rules})


def command_catalog(args: argparse.Namespace, runner: Runner) -> None:
    result = invoke_ruff(runner, ("rule", "--all", "--output-format", "json"))
    try:
        catalog = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ToolError(f"Ruff returned invalid rule catalog JSON: {exc}") from exc

    prefixes = selected_rules(args)
    fields = (
        "code",
        "name",
        "linter",
        "summary",
        "fix",
        "fix_availability",
        "preview",
        "status",
        "category",
    )
    rules = [
        {field: rule.get(field) for field in fields}
        for rule in catalog
        if str(rule.get("code", "")).startswith(prefixes)
        and (args.include_preview or not rule.get("preview", False))
    ]
    rules.sort(key=lambda rule: str(rule["code"]))
    print_json(
        {
            "schema_version": 1,
            "ruff_version": runner.version.removeprefix("ruff "),
            "profile": args.profile,
            "rule_prefixes": list(prefixes),
            "include_preview": args.include_preview,
            "rules": rules,
        }
    )


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))


def add_rule_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", choices=sorted(PROFILES), default=DEFAULT_PROFILE)
    parser.add_argument("--rules", help="Comma-separated Ruff rule prefixes; overrides --profile")
    parser.add_argument("--preview", action="store_true", help="Include Ruff preview diagnostics")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Expose version-aware Ruff modernization guidance to coding agents."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("probe", help="Report the Ruff runner and resolved Python target")
    probe.add_argument("--file", default=".", help="Relevant Python file or project path")

    check = subparsers.add_parser("check", help="Return modernization diagnostics as JSON")
    add_rule_options(check)
    check.add_argument("paths", nargs="*", help="Files or directories to check")

    fix = subparsers.add_parser("fix", help="Apply Ruff fixes and return remaining diagnostics")
    add_rule_options(fix)
    fix.add_argument(
        "--unsafe-fixes",
        action="store_true",
        help="Also apply Ruff fixes that may change behavior; requires deliberate use",
    )
    fix.add_argument("paths", nargs="*", help="Files or directories to fix")

    explain = subparsers.add_parser("explain", help="Return full documentation for rule codes")
    explain.add_argument("codes", nargs="+", help="Ruff rule codes from check output")

    catalog = subparsers.add_parser("catalog", help="Export the selected Ruff rule inventory")
    add_rule_options(catalog)
    catalog.add_argument(
        "--include-preview",
        action="store_true",
        help="Include preview rules in the exported inventory",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        runner = discover_runner()
        if args.command == "probe":
            command_probe(args, runner)
        elif args.command == "check":
            command_check(args, runner, fix=False)
        elif args.command == "fix":
            command_check(args, runner, fix=True)
        elif args.command == "explain":
            command_explain(args, runner)
        elif args.command == "catalog":
            command_catalog(args, runner)
        else:  # pragma: no cover - argparse prevents this
            raise ToolError(f"unsupported command: {args.command}")
    except ToolError as exc:
        print(f"modern-python-guidelines: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
