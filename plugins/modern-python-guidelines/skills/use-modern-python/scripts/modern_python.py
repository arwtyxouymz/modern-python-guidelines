#!/usr/bin/env python3
"""Expose Ruff's modernization guidance to coding agents."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = None  # type: ignore[assignment]

PROFILES = {
    "core": ("UP", "FURB", "F401"),
    "modern": ("UP", "FURB", "SIM", "C4", "PIE", "PTH", "FLY", "PERF", "F401"),
}
DEFAULT_PROFILE = "modern"
GUIDELINE_PREFIXES = ("UP", "FURB")
COMMAND_ENV = "MODERN_PYTHON_RUFF_COMMAND"
RULE_CODE = re.compile(r"^[A-Z]+[0-9]+$")
RULE_SELECTOR = re.compile(r"^[A-Z]+[0-9]*$")
TARGET_VERSION = re.compile(r"^py3[0-9]{1,2}$")
PYTHON_FENCE = re.compile(r"```(?:python|py)(?:[^\n]*)\n(.*?)```", re.IGNORECASE | re.DOTALL)


class ToolError(RuntimeError):
    """A user-facing tool failure."""


class CapabilityError(ToolError):
    """Ruff cannot provide the metadata required for proactive guidance."""


class RuffUpdateRequired(ToolError):
    """The agent must ask before updating or bypassing the project's Ruff."""


@dataclass(frozen=True)
class RunnerCandidate:
    label: str
    argv: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class Runner:
    label: str
    argv: tuple[str, ...]
    version: str
    source: str
    root: Path
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class MatcherEntry:
    absolute_matcher: str | None
    basename_matcher: str | None
    negated: bool
    data: str


@dataclass(frozen=True)
class ResolvedSettings:
    intended_file: Path
    subject: Path
    target_python: str
    target_version: str
    target_source: str
    preview: bool
    explicit_preview_rules: bool
    config_path: Path | None
    inferred_file_context: bool


@dataclass(frozen=True)
class GuidelineSet:
    rules: tuple[dict[str, object], ...]
    examples: dict[str, tuple[str, ...]]
    baseline_codes: frozenset[str]
    conditional_codes: frozenset[str]
    missing_example_codes: tuple[str, ...]

    @property
    def active_codes(self) -> frozenset[str]:
        return frozenset(rule_code(rule) for rule in self.rules)

    @property
    def available_codes(self) -> frozenset[str]:
        return self.baseline_codes | self.conditional_codes


@dataclass(frozen=True)
class Policy:
    globally_enabled: frozenset[str]
    per_file_ignored: frozenset[str]

    @property
    def allowed(self) -> frozenset[str]:
        return self.globally_enabled - self.per_file_ignored


@dataclass(frozen=True)
class IgnoreDirectives:
    ignore: tuple[str, ...] | None
    extend_ignore: tuple[str, ...]
    extend: str | None


def find_project_root(start: Path) -> Path:
    markers = (
        "pyproject.toml",
        "ruff.toml",
        ".ruff.toml",
        "uv.lock",
        "pixi.toml",
        "pixi.lock",
        "poetry.lock",
        "pdm.lock",
        "Pipfile",
        ".git",
    )
    current = start.resolve()
    for directory in (current, *current.parents):
        if any((directory / marker).exists() for marker in markers):
            return directory
    return current if current.is_dir() else current.parent


def pinned_ruff_version() -> str:
    value = Path(__file__).with_name("ruff-fallback.txt").read_text(encoding="utf-8").strip()
    match = re.fullmatch(r"ruff==(\d+\.\d+\.\d+)", value)
    if match is None:
        raise ToolError(f"invalid bundled Ruff requirement: {value!r}")
    return match.group(1)


def executable_in_venv(root: Path) -> Path | None:
    names = (Path(".venv/bin/ruff"), Path(".venv/Scripts/ruff.exe"))
    for relative in names:
        candidate = root / relative
        if candidate.is_file():
            return candidate
    return None


def is_pixi_project(root: Path) -> bool:
    if (root / "pixi.toml").is_file() or (root / "pixi.lock").is_file():
        return True

    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return False
    try:
        contents = pyproject.read_text(encoding="utf-8")
    except OSError:
        return False
    return re.search(r"^\s*\[tool\.pixi(?:\.|\])", contents, re.MULTILINE) is not None


def bundled_candidates() -> list[RunnerCandidate]:
    version = pinned_ruff_version()
    candidates: list[RunnerCandidate] = []
    if uvx := shutil.which("uvx"):
        candidates.append(
            RunnerCandidate(
                "bundled uvx fallback",
                (uvx, "--from", f"ruff=={version}", "ruff"),
                "bundled",
            )
        )
    if pipx := shutil.which("pipx"):
        candidates.append(
            RunnerCandidate(
                "bundled pipx fallback",
                (pipx, "run", f"ruff=={version}"),
                "bundled",
            )
        )
    return candidates


def candidate_runners(root: Path) -> list[RunnerCandidate]:
    candidates: list[RunnerCandidate] = []

    override = os.environ.get(COMMAND_ENV, "").strip()
    if override:
        command = tuple(shlex.split(override, posix=os.name != "nt"))
        if not command:
            raise ToolError(f"{COMMAND_ENV} is empty after parsing")
        candidates.append(RunnerCandidate(COMMAND_ENV, command, "project"))

    venv_ruff = executable_in_venv(root)
    if venv_ruff:
        candidates.append(RunnerCandidate("project .venv", (str(venv_ruff),), "project"))

    if is_pixi_project(root) and shutil.which("pixi"):
        candidates.append(RunnerCandidate("Pixi environment", ("pixi", "run", "ruff"), "project"))

    manager_candidates = (
        ("Poetry environment", "poetry.lock", ("poetry", "run", "ruff")),
        ("PDM environment", "pdm.lock", ("pdm", "run", "ruff")),
        ("Pipenv environment", "Pipfile", ("pipenv", "run", "ruff")),
    )
    for label, marker, command in manager_candidates:
        if (root / marker).exists() and shutil.which(command[0]):
            candidates.append(RunnerCandidate(label, command, "project"))

    if path_ruff := shutil.which("ruff"):
        candidates.append(RunnerCandidate("PATH", (path_ruff,), "ambient"))

    candidates.append(
        RunnerCandidate("current Python environment", (sys.executable, "-m", "ruff"), "ambient")
    )
    candidates.extend(bundled_candidates())
    return candidates


def run_process(
    argv: Sequence[str],
    *,
    timeout: int = 180,
    input_text: str | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_text,
            cwd=cwd,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolError(f"could not execute {shlex.join(argv)}: {exc}") from exc


def probe_candidate(candidate: RunnerCandidate, root: Path) -> tuple[str | None, str | None]:
    try:
        result = run_process((*candidate.argv, "--version"), timeout=30, cwd=root)
    except ToolError as exc:
        return None, str(exc)
    version_text = result.stdout.strip()
    if result.returncode == 0 and version_text.startswith("ruff "):
        return version_text, None
    detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
    return None, detail.splitlines()[0]


def discover_runner(root: Path) -> Runner:
    failures: list[str] = []
    project_failures: list[str] = []
    for candidate in candidate_runners(root):
        version, failure = probe_candidate(candidate, root)
        if version is not None:
            warnings: list[str] = []
            if candidate.source != "project" and project_failures:
                warnings.append(
                    "A project-managed Ruff runner was detected but unavailable: "
                    + "; ".join(project_failures)
                )
            return Runner(
                label=candidate.label,
                argv=candidate.argv,
                version=version,
                source=candidate.source,
                root=root,
                warnings=tuple(warnings),
            )

        assert failure is not None
        failures.append(f"{candidate.label}: {failure}")
        if candidate.label == COMMAND_ENV:
            raise ToolError(f"{COMMAND_ENV} failed: {failure}")
        if candidate.source == "project":
            project_failures.append(f"{candidate.label}: {failure}")

    attempted = "\n  - ".join(failures)
    raise ToolError(
        "Ruff is unavailable. Install Ruff in the project or install uv/pipx for the "
        f"bundled fallback. Attempts:\n  - {attempted}"
    )


def discover_bundled_reference(root: Path) -> tuple[Runner | None, str | None]:
    failures: list[str] = []
    for candidate in bundled_candidates():
        version, failure = probe_candidate(candidate, root)
        if version is not None:
            return Runner(candidate.label, candidate.argv, version, candidate.source, root), None
        assert failure is not None
        failures.append(f"{candidate.label}: {failure}")
    if not failures:
        return None, "uvx or pipx is required to compare with the bundled Ruff reference"
    return None, "; ".join(failures)


def invoke_ruff(
    runner: Runner,
    arguments: Sequence[str],
    *,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    result = run_process((*runner.argv, *arguments), input_text=input_text, cwd=runner.root)
    if result.returncode not in (0, 1):
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise ToolError(f"Ruff failed: {detail}")
    return result


def intended_path(value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()


def settings_subject(path: Path, root: Path) -> tuple[Path, bool, bool]:
    if path.exists():
        return path, False, False
    for name in ("ruff.toml", ".ruff.toml", "pyproject.toml"):
        marker = root / name
        if marker.is_file():
            return marker, True, False
    for directory in (path.parent, root):
        if directory.is_dir():
            sibling = next(
                (
                    candidate
                    for pattern in ("*.py", "*.pyi")
                    for candidate in directory.glob(pattern)
                ),
                None,
            )
            if sibling is not None:
                return sibling, True, False
    return Path(__file__).resolve(), True, True


def mapping_block(settings: str, key: str) -> str:
    match = re.search(rf"(?ms)^{re.escape(key)} = \{{\n(.*?)^\}}", settings)
    return match.group(1) if match else ""


def parse_setting_string(value: str) -> str | None:
    value = value.strip()
    if value == "none":
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value.strip('"')
    return parsed if isinstance(parsed, str) else None


def matcher_entries(settings: str, key: str) -> tuple[MatcherEntry, ...]:
    block = mapping_block(settings, key)
    starts = [match.start() for match in re.finditer(r"(?m)^\s*absolute_matcher = ", block)]
    entries: list[MatcherEntry] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(block)
        chunk = block[start:end]
        absolute_match = re.search(r"(?m)^\s*absolute_matcher = (.+)$", chunk)
        basename_match = re.search(r"(?m)^\s*basename_matcher = (.+)$", chunk)
        negated_match = re.search(r"(?m)^\s*negated = (true|false)$", chunk)
        data_match = re.search(r"(?ms)^\s*data = (.*?)(?:\n\s*\n|\Z)", chunk)
        if not (absolute_match and basename_match and negated_match and data_match):
            continue
        entries.append(
            MatcherEntry(
                absolute_matcher=parse_setting_string(absolute_match.group(1)),
                basename_matcher=parse_setting_string(basename_match.group(1)),
                negated=negated_match.group(1) == "true",
                data=data_match.group(1).strip(),
            )
        )
    return tuple(entries)


def glob_matches(value: str, pattern: str) -> bool:
    normalized_value = value.replace("\\", "/")
    normalized_pattern = pattern.replace("\\", "/")
    return fnmatch.fnmatchcase(normalized_value, normalized_pattern)


def matcher_applies(entry: MatcherEntry, path: Path) -> bool:
    absolute = path.resolve().as_posix()
    matched = False
    if entry.absolute_matcher is not None:
        matched = glob_matches(absolute, entry.absolute_matcher)
    if not matched and entry.basename_matcher is not None:
        matched = glob_matches(path.name, entry.basename_matcher)
    return not matched if entry.negated else matched


def target_from_settings(settings: str, path: Path) -> tuple[str, str]:
    base_match = re.search(
        r"^linter\.unresolved_target_version = (3\.[0-9]+)$", settings, re.MULTILINE
    )
    if base_match is not None:
        target = base_match.group(1)
    else:
        legacy_match = re.search(r"^linter\.target_version = Py3?([0-9]+)$", settings, re.MULTILINE)
        if legacy_match is None:
            raise CapabilityError("Ruff did not report a resolved target Python version")
        target = f"3.{legacy_match.group(1)}"
    source = "Ruff resolved target"
    per_file_block = mapping_block(settings, "linter.per_file_target_version")
    per_file_entries = matcher_entries(settings, "linter.per_file_target_version")
    if per_file_block.strip() and not per_file_entries:
        raise CapabilityError(
            "Ruff reported per-file target versions in an unsupported format; pass "
            "--target-version explicitly"
        )
    for entry in per_file_entries:
        target_match = re.fullmatch(r"3\.[0-9]+", entry.data)
        if target_match and matcher_applies(entry, path):
            target = entry.data
            source = "per-file-target-version"
    return target, source


def cli_target(target_python: str) -> str:
    major, minor = target_python.split(".", 1)
    return f"py{major}{minor}"


def resolve_settings(
    runner: Runner,
    file_value: str,
    *,
    target_override: str | None = None,
    require_file: bool = False,
) -> tuple[ResolvedSettings, str]:
    path = intended_path(file_value)
    if require_file and path.exists() and not path.is_file():
        raise ToolError("--file must identify one Python file, not a directory")
    subject, inferred, isolated = settings_subject(path, runner.root)
    arguments = ["check", "--show-settings", str(subject)]
    if isolated:
        arguments.append("--isolated")
    result = invoke_ruff(runner, arguments)
    settings_text = result.stdout
    if target_override is not None:
        if not TARGET_VERSION.fullmatch(target_override):
            raise ToolError("--target-version must look like py310 or py312")
        digits = target_override.removeprefix("py3")
        target_python = f"3.{digits}"
        target_source = "command-line override"
    else:
        target_python, target_source = target_from_settings(settings_text, path)

    preview_match = re.search(r"^linter\.preview = (.+)$", settings_text, re.MULTILINE)
    preview = bool(preview_match and preview_match.group(1).strip() == "enabled")
    explicit_preview_match = re.search(
        r"^linter\.explicit_preview_rules = (true|false)$", settings_text, re.MULTILINE
    )
    explicit_preview_rules = bool(
        explicit_preview_match and explicit_preview_match.group(1) == "true"
    )
    config_match = re.search(r'^Settings path: "(.+)"$', settings_text, re.MULTILINE)
    config_path = Path(config_match.group(1)) if config_match else None
    if target_source == "Ruff resolved target" and config_path is not None:
        target_source = config_path.name

    return (
        ResolvedSettings(
            intended_file=path,
            subject=subject,
            target_python=target_python,
            target_version=cli_target(target_python),
            target_source=target_source,
            preview=preview,
            explicit_preview_rules=explicit_preview_rules,
            config_path=config_path,
            inferred_file_context=inferred,
        ),
        settings_text,
    )


def load_rule_inventory(runner: Runner) -> list[dict[str, object]]:
    failures: list[str] = []
    document: object | None = None
    for format_option in ("--output-format", "--format"):
        try:
            result = invoke_ruff(runner, ("rule", "--all", format_option, "json"))
            document = json.loads(result.stdout)
            break
        except (ToolError, json.JSONDecodeError) as exc:
            failures.append(str(exc))
    if document is None:
        raise CapabilityError(
            f"{runner.label} cannot provide machine-readable `ruff rule --all`: "
            + "; ".join(failures)
        )
    if not isinstance(document, list) or not all(isinstance(rule, dict) for rule in document):
        raise CapabilityError(f"{runner.label} returned an unsupported Ruff rule schema")
    return document


def rule_code(rule: dict[str, object]) -> str:
    code = rule.get("code")
    return code if isinstance(code, str) else ""


def rule_status(rule: dict[str, object]) -> str:
    status = rule.get("status")
    if isinstance(status, dict) and status:
        return str(next(iter(status)))
    if isinstance(status, str):
        return status.title()
    explanation = rule.get("explanation")
    if isinstance(explanation, str) and re.search(
        r"(?mi)^##\s+(?:Removed|Deprecated)\b", explanation
    ):
        return "Removed"
    return "Preview" if rule.get("preview") is True else "Stable"


def selector_matches(code: str, selectors: Sequence[str]) -> bool:
    return any(code.startswith(selector) for selector in selectors)


def active_rules(
    inventory: Sequence[dict[str, object]],
    selectors: Sequence[str],
    *,
    preview: bool,
) -> tuple[dict[str, object], ...]:
    rules: list[dict[str, object]] = []
    for rule in inventory:
        code = rule_code(rule)
        if not code or not selector_matches(code, selectors):
            continue
        status = rule_status(rule)
        if status == "Stable" or (preview and status == "Preview"):
            rules.append(rule)
    return tuple(sorted(rules, key=lambda rule: guideline_sort_key(rule_code(rule))))


def guideline_sort_key(code: str) -> tuple[int, str, int]:
    prefix_match = re.match(r"[A-Z]+", code)
    number_match = re.search(r"[0-9]+$", code)
    prefix = prefix_match.group(0) if prefix_match else code
    try:
        prefix_order = GUIDELINE_PREFIXES.index(prefix)
    except ValueError:
        prefix_order = len(GUIDELINE_PREFIXES)
    number = int(number_match.group(0)) if number_match else 0
    return prefix_order, prefix, number


def code_examples(rule: dict[str, object]) -> tuple[str, ...]:
    explanation = rule.get("explanation")
    if not isinstance(explanation, str):
        return ()
    return tuple(
        block.strip() + "\n" for block in PYTHON_FENCE.findall(explanation) if block.strip()
    )


def rule_description(rule: dict[str, object]) -> str:
    explanation = rule.get("explanation")
    if isinstance(explanation, str):
        match = re.search(r"(?ms)^## What it does\s*\n(.+?)(?:\n\s*\n|^## )", explanation)
        if match:
            return " ".join(match.group(1).split())
    summary = rule.get("summary")
    return str(summary) if summary is not None else str(rule.get("name", ""))


def file_suffix(path: Path) -> str:
    return path.suffix if path.suffix in {".py", ".pyi"} else ".py"


def diagnose_examples(
    runner: Runner,
    rules: Sequence[dict[str, object]],
    examples: dict[str, tuple[str, ...]],
    *,
    target_version: str,
    preview: bool,
    suffix: str,
    future_annotations: bool,
) -> frozenset[str]:
    if not rules:
        return frozenset()
    with tempfile.TemporaryDirectory(prefix="modern-python-guidelines-") as directory:
        example_root = Path(directory)
        written_codes: list[str] = []
        for rule in rules:
            code = rule_code(rule)
            blocks = examples.get(code, ())
            if not blocks:
                continue
            code_directory = example_root / code
            code_directory.mkdir()
            for index, block in enumerate(blocks, start=1):
                contents = block
                if future_annotations and not re.search(
                    r"(?m)^from __future__ import .*\bannotations\b", contents
                ):
                    contents = "from __future__ import annotations\n" + contents
                (code_directory / f"example_{index:03d}{suffix}").write_text(
                    contents, encoding="utf-8"
                )
            written_codes.append(code)
        if not written_codes:
            return frozenset()

        arguments = [
            "check",
            str(example_root),
            "--isolated",
            "--target-version",
            target_version,
            "--select",
            ",".join(written_codes),
            "--output-format",
            "json",
            "--exit-zero",
            "--ignore-noqa",
            "--no-cache",
        ]
        if preview:
            arguments.append("--preview")
        result = invoke_ruff(runner, arguments)
        try:
            diagnostics = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise CapabilityError(f"Ruff returned invalid diagnostic JSON: {exc}") from exc
        if not isinstance(diagnostics, list):
            raise CapabilityError("Ruff returned an unsupported diagnostic schema")

        applicable: set[str] = set()
        for diagnostic in diagnostics:
            if not isinstance(diagnostic, dict):
                continue
            code = diagnostic.get("code")
            filename = diagnostic.get("filename")
            if not isinstance(code, str) or not isinstance(filename, str):
                continue
            if Path(filename).parent.name == code:
                applicable.add(code)
        return frozenset(applicable)


def mentions_future_annotations(rule: dict[str, object]) -> bool:
    explanation = str(rule.get("explanation", "")).lower()
    return any(
        phrase in explanation
        for phrase in ("__future__", "future annotations", "postponed annotations")
    )


def build_guideline_set(
    runner: Runner,
    settings: ResolvedSettings,
    *,
    preview: bool,
) -> GuidelineSet:
    inventory = load_rule_inventory(runner)
    rules = active_rules(inventory, GUIDELINE_PREFIXES, preview=preview)
    examples = {rule_code(rule): code_examples(rule) for rule in rules}
    missing_examples = tuple(code for code, blocks in examples.items() if not blocks)
    baseline = diagnose_examples(
        runner,
        rules,
        examples,
        target_version=settings.target_version,
        preview=preview,
        suffix=file_suffix(settings.intended_file),
        future_annotations=False,
    )
    with_future = diagnose_examples(
        runner,
        rules,
        examples,
        target_version=settings.target_version,
        preview=preview,
        suffix=file_suffix(settings.intended_file),
        future_annotations=True,
    )
    by_code = {rule_code(rule): rule for rule in rules}
    conditional = frozenset(
        code
        for code in with_future - baseline
        if code in by_code and mentions_future_annotations(by_code[code])
    )
    return GuidelineSet(rules, examples, baseline, conditional, missing_examples)


def selection_settings(
    runner: Runner,
    settings: ResolvedSettings,
    selectors: Sequence[str],
    *,
    preview: bool,
    inherit_project: bool = True,
) -> str:
    selector_array = json.dumps(list(selectors))
    if inherit_project and settings.config_path is not None:
        contents = (
            f"extend = {json.dumps(str(settings.config_path))}\n\n"
            f"[lint]\nextend-select = {selector_array}\n"
        )
    else:
        contents = (
            f'target-version = "{settings.target_version}"\n\n[lint]\nselect = {selector_array}\n'
        )

    with tempfile.TemporaryDirectory(prefix="modern-python-policy-") as directory:
        config = Path(directory) / "ruff.toml"
        config.write_text(contents, encoding="utf-8")
        arguments = ["check", "--show-settings", str(settings.subject), "--config", str(config)]
        if preview:
            arguments.append("--preview")
        return invoke_ruff(runner, arguments).stdout


def enabled_rule_codes(settings_text: str) -> frozenset[str]:
    match = re.search(r"(?ms)^linter\.rules\.enabled = \[\n(.*?)^\]", settings_text)
    if match is None:
        raise CapabilityError("Ruff did not report its enabled rule set")
    return frozenset(re.findall(r"\(([A-Z]+[0-9]+)\)", match.group(1)))


def per_file_ignored_codes(settings_text: str, path: Path) -> frozenset[str]:
    ignored: set[str] = set()
    for entry in matcher_entries(settings_text, "linter.per_file_ignores"):
        if matcher_applies(entry, path):
            ignored.update(re.findall(r"\(([A-Z]+[0-9]+)\)", entry.data))
    return frozenset(ignored)


def string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CapabilityError("Ruff ignore selectors must be a TOML array of strings")
    return tuple(value)


def directives_from_tomllib(path: Path) -> IgnoreDirectives:
    assert tomllib is not None
    try:
        with path.open("rb") as file:
            document = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CapabilityError(f"could not read Ruff configuration {path}: {exc}") from exc
    tool = document.get("tool")
    if isinstance(tool, dict) and isinstance(tool.get("ruff"), dict):
        ruff = tool["ruff"]
    else:
        ruff = document
    lint = ruff.get("lint", {})
    if not isinstance(lint, dict):
        lint = {}
    ignore_value = lint.get("ignore", ruff.get("ignore"))
    extend_ignore_value = lint.get("extend-ignore", ruff.get("extend-ignore", []))
    extend_value = ruff.get("extend")
    return IgnoreDirectives(
        ignore=None if ignore_value is None else string_tuple(ignore_value),
        extend_ignore=string_tuple(extend_ignore_value),
        extend=extend_value if isinstance(extend_value, str) else None,
    )


def quoted_toml_strings(value: str) -> tuple[str, ...]:
    return tuple(match.group(2) for match in re.finditer(r"(['\"])(.*?)\1", value, re.DOTALL))


def directives_from_text(path: Path) -> IgnoreDirectives:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CapabilityError(f"could not read Ruff configuration {path}: {exc}") from exc
    is_pyproject = path.name == "pyproject.toml"
    table = ""
    values: dict[str, str] = {}
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        table_match = re.fullmatch(r"\[([^]]+)]", stripped)
        if table_match:
            table = table_match.group(1)
            index += 1
            continue
        assignment = re.match(r"^([A-Za-z0-9_.-]+)\s*=\s*(.*)$", stripped)
        if assignment:
            key, value = assignment.groups()
            relevant_table = table in (
                {"tool.ruff", "tool.ruff.lint"} if is_pyproject else {"", "lint"}
            )
            relevant_key = key in {
                "ignore",
                "extend-ignore",
                "extend",
                "lint.ignore",
                "lint.extend-ignore",
            }
            if relevant_table and relevant_key:
                while value.count("[") > value.count("]") and index + 1 < len(lines):
                    index += 1
                    value += "\n" + lines[index]
                values[f"{table}:{key}"] = value
        index += 1

    lint_prefix = "tool.ruff.lint" if is_pyproject else "lint"
    root_prefix = "tool.ruff" if is_pyproject else ""

    def find_value(*keys: str) -> str | None:
        return next((values[key] for key in keys if key in values), None)

    ignore_text = find_value(
        f"{lint_prefix}:ignore",
        f"{root_prefix}:lint.ignore",
        f"{root_prefix}:ignore",
    )
    extend_ignore_text = find_value(
        f"{lint_prefix}:extend-ignore",
        f"{root_prefix}:lint.extend-ignore",
        f"{root_prefix}:extend-ignore",
    )
    extend_text = find_value(f"{root_prefix}:extend")
    extend_values = quoted_toml_strings(extend_text or "")
    return IgnoreDirectives(
        ignore=None if ignore_text is None else quoted_toml_strings(ignore_text),
        extend_ignore=quoted_toml_strings(extend_ignore_text or ""),
        extend=extend_values[0] if extend_values else None,
    )


def ignore_directives(path: Path) -> IgnoreDirectives:
    return directives_from_tomllib(path) if tomllib is not None else directives_from_text(path)


def resolved_ignore_selectors(
    path: Path | None, seen: frozenset[Path] = frozenset()
) -> frozenset[str]:
    if path is None:
        return frozenset()
    resolved = path.resolve()
    if resolved in seen:
        raise CapabilityError(f"cyclic Ruff configuration extension at {resolved}")
    directives = ignore_directives(resolved)
    inherited: frozenset[str] = frozenset()
    if directives.extend is not None:
        extended = Path(directives.extend).expanduser()
        if not extended.is_absolute():
            extended = resolved.parent / extended
        inherited = resolved_ignore_selectors(extended, seen | {resolved})
    selectors = inherited if directives.ignore is None else frozenset(directives.ignore)
    return selectors | frozenset(directives.extend_ignore)


def global_ignored_codes(
    settings: ResolvedSettings,
    settings_text: str,
    candidate_codes: frozenset[str],
) -> frozenset[str]:
    selectors = resolved_ignore_selectors(settings.config_path)
    ignored = frozenset(
        code for code in candidate_codes if any(code.startswith(selector) for selector in selectors)
    )
    # A more-specific explicit selection can override a broader ignore in Ruff.
    native_enabled = enabled_rule_codes(settings_text)
    return ignored - native_enabled


def resolve_policy(
    settings: ResolvedSettings,
    settings_text: str,
    candidate_codes: frozenset[str],
    *,
    preview_codes_requiring_native_selection: frozenset[str] = frozenset(),
) -> Policy:
    globally_ignored = global_ignored_codes(settings, settings_text, candidate_codes)
    enabled = candidate_codes - globally_ignored
    native_enabled = enabled_rule_codes(settings_text)
    enabled -= preview_codes_requiring_native_selection - native_enabled
    per_file = per_file_ignored_codes(settings_text, settings.intended_file) & candidate_codes
    return Policy(enabled, per_file)


def parse_version(version_text: str) -> tuple[int, int, int] | None:
    match = re.search(r"(?:^|\s)(\d+)\.(\d+)\.(\d+)(?:\s|$)", version_text)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def reference_differences(
    project: GuidelineSet,
    project_policy: Policy,
    reference: GuidelineSet,
    reference_policy: Policy,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    reference_available = reference.available_codes & reference_policy.allowed
    missing = tuple(sorted(reference_available - project.active_codes, key=guideline_sort_key))
    project_available = project.available_codes & project_policy.allowed
    retired = tuple(sorted(project_available - reference.active_codes, key=guideline_sort_key))
    return missing, retired


def selected_rules(args: argparse.Namespace) -> tuple[str, ...]:
    if args.rules:
        rules = tuple(
            dict.fromkeys(part.strip().upper() for part in args.rules.split(",") if part.strip())
        )
        if not rules:
            raise ToolError("--rules must contain at least one Ruff rule prefix")
        invalid = next((rule for rule in rules if not RULE_SELECTOR.fullmatch(rule)), None)
        if invalid is not None:
            raise ToolError(f"invalid Ruff rule selector: {invalid!r}")
        return rules
    return PROFILES[args.profile]


def check_arguments(
    args: argparse.Namespace,
    *,
    fix: bool,
    ignored_codes: Sequence[str] = (),
) -> list[str]:
    paths = [str(intended_path(path)) for path in args.paths] if args.paths else [str(Path.cwd())]
    command = [
        "check",
        *paths,
        "--select",
        ",".join(selected_rules(args)),
        "--output-format",
        "json",
        "--exit-zero",
    ]
    if ignored_codes:
        command.extend(("--ignore", ",".join(ignored_codes)))
    if args.preview:
        command.append("--preview")
    if fix:
        command.append("--fix")
        if args.unsafe_fixes:
            command.append("--unsafe-fixes")
    return command


def command_probe(args: argparse.Namespace, runner: Runner) -> None:
    settings, _ = resolve_settings(runner, args.file)
    payload = {
        "runner": runner.label,
        "runner_source": runner.source,
        "command": list(runner.argv),
        "ruff_version": runner.version.removeprefix("ruff "),
        "target_python": settings.target_python,
        "target_source": settings.target_source,
        "preview": settings.preview,
        "explicit_preview_rules": settings.explicit_preview_rules,
        "inferred_file_context": settings.inferred_file_context,
        "default_profile": DEFAULT_PROFILE,
        "default_rule_prefixes": list(PROFILES[DEFAULT_PROFILE]),
        "warnings": list(runner.warnings),
    }
    print_json(payload)


def command_check(args: argparse.Namespace, runner: Runner, *, fix: bool) -> None:
    selectors = selected_rules(args)
    context = args.paths[0] if args.paths else "."
    settings, settings_text = resolve_settings(runner, context)
    preview = settings.preview or args.preview
    try:
        clean_selection = selection_settings(
            runner, settings, selectors, preview=preview, inherit_project=False
        )
        candidate_codes = enabled_rule_codes(clean_selection)
        ignored_codes = tuple(
            sorted(global_ignored_codes(settings, settings_text, candidate_codes))
        )
    except (CapabilityError, ToolError) as exc:
        raise ToolError(
            f"could not resolve the project's explicit Ruff ignores safely before check/fix: {exc}"
        ) from exc
    result = invoke_ruff(
        runner,
        check_arguments(args, fix=fix, ignored_codes=ignored_codes),
    )
    print(result.stdout.strip() or "[]")


def command_explain(args: argparse.Namespace, runner: Runner) -> None:
    rules: list[dict[str, object]] = []
    for code in dict.fromkeys(args.codes):
        normalized = code.strip().upper()
        if not RULE_CODE.fullmatch(normalized):
            raise ToolError(f"invalid Ruff rule code: {code!r}")
        rule: object | None = None
        failures: list[str] = []
        for format_option in ("--output-format", "--format"):
            try:
                result = invoke_ruff(runner, ("rule", normalized, format_option, "json"))
                rule = json.loads(result.stdout)
                break
            except (ToolError, json.JSONDecodeError) as exc:
                failures.append(str(exc))
        if not isinstance(rule, dict):
            raise ToolError(f"Ruff could not explain {normalized} as JSON: " + "; ".join(failures))
        rules.append(rule)
    print_json({"ruff_version": runner.version.removeprefix("ruff "), "rules": rules})


def rule_payload(
    rule: dict[str, object],
    *,
    applicability: str,
) -> dict[str, object]:
    return {
        "code": rule_code(rule),
        "name": rule.get("name"),
        "description": rule_description(rule),
        "status": rule_status(rule),
        "applicability": applicability,
    }


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def render_guidelines(payload: dict[str, object]) -> None:
    if payload.get("format") == "json":
        document = dict(payload)
        document.pop("format", None)
        print_json(document)
        return

    print("Modern Python Guidelines")
    print(f"File: {payload['file']}")
    print(f"Target: Python {payload['target_python']} ({payload['target_source']})")
    print(f"Ruff: {payload['ruff_version']} — {payload['runner']} [{payload['runner_source']}]")
    print(f"Preview: {'enabled' if payload['preview'] else 'disabled'}")
    print(
        f"Guidelines: {payload['baseline_count']} baseline, "
        f"{payload['conditional_count']} conditional, "
        f"{payload['ignored_count']} ignored by project policy"
    )
    ignored_codes = payload.get("ignored_codes", [])
    if ignored_codes:
        print(f"Ignored: {', '.join(ignored_codes)}")
    print()
    print(
        "Baseline means Ruff's official examples diagnose under the resolved target. "
        "The post-edit check can still surface configuration-dependent guidance."
    )
    warnings = payload.get("warnings", [])
    if warnings:
        print()
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")

    baseline = payload.get("baseline", [])
    if baseline:
        print()
        print("Baseline guidelines:")
        for rule in baseline:
            print(f"{rule['code']} {rule['description']}")

    conditional = payload.get("conditional", [])
    if conditional:
        print()
        print("Conditional with postponed annotations:")
        for rule in conditional:
            print(f"{rule['code']} {rule['description']}")


def degraded_list_payload(
    args: argparse.Namespace,
    runner: Runner,
    settings: ResolvedSettings,
    warning: str,
) -> dict[str, object]:
    return {
        "format": args.format,
        "status": "degraded",
        "file": display_path(settings.intended_file),
        "target_python": settings.target_python,
        "target_source": settings.target_source,
        "runner": runner.label,
        "runner_source": runner.source,
        "ruff_version": runner.version.removeprefix("ruff "),
        "preview": settings.preview or args.preview,
        "explicit_preview_rules": settings.explicit_preview_rules,
        "baseline_count": 0,
        "conditional_count": 0,
        "ignored_count": 0,
        "ignored_codes": [],
        "baseline": [],
        "conditional": [],
        "warnings": [*runner.warnings, warning],
    }


def command_list(args: argparse.Namespace, runner: Runner) -> None:
    settings, settings_text = resolve_settings(
        runner,
        args.file,
        target_override=args.target_version,
        require_file=True,
    )
    preview_mode = settings.preview or args.preview
    include_preview_rules = preview_mode or settings.explicit_preview_rules
    warnings = list(runner.warnings)
    if settings.inferred_file_context:
        warnings.append(
            "The requested file does not exist; Ruff settings were resolved from the nearest "
            "project configuration."
        )

    try:
        guidelines = build_guideline_set(runner, settings, preview=include_preview_rules)
    except CapabilityError as exc:
        if runner.source == "bundled":
            raise
        if not args.allow_stale:
            raise RuffUpdateRequired(
                f"{exc}. Ask the user whether to update the project's Ruff. Do not update it "
                "without approval. If they decline, rerun `list --allow-stale` and use the "
                "post-edit check as the safe fallback."
            ) from exc
        render_guidelines(
            degraded_list_payload(
                args,
                runner,
                settings,
                f"Proactive rule metadata is unavailable: {exc}. Use check after editing.",
            )
        )
        return

    preview_codes = frozenset(
        rule_code(rule) for rule in guidelines.rules if rule_status(rule) == "Preview"
    )
    policy = resolve_policy(
        settings,
        settings_text,
        guidelines.active_codes,
        preview_codes_requiring_native_selection=(
            preview_codes if settings.explicit_preview_rules and not args.preview else frozenset()
        ),
    )
    missing_reference: tuple[str, ...] = ()
    retired_reference: tuple[str, ...] = ()
    selected_version = parse_version(runner.version)
    pinned_version = parse_version(f"ruff {pinned_ruff_version()}")
    should_compare = (
        runner.source != "bundled"
        and selected_version is not None
        and pinned_version is not None
        and selected_version < pinned_version
    )
    if should_compare:
        reference, failure = discover_bundled_reference(runner.root)
        if reference is None:
            warnings.append(
                "Could not compare the project Ruff with the bundled reference; continuing with "
                f"the project Ruff. {failure}"
            )
        else:
            reference_guidelines = build_guideline_set(
                reference, settings, preview=include_preview_rules
            )
            reference_policy = resolve_policy(
                settings,
                settings_text,
                reference_guidelines.active_codes,
                preview_codes_requiring_native_selection=(
                    frozenset(
                        rule_code(rule)
                        for rule in reference_guidelines.rules
                        if rule_status(rule) == "Preview"
                    )
                    if settings.explicit_preview_rules and not args.preview
                    else frozenset()
                ),
            )
            missing_reference, retired_reference = reference_differences(
                guidelines,
                policy,
                reference_guidelines,
                reference_policy,
            )

    if (missing_reference or retired_reference) and not args.allow_stale:
        differences: list[str] = []
        if missing_reference:
            shown = ", ".join(missing_reference[:12])
            remainder = len(missing_reference) - 12
            suffix = f" (and {remainder} more)" if remainder > 0 else ""
            differences.append(
                f"{len(missing_reference)} newer target-compatible guideline(s): {shown}{suffix}"
            )
        if retired_reference:
            differences.append(
                f"{len(retired_reference)} retired guideline(s): "
                + ", ".join(retired_reference[:12])
            )
        raise RuffUpdateRequired(
            f"{runner.label} {runner.version.removeprefix('ruff ')} differs materially from the "
            f"bundled Ruff {pinned_ruff_version()} ({'; '.join(differences)}). Ask the user "
            "whether to update Ruff "
            "through the project's dependency manager. Do not update automatically. If they "
            "decline, rerun `list --allow-stale` and do not ask again during this task."
        )
    if missing_reference:
        warnings.append(
            f"Continuing with the project Ruff after an explicit stale override; "
            f"{len(missing_reference)} newer guideline(s) are unavailable."
        )
    if retired_reference:
        warnings.append(
            f"Excluded {len(retired_reference)} guideline(s) that the bundled Ruff marks retired: "
            + ", ".join(retired_reference)
        )
    if guidelines.missing_example_codes:
        warnings.append(
            "Ruff documentation had no executable Python example for: "
            + ", ".join(guidelines.missing_example_codes)
        )

    baseline_codes = guidelines.baseline_codes & policy.allowed - set(retired_reference)
    conditional_codes = guidelines.conditional_codes & policy.allowed - set(retired_reference)
    ignored_codes = sorted(
        guidelines.available_codes - policy.allowed,
        key=guideline_sort_key,
    )
    rules_by_code = {rule_code(rule): rule for rule in guidelines.rules}
    baseline = [
        rule_payload(rules_by_code[code], applicability="baseline")
        for code in sorted(baseline_codes, key=guideline_sort_key)
    ]
    conditional = [
        rule_payload(rules_by_code[code], applicability="postponed-annotations")
        for code in sorted(conditional_codes, key=guideline_sort_key)
    ]
    payload: dict[str, object] = {
        "format": args.format,
        "status": "ok",
        "file": display_path(settings.intended_file),
        "target_python": settings.target_python,
        "target_version": settings.target_version,
        "target_source": settings.target_source,
        "runner": runner.label,
        "runner_source": runner.source,
        "ruff_version": runner.version.removeprefix("ruff "),
        "preview": preview_mode,
        "explicit_preview_rules": settings.explicit_preview_rules,
        "inferred_file_context": settings.inferred_file_context,
        "baseline_count": len(baseline),
        "conditional_count": len(conditional),
        "ignored_count": len(ignored_codes),
        "ignored_codes": ignored_codes,
        "baseline": baseline,
        "conditional": conditional,
        "warnings": warnings,
    }
    render_guidelines(payload)


def print_json(value: object) -> None:
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

    list_parser = subparsers.add_parser(
        "list", help="List compact, target-aware guidance before editing Python"
    )
    list_parser.add_argument("--file", required=True, help="Python file that will be edited")
    list_parser.add_argument(
        "--target-version",
        help="Explicit Ruff target such as py312; useful for a new file with ambiguous settings",
    )
    list_parser.add_argument(
        "--preview", action="store_true", help="Include Ruff preview guidelines"
    )
    list_parser.add_argument(
        "--allow-stale",
        action="store_true",
        help="Continue with an older project Ruff after the user declines an update",
    )
    list_parser.add_argument("--format", choices=("text", "json"), default="text")

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
    explain.add_argument(
        "--file",
        default=".",
        help="Relevant Python file, used to select the same project Ruff as list",
    )
    explain.add_argument("codes", nargs="+", help="Ruff rule codes from list or check output")

    return parser


def runner_start(args: argparse.Namespace) -> Path:
    if args.command in {"list", "probe", "explain"}:
        path = intended_path(args.file)
    elif args.command in {"check", "fix"} and args.paths:
        path = intended_path(args.paths[0])
    else:
        path = Path.cwd()
    return path if path.is_dir() else path.parent


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = find_project_root(runner_start(args))
        runner = discover_runner(root)
        if args.command == "list":
            command_list(args, runner)
        elif args.command == "probe":
            command_probe(args, runner)
        elif args.command == "check":
            command_check(args, runner, fix=False)
        elif args.command == "fix":
            command_check(args, runner, fix=True)
        elif args.command == "explain":
            command_explain(args, runner)
        else:  # pragma: no cover - argparse prevents this
            raise ToolError(f"unsupported command: {args.command}")
    except RuffUpdateRequired as exc:
        print(f"modern-python-guidelines: {exc}", file=sys.stderr)
        return 3
    except ToolError as exc:
        print(f"modern-python-guidelines: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
