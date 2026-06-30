# CLI Contract (`nspmctl`)

## Goal
Expose a small, stable command surface that orchestration agents can call directly.

## Core commands
- `nspmctl capabilities`: returns parameters/action-commands/actions/policy summary,
  including rich `parameters.items[*]` metadata (`get_cmd`, `set_cmd`,
  `safety`) and `action_commands.items[*]` metadata (`action_cmd`, `safety_mode`).
  Descriptions are exposed on command blocks when present.
- `nspmctl observables list`: returns readable and writable parameter metadata.
- `nspmctl actions list`: returns supported action descriptors.
- `nspmctl act <action_name> --arg <key=value>`: invoke one manifest action command.
- `nspmctl get <parameter>`: reads one parameter value.
- `nspmctl set <parameter> --arg <key=value>`: guarded strict write using structured argument fields.
- `nspmctl ramp <parameter> <start> <end> <step> --interval-s <sec>`: guarded explicit ramp.
- `nspmctl policy show`: returns effective write policy and enablement guidance.

## Parameter-file commands
- `nspmctl parameters discover --match LockIn`
- `nspmctl parameters validate --file config/parameters.yaml`

## Backend discovery
- `nspmctl backend commands`

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
- Non-`Get`/`Set` backend methods are exposed via `actions` section in `parameters.yaml`.
- Action safety modes are: `alwaysAllowed`, `guarded`, `blocked`.
- Use `scripts/generate_parameters_manifest.py` to refresh `config/parameters.yaml` from `nanonis_spm.Nanonis`.
- `set` never auto-ramps; use `ramp` for stepped trajectories.
- Keep sequencing logic in orchestration layer; `nspmctl` exposes atomic operations.

## Help usage
- `nspmctl -help`: top-level help
- `nspmctl -help observables`: command-group help
- `nspmctl -help parameters`: parameter-file workflow help
