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
- Optional: tune write limits in `config/default.yaml`.

## 3) Connectivity checks

```powershell
python scripts/bridge_doctor.py --json
python scripts/probe_nanonis.py --backend nanonis_spm --command-probe
```

Expected: one or more candidate ports and at least one recommended port.

## 4) Read-path smoke

```powershell
python scripts/read_client_demo.py --iterations 5 --interval-s 0.2
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
- Default config blocks writes (`allow_writes=false`).
- Use the demo script to verify policy behavior.

```powershell
python scripts/guarded_write_demo.py --channel bias_v --target 1.8
```

## 7) Automated operation demo (no manual GUI actions)
Run one-step bias-dependent topo query with trajectory enabled:

```powershell
python scripts/bias_dependent_topo_query.py --start-bias-mv 50 --stop-bias-mv 50 --step-bias-mv 25 --start-current-pa 100 --trajectory-enable --trajectory-dir artifacts/trajectory_quickstart
python scripts/trajectory_reader.py --directory artifacts/trajectory_quickstart --limit 20
```

## 8) Test matrix

```powershell
python -m pytest -q -m "not simulator"
NANONIS_RUN_SIMULATOR_TESTS=1 python -m pytest -q -m "simulator and not simulator_writes"
NANONIS_RUN_SIMULATOR_TESTS=1 NANONIS_RUN_SIMULATOR_WRITE_TESTS=1 python -m pytest -q -m simulator_writes
```

## References
- Architecture: `docs/architecture.md`
- Safety model: `docs/safety_model.md`
- Trajectory model: `docs/trajectory_model.md`
- Test runbook: `docs/test_runbook.md`
- Example notebook: `docs/notebooks/simulator_demo.ipynb`
