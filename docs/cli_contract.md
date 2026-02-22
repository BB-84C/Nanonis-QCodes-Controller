# CLI Contract (`nqctl`)

## Goal
Expose a small, stable command surface that orchestration agents can call directly.

## Core commands
- `nqctl capabilities --json`: returns observables/actions/policy summary.
- `nqctl observables list --json`: returns readable channels.
- `nqctl actions list --json`: returns supported action descriptors.
- `nqctl get <observable> --json`: reads one observable.
- `nqctl set <channel> <value> --confirmed --json`: guarded strict single-step write (`bias_v`, `setpoint_a`).
- `nqctl ramp <channel> <start> <end> <step> --interval-s <sec> --confirmed --json`: guarded explicit multi-step ramp.
- `nqctl policy show --json`: returns effective write policy and enablement guidance.

## Extension-file commands
- `nqctl extensions discover --match LockIn --json`
- `nqctl extensions scaffold --match LockIn --output config/lockin_parameters.yaml --json`
- `nqctl extensions validate --file config/lockin_parameters.yaml --json`

Backward-compatible alias: `manifest` (for example `nqctl manifest discover ...`).

## Trajectory commands
- `nqctl trajectory tail --directory artifacts/trajectory --limit 20 --json`
- `nqctl trajectory follow --directory artifacts/trajectory --interval-s 0.5 --json`

Runtime toggle for non-blocking trajectory logging on `get`, `set`, and `ramp`:
- `--trajectory-enable`
- `--trajectory-dir`
- `--trajectory-queue-size`
- `--trajectory-max-events-per-file`

## Backend passthrough
- `nqctl backend commands --json`
- `nqctl backend call <Command> --args-json '{...}' --unsafe-raw-call --json`

`backend call` is intentionally gated for explicit unsafe acknowledgement.

## Exit codes
- `0`: success
- `1`: generic failure
- `2`: policy blocked (safety)
- `3`: invalid input / extension-file / protocol-shape issue
- `4`: unavailable command/backend capability
- `5`: connection/timeout failure

## Notes for orchestration agents
- Prefer `--json` for all calls.
- Use `capabilities` once at task start to learn action/observable surface.
- Use `extensions` commands to extend observables without code changes.
- `set` never auto-ramps; use `ramp` when move requires intermediate points.
- Keep sequencing logic in orchestration layer; `nqctl` intentionally exposes atomic operations.

## Help usage
- `nqctl -help`: top-level help
- `nqctl -help observables`: command-group help
- `nqctl -help extensions`: extension-file workflow help
