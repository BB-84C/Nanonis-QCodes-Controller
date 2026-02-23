# Nanonis-QCodes-Controller

Simulator-first Python bridge between Nanonis SPM controller interfaces and QCodes.

## What this project provides

- `nqctl`: an agent-friendly CLI for atomic read/write/ramp operations.
- `QcodesNanonisSTM`: a QCodes instrument wrapper with spec-driven parameters.
- Strict write semantics:
  - `set` is always a guarded single-step write.
  - `ramp` is always an explicit multi-step trajectory.
- Safety-first defaults (`allow_writes=false`, `dry_run=true`).

## Install

Install from source:

```powershell
python -m pip install .
```

Optional extras:

```powershell
python -m pip install ".[qcodes]"
python -m pip install ".[nanonis]"
```

## Configure

1. Optionally copy `.env.example` to `.env`.
2. Set runtime values in `config/default_runtime.yaml`.
3. Unified parameter specs are in `config/parameters.yaml`.
4. Regenerate from `nanonis_spm.Nanonis` with `scripts/generate_parameters_manifest.py`.
5. Trajectory monitor defaults are in `config/default_trajectory_monitor.yaml`.

Runtime config controls host, candidate ports, timeout, backend, write policy, and trajectory settings.

## CLI quickstart (`nqctl`)

Show capability contract:

```powershell
nqctl capabilities
```

Read one parameter:

```powershell
nqctl get bias_v
```

Guarded single-step set:

```powershell
nqctl set bias_v 0.12
```

Explicit guarded ramp:

```powershell
nqctl ramp bias_v 0.10 0.25 0.01 --interval-s 0.10
```

Inspect effective policy:

```powershell
nqctl policy show
```

## Trajectory monitor quickstart (`nqctl`)

Inspect monitor defaults and available labels:

```powershell
nqctl trajectory monitor config show
nqctl trajectory monitor list-signals
nqctl trajectory monitor list-specs
```

Stage a run configuration (required before each run):

```powershell
nqctl trajectory monitor config set --run-name gui-play-001 --interval-s 0.1 --rotate-entries 6000 --action-window-s 2.5
```

Start monitoring (foreground; stop with `Ctrl+C`):

```powershell
nqctl trajectory monitor run
```

Inspect action trajectory from SQLite:

```powershell
nqctl trajectory action list --db-path artifacts/trajectory/trajectory-monitor.sqlite3 --run-name gui-play-001
nqctl trajectory action show --db-path artifacts/trajectory/trajectory-monitor.sqlite3 --run-name gui-play-001 --action-idx 0 --with-signal-window
```

Notes:
- `run_name` is cleared after each monitor run, so set it again before the next run.
- Action entries use ISO UTC timestamps and include `delta_value` for numeric spec changes.
- Legacy JSONL readers remain available via `nqctl trajectory tail` and `nqctl trajectory follow`.

JSON is the default output format. Use `--text` for human-readable key/value output.

Help shortcuts:

```powershell
nqctl -help
nqctl -help parameters
```

## Parameter extension workflow

Discover candidate backend commands:

```powershell
nqctl parameters discover --match LockIn
```

Regenerate the unified parameter manifest:

```powershell
python scripts/generate_parameters_manifest.py --output config/parameters.yaml
```

Validate parameter YAML:

```powershell
nqctl parameters validate --file config/parameters.yaml
```

## QCodes usage

```python
from qcodes.station import Station
from nanonis_qcodes_controller.qcodes_driver import QcodesNanonisSTM

station = Station()
nanonis = QcodesNanonisSTM("nanonis", auto_connect=True)
station.add_component(nanonis)

print(nanonis.bias_v())
print(nanonis.current_a())

nanonis.close()
```

## Documentation index

- CLI contract: `docs/cli_contract.md`
- Extension workflow: `docs/extension_workflow.md`
- Safety model: `docs/safety_model.md`
- Architecture overview: `docs/architecture.md`
- Simulator quickstart: `docs/quickstart_simulator.md`
- Trajectory model: `docs/trajectory_model.md`
- Porting to real controller: `docs/porting_to_real_controller.md`

Project planning and internal development workflow details: `PLAN.md`
