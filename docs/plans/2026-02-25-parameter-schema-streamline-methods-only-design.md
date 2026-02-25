# Parameter Schema Streamline (Methods-Only) Design

Date: 2026-02-25

## Goal

Streamline parameter schema and runtime contract to structured methods-only IO.

- Remove top-level parameter keys: `unit`, `value_type`, `vals`, `snapshot_value`.
- Stop exposing scalar QCodes parameters as the primary interface.
- Route runtime behavior through `arg_fields` / `response_fields` for all commands.

## Decision

Adopt Option 1 (hard migration):

- Methods-only runtime contract, even for single-argument commands.
- Accept API breaks now (pre-production).

## Current coupling to remove

- `snapshot_value` is currently only passed into QCodes `add_parameter` registration.
- `value_type` and `vals` currently drive scalar coercion and QCodes validators.
- `unit` currently appears in scalar-oriented parameter output.

With methods-only contract, these top-level scalar parameter descriptors become redundant.

## Target contract

### Schema (`parameters` entries)

Keep:

- `label`
- `get_cmd` (`command`, `description`, `arg_fields`, `response_fields`)
- `set_cmd` (`command`, `description`, `arg_fields`)
- `safety`

Remove:

- `unit`
- `value_type`
- `vals`
- `snapshot_value`

### Runtime API

Primary methods:

- `get_parameter_snapshot(name)`
- `set_parameter_fields(name, args=..., plan_only=...)`
- `execute_action(name, args=..., plan_only=...)`

No scalar-first behavior assumptions.

### CLI payload

`nqctl capabilities` parameter items should expose only structured command metadata and safety.
No scalar schema keys listed above.

## Migration rules

- Generator stops emitting removed top-level keys.
- Loader accepts legacy files containing removed keys (ignore) for transition.
- CLI and driver logic must avoid reading removed keys.

## Risks

- Breaking downstream code using `instrument.<param>()` scalar API.
- Test churn due to broad contract updates.

Accepted per project stage (development/testing).

## Validation

- Update tests to assert methods-only structured behavior.
- Verify full suite after schema and runtime changes.
