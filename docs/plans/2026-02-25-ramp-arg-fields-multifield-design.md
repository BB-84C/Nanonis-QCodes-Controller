# Multi-Field Ramp Arg-Fields Design

## Goal
Extend `nqctl ramp` from scalar positional inputs to structured multi-field ramping aligned with `arg_fields` usage.

## Approved CLI Contract

```powershell
nqctl ramp scan_frame --arg Width=10e-9:20e-9:1e-9 --arg Height=8e-9:16e-9:1e-9:0.14 --interval-s 0.1
```

## Design Summary

`ramp` accepts repeatable `--arg key=value` where each value may be:
- **Ramp tuple:** `start:end:step` or `start:end:step:interval`
- **Fixed value:** `value` (held constant at each ramp step)

Global `--interval-s` remains required and acts as default interval for tuple values without an explicit per-field interval.

## Data Model

Each parsed `--arg` entry becomes one of:
- `RampFieldSpec(name, start, end, step, interval_s)`
- `FixedFieldSpec(name, value)`

Execution builds a merged step schedule over all ramp fields and applies each step using structured `set` with all required fields:
- ramped fields -> current step value
- fixed fields -> fixed value
- unspecified set fields -> autofilled from latest `get` snapshot (existing behavior in `set_parameter_fields`)

## Timeline and Step Semantics

- Every ramp field yields its own value sequence from `start/end/step`.
- At each execution tick, all active fields update.
- If one field reaches `end` earlier, it remains at end value while others continue.
- Per-field interval (4th tuple value) overrides global `--interval-s` for that field cadence.
- Tick scheduler advances by the nearest pending field interval and updates any field due at that time.

## Validation Rules

- At least one `--arg` must be a ramp tuple.
- Ramp tuple must contain exactly 3 or 4 numeric parts.
- `step` must be positive.
- `interval` (if present) must be positive.
- `--interval-s` must be non-negative and required.
- Unknown set arg names are rejected.
- If parameter is non-writable or ramp-disabled by safety, reject.
- If missing required set fields cannot be autofilled (no matching snapshot field), reject with explicit missing field list.

## Backward Compatibility

- Existing positional ramp form (`<start> <end> <step>`) will be deprecated in CLI help and can be retained for one transition release or removed immediately (project accepts breaking changes).
- Runtime write semantics remain policy-guarded.

## Output Contract

Ramp result payload remains structured and gains multi-field metadata:
- `ramp_fields`: parsed per-field ramp definitions
- `fixed_fields`: explicit fixed args
- `timeline`: optional condensed schedule summary (tick count, duration)
- existing `plan`, `report`, `applied`, `timestamp_utc`

## Test Strategy

- CLI parsing tests for 3-part and 4-part tuples.
- Mixed fixed+ramp args tests.
- Per-field interval override tests.
- Autofill preservation tests for unspecified fields (scan-buffer-style regression coverage).
- Error tests for malformed tuples and missing required fields.
