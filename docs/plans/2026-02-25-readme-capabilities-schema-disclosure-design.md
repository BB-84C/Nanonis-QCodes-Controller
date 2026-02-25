# README Capabilities Schema Disclosure Design

## Goal
Disclose the machine-readable `nqctl capabilities` contract in `README.md` by documenting the JSON schema for:
- `parameters.items[*]`
- `action_commands.items[*]`

## Scope
- Update `README.md` only.
- Add a compact section with formal JSON Schema blocks.
- Cover current methods-only fields used by capabilities payloads.

## Non-Goals
- No legacy/deprecated field discussion.
- No manifest file schema expansion beyond capabilities output.
- No CLI behavior changes.

## Chosen Approach
Use a README-local schema disclosure section with two formal JSON Schema snippets:
1. `parameters.items[*]`
2. `action_commands.items[*]`

This keeps the contract visible at the main entry point and aligned with the user request for concise disclosure.

## Design Details
Add a new section under the CLI guide:
- Short intro sentence: these schemas describe the structures returned by `nqctl capabilities`.
- JSON Schema 2020-12 blocks with `type`, `required`, and nested field definitions.
- Include nested object definitions for:
  - `get_cmd`, `set_cmd`, `action_cmd`
  - `arg_fields`, `response_fields`
  - `safety`

Keep `additionalProperties` permissive at top-level command containers to avoid over-constraining minor additive metadata.

## Validation Plan
- Run `python -m pytest tests/test_docs_parameters_manifest.py tests/test_release_docs.py -q`.
- Confirm README remains coherent and examples still readable.

## Risks
- Schema drift if payload changes without README updates.

## Mitigation
- Keep schema blocks narrowly scoped to capabilities surfaces and future-proof command objects for additive metadata.
