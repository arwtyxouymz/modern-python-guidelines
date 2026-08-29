---
name: use-modern-python
description: Use Ruff-backed, version-aware guidance whenever writing, modifying, fixing, or refactoring Python code. Apply relevant modern Python idioms and verify the resulting changes.
---

# Modern Python Guidelines

Use the bundled CLI as the source of truth for modernization guidance. It delegates
to the target project's Ruff when available and otherwise uses a pinned fallback
without modifying the project.

Commands:

- macOS or Linux: `sh "<skill-dir>/scripts/run-tool.sh"`
- Windows PowerShell: `& "<skill-dir>\scripts\run-tool.ps1"`

## Before editing Python

Run `probe` for a relevant file:

```sh
sh "<skill-dir>/scripts/run-tool.sh" probe --file path/to/file.py
```

Use the reported target Python version as a compatibility boundary. Preserve a
project's explicit Ruff configuration and established conventions unless the user
asks to change them.

## After editing Python

1. Check only the files or focused directory touched by the task:

   ```sh
   sh "<skill-dir>/scripts/run-tool.sh" check path/to/file.py
   ```

2. Read the complete JSON output. For every diagnostic that may apply, retrieve
   its authoritative explanation before deciding how to handle it:

   ```sh
   sh "<skill-dir>/scripts/run-tool.sh" explain UP045 FURB123
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

The default `modern` profile checks `UP,FURB,SIM,C4,PIE,PTH,FLY,PERF,F401`.
Use `--profile core` for the narrower `UP,FURB,F401` set when the task calls for
a conservative modernization pass. `F401` cleans imports made unused by safe
modernization fixes. Preview rules remain disabled unless the user or project
explicitly opts into them.

Do not call `catalog` during ordinary edits. It exports the whole selected rule
inventory for maintenance and audits; diagnostics plus targeted `explain` calls
provide the intended progressive disclosure.
