# Quickstart (STM Simulator)

## Goal
Get a clean machine from zero to a working simulator demo in under one hour.

## Prerequisites
- Windows machine with Nanonis STM Simulator installed.
- Python 3.10+ available in PATH.
- STM Simulator running before probe/integration commands.

## 1) Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .[dev,qcodes,nanonis]
```

## 2) Configure
- Copy `.env.example` to `.env`.
- Keep defaults for simulator-first testing unless your host/ports differ.
- Optional: tune runtime policy in `config/default_runtime.yaml` and parameter safety in `config/default_parameters.yaml`.

## 3) Connectivity checks

```powershell
python scripts/bridge_doctor.py --json
python tests/probe_nanonis.py --backend nanonis_spm --command-probe
```

Expected: one or more candidate ports and at least one recommended port.

## 4) Read-path smoke

```powershell
python tests/read_client_demo.py --iterations 5 --interval-s 0.2
```

## 5) QCodes smoke

```python
from qcodes.station import Station
from nanonis_qcodes_controller.qcodes_driver import QcodesNanonisSTM

station = Station()
nanonis = QcodesNanonisSTM("nanonis", auto_connect=True)
station.add_component(nanonis)

print(nanonis.bias_v())
print(nanonis.current_a())
print(nanonis.zctrl_setpoint_a())
print(nanonis.snapshot(update=True))

nanonis.close()
```

## 6) Guarded-write smoke
- Runtime policy is controlled in `config/default_runtime.yaml`.
- Use the demo script to verify single-step guarded writes.

```powershell
python tests/guarded_write_demo.py --channel bias_v --target 1.8
```

## 7) Trajectory monitor quick workflow
Stage monitor config, inspect available labels, run monitor, then query actions:

```powershell
nqctl trajectory monitor config clear
nqctl trajectory monitor list-signals
nqctl trajectory monitor list-specs
nqctl trajectory monitor config set --run-name sim-demo-001
nqctl trajectory monitor run --iterations 50
nqctl trajectory action list --db-path artifacts/trajectory/trajectory-monitor.sqlite3 --run-name sim-demo-001
# Run show only when action list count > 0.
nqctl trajectory action show --db-path artifacts/trajectory/trajectory-monitor.sqlite3 --run-name sim-demo-001 --action-idx 0 --with-signal-window
```

Notes:
- `run_name` is required before `trajectory monitor run`.
- `run_name` is automatically cleared from staged config when the run exits.
- Action timestamps are ISO UTC, and action signal window default is `2.5` seconds.
- Dense signal/spec rotation default is `6000` entries per segment.

## 8) Test matrix

```powershell
python -m pytest -q -m "not simulator"
$env:NANONIS_RUN_SIMULATOR_TESTS = "1"
python -m pytest -q -m "simulator and not simulator_writes"
$env:NANONIS_RUN_SIMULATOR_WRITE_TESTS = "1"
python -m pytest -q -m simulator_writes
```

## References
- Architecture: `docs/architecture.md`
- Safety model: `docs/safety_model.md`
- Trajectory model: `docs/trajectory_model.md`
- Test runbook: `docs/test_runbook.md`
- Example notebook: `docs/notebooks/simulator_demo.ipynb`
