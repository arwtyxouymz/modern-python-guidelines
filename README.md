# Modern Python Guidelines

[![CI](https://github.com/arwtyxouymz/modern-python-guidelines/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/arwtyxouymz/modern-python-guidelines/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Powered by Ruff](https://img.shields.io/badge/Powered%20by-Ruff-D7FF64?logo=ruff&logoColor=261230)](https://docs.astral.sh/ruff/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> [!NOTE]
> This project is strongly inspired by
> [JetBrains/go-modern-guidelines](https://github.com/JetBrains/go-modern-guidelines).

Modern Python guidance for AI coding agents, backed by Ruff instead of a
hand-maintained rule database.

The plugin makes an agent:

1. resolve the target file's Ruff runner, Python version, preview mode, and ignores,
2. read a compact list of applicable modernization guidance **before editing**,
3. retrieve full documentation only for rule IDs relevant to the planned code,
4. write the code, then check it with a broader modern-Python profile, and
5. apply safe fixes and run the project's normal verification afterward.

Ruff remains the source of truth. The bundled tool does not scrape Python release
notes, rewrite Ruff's rules, or maintain a second set of recommendations.

## Why this works

Ruff already maintains machine-readable rules derived from projects such as
`pyupgrade`, `refurb`, `flake8-simplify`, `flake8-use-pathlib`, and `flynt`. It
also understands the project's `target-version` or `requires-python` setting.
Before editing, the plugin reads Ruff's runtime JSON metadata, extracts Ruff's own
Python examples into a temporary directory, and checks them in one batch against
the target version. No generated rule JSON is committed to this repository.

The resulting list has two groups:

- **Baseline**: Ruff's documented examples diagnose under the resolved target.
- **Conditional**: the same guidance becomes available with postponed annotations,
  such as `from __future__ import annotations`.

This executable-example check is a verified baseline, not a proof of every possible
configuration. The post-edit check remains necessary and can surface rules whose
behavior depends on the actual code or other Ruff settings.

Ruff's current rule status is authoritative: Stable rules are considered, Preview
rules require project or command-line opt-in, and Removed rules are excluded. When
the project Ruff is older than the bundled reference, the tool compares actual
target-compatible rule sets in both directions so newly available and retired
guidance are detected without treating a version-number difference alone as stale.

Two profiles are available:

| Profile | Ruff prefixes | Purpose |
| --- | --- | --- |
| `core` | `UP,FURB,F401` | Language/API modernization plus unused-import cleanup |
| `modern` | `UP,FURB,SIM,C4,PIE,PTH,FLY,PERF,F401` | Broader modern and Pythonic guidance; the default |

Preview rules are excluded unless explicitly requested. `fix` applies Ruff's safe
fixes only; unsafe fixes require an explicit flag and should be reviewed for
behavior changes. `F401` is included because modernization frequently makes old
compatibility imports unused.

Project `lint.ignore` and matching `per-file-ignores` are preserved by both the
pre-edit list and post-edit checks, even though a plain Ruff CLI `--select` would
otherwise take precedence over global ignores. Per-file target versions are also
resolved for the requested path rather than inferred from the base target alone.

## Requirements

- Python 3.10 or newer
- Ruff available in the project, on `PATH`, or through a supported environment
- `uv` or `pipx` only when Ruff is not otherwise available

Runner priority is: an explicit `MODERN_PYTHON_RUFF_COMMAND`, project `.venv`,
Pixi, Poetry/PDM/Pipenv, `PATH`, the current Python environment, then the bundled
Ruff version through `uvx` or `pipx`. Pixi projects are detected from
`pixi.toml`, `pixi.lock`, or `[tool.pixi...]` tables in `pyproject.toml`, and the
tool runs `pixi run ruff`. For a non-default Pixi environment, set an explicit
command such as `MODERN_PYTHON_RUFF_COMMAND="pixi run --environment dev ruff"`.
The fallback is cached outside the target project and does not add dependencies
to it.

Runner provenance is reported as `project`, `ambient`, or `bundled`. Discovery is
anchored at the requested file, which keeps nested projects and monorepos from
accidentally using the caller's working-directory configuration. If a detected
project runner is unavailable and another runner is selected, the result includes a
warning instead of silently presenting it as the project toolchain.

When an older project Ruff is in use, `list` may invoke the pinned Ruff through
`uvx` or `pipx` only to compare rule capabilities. That can cause a one-time cached
download. If the comparison is unavailable offline, the project Ruff remains the
source of truth and the tool continues with an explicit warning.

## Ruff update consent

`list` exits with status 3 only when the selected Ruff cannot provide the required
metadata or when comparison finds a material applicable-rule difference. The agent
must then ask whether to update Ruff; it never edits dependencies or lockfiles on
its own.

If approved, the agent updates Ruff through the project's existing manager, such as
Pixi, uv, Poetry, PDM, or pre-commit, and reruns `list`. If declined, it reruns with
`--allow-stale` and continues with the project's Ruff for that task. Very old Ruff
releases without machine-readable rule metadata degrade safely to the resolved
target plus the post-edit check.

## Installation

### Codex

```bash
codex plugin marketplace add arwtyxouymz/modern-python-guidelines
codex plugin add modern-python-guidelines@modern-python-codex-marketplace
```

Update by refreshing the marketplace and reinstalling the plugin:

```bash
codex plugin marketplace upgrade modern-python-codex-marketplace
codex plugin remove modern-python-guidelines@modern-python-codex-marketplace
codex plugin add modern-python-guidelines@modern-python-codex-marketplace
```

### Claude Code

Inside Claude Code:

```text
/plugin marketplace add arwtyxouymz/modern-python-guidelines
/plugin install modern-python-guidelines@modern-python-claude-marketplace
```

The skill is selected automatically for Python implementation and refactoring.
It can also be invoked explicitly as `/modern-python-guidelines:use-modern-python`.

### Cursor

```bash
cursor-agent plugin marketplace add https://github.com/arwtyxouymz/modern-python-guidelines
```

Then install **Modern Python Guidelines** from `/plugins` in a Cursor agent session.

### Junie CLI

Inside Junie CLI:

```text
/extensions marketplace add arwtyxouymz/modern-python-guidelines
/extensions install modern-python-guidelines
```

### Other Agent Skills clients

For clients supported by `skills.sh`:

```bash
npx skills add arwtyxouymz/modern-python-guidelines --skill use-modern-python
```

## Tool commands

The agent normally calls these through the skill wrappers:

```bash
# macOS / Linux
sh <skill-dir>/scripts/run-tool.sh list --file src/example.py
sh <skill-dir>/scripts/run-tool.sh list --file src/new_file.py --target-version py312
sh <skill-dir>/scripts/run-tool.sh probe --file src/example.py
sh <skill-dir>/scripts/run-tool.sh check src/example.py
sh <skill-dir>/scripts/run-tool.sh explain --file src/example.py UP045
sh <skill-dir>/scripts/run-tool.sh fix src/example.py

# Windows PowerShell
& <skill-dir>\scripts\run-tool.ps1 list --file src\example.py
```

The agent reads the complete compact `list` output, requests `explain` only for IDs
that could affect the planned change, and then starts editing. `probe` is a debugging
command for runner and target resolution; it is not the pre-edit guidance step.

## Automatic dependency updates

The bundled fallback is pinned as a standard pip requirement in
`ruff-fallback.txt`. Dependabot watches that pin and the GitHub Actions used by
this repository. Its pull requests are squash-merged automatically only after
the complete CI workflow, including a real Ruff integration smoke test, passes.

Runtime checks still prefer the target project's Ruff, so an automatically
updated fallback never overrides a project's locked toolchain.

CI validates metadata extraction, documented examples, postponed-annotation cases,
Python 3.8 through 3.14 targets, per-file targeting, project ignores, and the full
`list → explain → fix → check` flow. This makes a Dependabot fallback update
auto-mergeable without introducing a hand-maintained rule catalog.

## Development

```bash
python -m unittest discover -s tests -v
python scripts/validate_distribution.py

RUFF_REQUIREMENT=$(cat plugins/modern-python-guidelines/skills/use-modern-python/scripts/ruff-fallback.txt)
uvx --from "$RUFF_REQUIREMENT" ruff check .
uvx --from "$RUFF_REQUIREMENT" ruff format --check .
```

## License

MIT
