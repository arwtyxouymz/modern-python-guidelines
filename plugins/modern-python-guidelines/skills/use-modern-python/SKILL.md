---
name: use-modern-python
description: Use Ruff-backed, version-aware guidance whenever writing, modifying, fixing, or refactoring Python code. Read relevant modern idioms before editing and verify the resulting changes.
---

# Modern Python Guidelines

Use the bundled CLI as the source of truth for modernization guidance. It delegates
to the target project's Ruff when available and otherwise uses a pinned fallback
without modifying the project.

Commands:

- macOS or Linux: `sh "<skill-dir>/scripts/run-tool.sh"`
- Windows PowerShell: `& "<skill-dir>\scripts\run-tool.ps1"`

## Before editing Python

1. Run `list` for the file that will be edited, before choosing Python syntax or
   APIs:

   ```sh
   sh "<skill-dir>/scripts/run-tool.sh" list --file path/to/file.py
   ```

2. If `list` exits with status 3, it found a material Ruff capability or guideline
   difference. Ask the user whether to update the project's Ruff. Do not update a
   dependency or lockfile without approval.

   - If approved, inspect how the project manages Ruff (Pixi, uv, Poetry, PDM,
     pre-commit, or another tool), update it through that existing mechanism, and
     rerun `list`.
   - If declined, rerun the same command with `--allow-stale`. Do not ask again in
     the same task. A degraded result may have no proactive list; in that case use
     the resolved target as the boundary and rely on the post-edit check.

3. Read the complete compact output. Baseline rules are verified from Ruff's
   executable documentation examples for the resolved target. Conditional rules
   require postponed annotations. The post-edit check can still find
   configuration-dependent cases that examples cannot prove proactively.

4. For every listed rule that may affect the planned code, read its authoritative
   explanation before editing:

   ```sh
   sh "<skill-dir>/scripts/run-tool.sh" explain --file path/to/file.py UP045 FURB123
   ```

5. Only then write or modify the Python. Use the reported target version as a
   compatibility boundary. Preserve explicit Ruff ignores and established project
   conventions unless the user asks to change them.

For a new path, `list` resolves the nearest project configuration and says that the
file context was inferred. If per-file targeting remains ambiguous, rerun with an
explicit target such as `--target-version py312`. `probe` remains available for
runner and settings debugging; it does not replace `list`.

## After editing Python

1. Check only the files or focused directory touched by the task:

   ```sh
   sh "<skill-dir>/scripts/run-tool.sh" check path/to/file.py
   ```

2. Read the complete JSON output. For every diagnostic that may apply, retrieve
   its authoritative explanation before deciding how to handle it:

   ```sh
   sh "<skill-dir>/scripts/run-tool.sh" explain --file path/to/file.py UP045 FURB123
   ```

3. Apply the guidance when it preserves intended behavior and fits the edited
   code. Do not mechanically force a diagnostic whose documented caveat applies.

4. Safe automatic fixes may be applied with:

   ```sh
   sh "<skill-dir>/scripts/run-tool.sh" fix path/to/file.py
   ```

   This does not enable Ruff's unsafe fixes. Use `--unsafe-fixes` only after
   explaining the behavior risk and only when the task authorizes that change.

5. Run `check` again, then run the project's formatter, type checker, and tests
   relevant to the changed code. The modernization check supplements the
   project's verification; it does not replace it.

## Profiles

The default `modern` post-edit profile checks
`UP,FURB,SIM,C4,PIE,PTH,FLY,PERF,F401`. Use `--profile core` for the narrower
`UP,FURB,F401` set when the task calls for a conservative modernization pass.
`F401` cleans imports made unused by safe modernization fixes. Preview rules remain
disabled unless the user or project explicitly opts into them.

`list` intentionally loads the complete compact UP/FURB guidance before editing,
matching the proactive workflow. Keep full rule documentation progressive: call
`explain` only for IDs relevant to the planned or diagnosed code.
