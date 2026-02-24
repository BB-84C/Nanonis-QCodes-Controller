# Nanonis-QCodes-Controller

Simulator-first Python bridge between Nanonis SPM controller interfaces and QCodes.

## What this project provides

- `nqctl`: an agent-friendly CLI for atomic read/write/ramp operations.
- `QcodesNanonisSTM`: a QCodes instrument wrapper with spec-driven parameters.
- Strict write semantics:
  - `set` is always a guarded single-step write.
  - `ramp` is always an explicit multi-step trajectory.
- Safety-first defaults (`allow_writes=false`, `dry_run=true`).

## v1 API support contract

- Stable Python API symbols: `QcodesNanonisSTM`, `create_client`, `load_settings`.
- Stable CLI contract: documented `nqctl` commands and outputs.
- Other Python symbols are provisional/internal and may change across minor releases.

## Install

Install from a GitHub release (recommended for test users):

1. Open the releases page and download the wheel asset (`*.whl`), not the auto-generated source zip/tarball.
2. Create a virtual environment.
3. Install the wheel, then install optional runtime integrations.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install .\nanonis_qcodes_controller-<version>-py3-none-any.whl
python -m pip install "qcodes>=0.46.0" "nanonis-spm>=1.0.3"
nqctl capabilities
```

You can also install directly from a release URL:

```powershell
python -m pip install "https://github.com/BB-84C/Nanonis-QCodes-Controller/releases/download/v<version>/nanonis_qcodes_controller-<version>-py3-none-any.whl"
```

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
   - `parameters`: scalar `get`/`set` mappings.
   - `actions`: non-`Get`/`Set` backend methods with `action_cmd` metadata.
4. Regenerate from `nanonis_spm.Nanonis` with `scripts/generate_parameters_manifest.py`.
5. Trajectory monitor defaults are in `config/default_trajectory_monitor.yaml`.

Runtime config controls host, candidate ports, timeout, backend, write policy, and trajectory settings.

## CLI command guide (`nqctl`)

### Inspect and introspect

Get the machine-readable CLI contract (parameters, action commands, policy):

```powershell
nqctl capabilities
```

Include backend command discovery from the active backend:

```powershell
nqctl capabilities --include-backend-commands --backend-match Scan
```

List observable parameter metadata and high-level CLI action descriptors:

```powershell
nqctl observables list
nqctl actions list
```

Inspect active policy, backend command inventory, and connectivity preflight:

```powershell
nqctl policy show
nqctl backend commands --match Bias
nqctl doctor --command-probe
```

### Execute operations

Read one parameter:

```powershell
nqctl get bias_v
```

Apply guarded strict single-step write:

```powershell
nqctl set bias_v 0.12
```

Apply explicit guarded ramp:

```powershell
nqctl ramp bias_v 0.10 0.25 0.01 --interval-s 0.10
```

Invoke one manifest action command with argument overrides:

```powershell
nqctl act Scan_Action --arg Scan_action=0 --arg Scan_direction=1
```

### `act` vs action metadata

- `nqctl act <action_name> --arg key=value` executes one backend action command from
  the manifest `actions` section.
- `nqctl actions list` lists CLI-level action descriptors (what workflows the CLI
  supports, with safety hints and templates).
- `nqctl capabilities` exposes executable manifest action inventory under
  `action_commands.items[*]` (command name, args, arg types, safety mode).

### Trajectory commands

Legacy JSONL readers:

```powershell
nqctl trajectory tail --directory artifacts/trajectory --limit 20
nqctl trajectory follow --directory artifacts/trajectory --interval-s 0.5
```

SQLite action queries:

```powershell
nqctl trajectory action list --db-path artifacts/trajectory/trajectory-monitor.sqlite3 --run-name gui-play-001
nqctl trajectory action show --db-path artifacts/trajectory/trajectory-monitor.sqlite3 --run-name gui-play-001 --action-idx 0 --with-signal-window
```

Monitor config and run loop:

```powershell
nqctl trajectory monitor config show
nqctl trajectory monitor config set --run-name gui-play-001 --interval-s 0.1 --rotate-entries 6000 --action-window-s 2.5
nqctl trajectory monitor list-signals
nqctl trajectory monitor list-specs
nqctl trajectory monitor run
nqctl trajectory monitor config clear
```

Notes:
- `run_name` is cleared after each monitor run attempt; set it again before the next run.
- Action entries use ISO UTC timestamps and include `delta_value` for numeric spec changes.

### Output and help

JSON is the default output format. Use `--text` for human-readable key/value output.

```powershell
nqctl -help
nqctl -help trajectory
nqctl -help act
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
- Private-index release runbook: `docs/release_private_index.md`

Project planning and internal development workflow details: `PLAN.md`
