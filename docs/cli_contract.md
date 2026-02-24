# CLI Contract (`nqctl`)

## Goal
Expose a small, stable command surface that orchestration agents can call directly.

## Core commands
- `nqctl capabilities`: returns parameters/actions/policy summary, including rich
  `parameters.items[*]` metadata (`get_cmd`, `set_cmd`, `vals`, `safety`, non-empty descriptions)
  for agent planning.
- `nqctl observables list`: returns readable and writable parameter metadata.
- `nqctl actions list`: returns supported action descriptors.
- `nqctl get <parameter>`: reads one parameter value.
- `nqctl set <parameter> <value>`: guarded strict single-step write.
- `nqctl ramp <parameter> <start> <end> <step> --interval-s <sec>`: guarded explicit ramp.
- `nqctl policy show`: returns effective write policy and enablement guidance.

## Parameter-file commands
- `nqctl parameters discover --match LockIn`
- `nqctl parameters validate --file config/parameters.yaml`

## Trajectory commands
Legacy JSONL readers are still available:
- `nqctl trajectory tail --directory artifacts/trajectory --limit 20`
- `nqctl trajectory follow --directory artifacts/trajectory --interval-s 0.5`

SQLite action query commands:
- `nqctl trajectory action list --db-path artifacts/trajectory/trajectory-monitor.sqlite3 [--run-name <name>]`
- `nqctl trajectory action show --db-path artifacts/trajectory/trajectory-monitor.sqlite3 --action-idx <idx> [--run-name <name>]`
- Add `--with-signal-window` to `action show` to include sampled signal rows in the stored action window.

SQLite monitor workflow:
- `nqctl trajectory monitor config show`
- `nqctl trajectory monitor config set --run-name <name> [--signals <csv>] [--specs <csv>] [--interval-s <sec>] [--rotate-entries <n>] [--action-window-s <sec>] [--directory <dir>] [--db-name <file>]`
- `nqctl trajectory monitor config clear`
- `nqctl trajectory monitor list-signals`
- `nqctl trajectory monitor list-specs`
- `nqctl trajectory monitor run [--iterations <n>]`

Monitor config requirements and defaults:
- `run_name` must be set before `trajectory monitor run`.
- `run_name` is cleared from staged config after each monitor run attempt.
- Default action window is `2.5` seconds.
- Default dense rotation is `6000` samples per segment.

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
- Use `scripts/generate_parameters_manifest.py` to refresh `config/parameters.yaml` from `nanonis_spm.Nanonis`.
- `set` never auto-ramps; use `ramp` for stepped trajectories.
- Keep sequencing logic in orchestration layer; `nqctl` exposes atomic operations.
- For monitor runs, stage `run_name` first via `trajectory monitor config set`.

## Help usage
- `nqctl -help`: top-level help
- `nqctl -help observables`: command-group help
- `nqctl -help parameters`: parameter-file workflow help
