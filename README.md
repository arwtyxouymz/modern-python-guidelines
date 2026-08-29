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

1. resolve the project's Python target through Ruff,
2. check edited code with version-aware modernization rules,
3. retrieve full documentation only for diagnostics that actually occur,
4. apply safe fixes or make a reasoned manual change, and
5. run the project's normal verification afterward.

Ruff remains the source of truth. The bundled tool does not scrape Python release
notes, rewrite Ruff's rules, or maintain a second set of recommendations.

## Why this works

Ruff already maintains machine-readable rules derived from projects such as
`pyupgrade`, `refurb`, `flake8-simplify`, `flake8-use-pathlib`, and `flynt`. It
also understands the project's `target-version` or `requires-python` setting.
The plugin uses Ruff's diagnostics as a retrieval step, so the agent sees only
guidance relevant to the code it touched.

Two profiles are available:

| Profile | Ruff prefixes | Purpose |
| --- | --- | --- |
| `core` | `UP,FURB,F401` | Language/API modernization plus unused-import cleanup |
| `modern` | `UP,FURB,SIM,C4,PIE,PTH,FLY,PERF,F401` | Broader modern and Pythonic guidance; the default |

Preview rules are excluded unless explicitly requested. `fix` applies Ruff's safe
fixes only; unsafe fixes require an explicit flag and should be reviewed for
behavior changes. `F401` is included because modernization frequently makes old
compatibility imports unused.

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
sh <skill-dir>/scripts/run-tool.sh probe --file src/example.py
sh <skill-dir>/scripts/run-tool.sh check src/example.py
sh <skill-dir>/scripts/run-tool.sh explain UP045
sh <skill-dir>/scripts/run-tool.sh fix src/example.py

# Windows PowerShell
& <skill-dir>\scripts\run-tool.ps1 probe --file src\example.py
```

## Automatic dependency updates

The bundled fallback is pinned as a standard pip requirement in
`ruff-fallback.txt`. Dependabot watches that pin and the GitHub Actions used by
this repository. Its pull requests are squash-merged automatically only after
the complete CI workflow, including a real Ruff integration smoke test, passes.

Runtime checks still prefer the target project's Ruff, so an automatically
updated fallback never overrides a project's locked toolchain.

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
