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
3. Built-in parameter specs are in `config/default_parameters.yaml`.
4. Optional lab-specific parameter specs can be added in `config/extra_parameters.yaml`.

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

Scaffold an extra parameter file:

```powershell
nqctl parameters scaffold --match LockIn --output config/extra_parameters.yaml
```

Validate parameter YAML:

```powershell
nqctl parameters validate --file config/extra_parameters.yaml
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
