# CLI Contract (`nqctl`)

## Goal
Expose a small, stable command surface that orchestration agents can call directly.

## Core commands
- `nqctl capabilities`: returns parameters/actions/policy summary.
- `nqctl observables list`: returns readable and writable parameter metadata.
- `nqctl actions list`: returns supported action descriptors.
- `nqctl get <parameter>`: reads one parameter value.
- `nqctl set <parameter> <value>`: guarded strict single-step write.
- `nqctl ramp <parameter> <start> <end> <step> --interval-s <sec>`: guarded explicit ramp.
- `nqctl policy show`: returns effective write policy and enablement guidance.

## Parameter-file commands
- `nqctl parameters discover --match LockIn`
- `nqctl parameters scaffold --match LockIn --output config/extra_parameters.yaml`
- `nqctl parameters validate --file config/extra_parameters.yaml`

## Trajectory commands
- `nqctl trajectory tail --directory artifacts/trajectory --limit 20`
- `nqctl trajectory follow --directory artifacts/trajectory --interval-s 0.5`

Runtime toggle for non-blocking trajectory logging on `get`, `set`, and `ramp`:
- `--trajectory-enable`
- `--trajectory-dir`
- `--trajectory-queue-size`
- `--trajectory-max-events-per-file`

## Backend discovery
- `nqctl backend commands`

## Exit codes
- `0`: success
- `1`: generic failure
- `2`: policy blocked (safety)
- `3`: invalid input / parameter-file / protocol-shape issue
- `4`: unavailable command/backend capability
- `5`: connection/timeout failure

## Notes for orchestration agents
- JSON output is the default format; use `--text` when needed.
- Use `capabilities` once at task start to learn available parameters and actions.
- Use `parameters` commands to add observables without code edits.
- `set` never auto-ramps; use `ramp` for stepped trajectories.
- Keep sequencing logic in orchestration layer; `nqctl` exposes atomic operations.

## Help usage
- `nqctl -help`: top-level help
- `nqctl -help observables`: command-group help
- `nqctl -help parameters`: parameter-file workflow help
