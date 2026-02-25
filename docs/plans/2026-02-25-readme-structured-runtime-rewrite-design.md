# README Structured Runtime Rewrite Design

Date: 2026-02-25

## Goal

Rewrite the README CLI guide so it reflects the new structured runtime contract:

- `capabilities` is slim (`parameters`, `action_commands` only)
- `showall` is the legacy full payload command
- `set`/`act` are primarily `--arg key=value` driven
- `get` can return multi-field structures
- set autofill/defaulting behavior is explicitly documented

## Scope

Use Option 2 (full command guide rewrite), not a narrow patch.

Sections to refresh:

1. Inspect/introspect commands
2. Execute operations (`get`, `set`, `ramp`, `act`)
3. `act` vs action metadata explanation
4. Trajectory command block (align wording with current behavior)
5. Output/help notes

## Content decisions

- Promote `set --arg key=value` and `act --arg key=value` as canonical.
- Keep `set <parameter> <value>` as scalar shorthand only.
- Explain structured set defaulting:
  - explicit args win,
  - missing required args are filled from one `get` snapshot by normalized field name,
  - unmatched get-only fields are ignored,
  - optional unresolved args can fall back to defaults.
- Clarify that field mapping is by name, not positional index.

## Non-goals

- No CLI behavior changes in this task.
- No schema changes.

## Validation

- Run docs tests:
  - `python -m pytest tests/test_docs_parameters_manifest.py tests/test_release_docs.py -q`
- Run CLI help smoke checks:
  - `python -m nanonis_qcodes_controller.cli --help`
  - `python -m nanonis_qcodes_controller.cli set --help`
  - `python -m nanonis_qcodes_controller.cli act --help`
