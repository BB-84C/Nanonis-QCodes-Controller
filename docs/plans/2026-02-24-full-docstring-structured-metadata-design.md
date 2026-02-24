# Full Docstring and Structured Command Metadata Design

Date: 2026-02-24

## Goal

Upgrade generated `config/parameters.yaml` so every `get_cmd`, `set_cmd`, and
`action_cmd` contains:

- concise `description` (existing behavior),
- complete original command docstring (`docstring_full`),
- structured metadata extracted from docstrings.

For structured metadata:

- `get_cmd.response_fields: [{index, name, type, unit, description}]`
- `set_cmd.arg_fields: [{name, type, required, description}]`
- `action_cmd.arg_fields: [{name, type, required, description}]`

Scope is all discovered commands.

## Design

### Manifest schema additions

Additive only (backward compatible):

- `parameters.<name>.get_cmd.docstring_full`
- `parameters.<name>.get_cmd.response_fields`
- `parameters.<name>.set_cmd.docstring_full`
- `parameters.<name>.set_cmd.arg_fields`
- `actions.<name>.action_cmd.docstring_full`
- `actions.<name>.action_cmd.arg_fields`

No top-level parameter `description` is reintroduced.

### Parser strategy

In `manifest_generator.py`, parse each Nanonis docstring into:

1. summary/header lines,
2. `Arguments:` section,
3. `Return arguments` section.

Then:

- keep existing short `description` extraction,
- normalize and store full doc in `docstring_full`,
- parse `-- ...` list entries into structured rows.

Field extraction rules:

- `index`: zero-based order in return list,
- `name`: left-hand phrase before explanatory clause,
- `type`: normalized scalar or textual type token from doc (e.g., `int`,
  `float`, `str`, `bool`, array forms as textual type labels),
- `unit`: parsed unit token when present,
- `description`: full line text for context.

### Loader and capabilities propagation

Update `extensions.py` command dataclasses/parsers to carry new optional fields.
Update CLI capabilities collectors to include these fields when present.

### Merge behavior

Generated fields are present for all discovered commands. Curated overlays can
override or extend, but missing new fields are auto-filled from generated data.

## Validation

- Unit tests for doc parsing and manifest entry emission in
  `tests/test_parameter_manifest_generator.py`.
- Parser tests in `tests/test_qcodes_extensions.py`.
- Capabilities payload tests in `tests/test_cli.py`.
- Full verification: `ruff`, `black --check`, `mypy`, `pytest`.
