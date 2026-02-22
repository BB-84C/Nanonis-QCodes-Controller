# Nanonis-QCodes-Controller

Simulator-first Python bridge between Nanonis SPM controller interfaces and QCodes.

## Current stage

Stages 1-9 foundation and handoff docs are now in place:
- project packaging and quality tooling
- environment and YAML configuration scaffolding
- transport client with backend adapter and retry/lock policy
- connectivity probe script with candidate ranking
- QCodes read-only instrument parameters wired to transport client
- safety policy layer for guarded writes (bounds/ramp/cooldown/dry-run)
- first guarded writes enabled (bias, setpoint, optional scan frame)
- non-blocking trajectory journal + reader utilities

Phase 7 test matrix is now available with simulator-marked integration tests.

## Design goals

- Avoid machine-specific assumptions. No hardcoded install path is required.
- Keep host/ports/write policy configurable for any lab machine.
- Ship read-only monitoring first, then enable guarded writes.
- Keep architecture ready for backend and MCP expansion.

## Quickstart (development)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .[dev]

pytest -q
ruff check .
black --check .
mypy nanonis_qcodes_controller
```

Fast local tests (excluding simulator-marked integration):

```powershell
python -m pytest -q -m "not simulator"
```

For QCodes driver development, install the optional extra:

```powershell
python -m pip install -e .[dev,qcodes]
```

For the optional backend command probe:

```powershell
python -m pip install -e .[dev,nanonis]
```

## Configuration

1) Copy `.env.example` to `.env` (optional, environment overrides).
2) Edit values as needed for your machine.
3) Defaults are in `config/default.yaml`.

The project uses runtime configuration for host, candidate ports, timeout, and write policy.

## Probe script

Run the connectivity probe against configured host/ports:

```powershell
python scripts/probe_nanonis.py
```

Override at runtime when needed:

```powershell
python scripts/probe_nanonis.py --host 127.0.0.1 --ports 3364,6501-6504 --attempts 5
```

Optional backend-level minimal command probe (read-only) using `nanonis_spm`:

```powershell
python -m pip install nanonis-spm
python scripts/probe_nanonis.py --backend nanonis_spm --command-probe
```

When command probe succeeds, candidate ports are restricted to backend-validated ports.

JSON output for automation:

```powershell
python scripts/probe_nanonis.py --json
```

## Transport client demo

Run repeated read commands through the Phase 3 transport layer:

```powershell
python scripts/read_client_demo.py --iterations 5 --interval-s 0.2
```

Example with explicit commands:

```powershell
python scripts/read_client_demo.py --commands Bias_Get,Current_Get,ZCtrl_ZPosGet,Scan_StatusGet
```

## QCodes driver demo

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

### Guarded write demo (Phase 5)

Writes are policy-gated. With default config (`allow_writes=false`), guarded write methods raise a policy error.

CLI preview/apply example:

```powershell
python scripts/guarded_write_demo.py --channel bias_v --target 1.8
```

```python
from nanonis_qcodes_controller.qcodes_driver import QcodesNanonisSTM

nanonis = QcodesNanonisSTM("nanonis", auto_connect=True)

# Dry-run or blocked depending on policy settings
plan = nanonis.plan_bias_v_set(1.8)
report = nanonis.set_bias_v_guarded(1.8)

print(plan)
print(report)

nanonis.close()
```

### First enabled guarded writes (Phase 6)

- `set_bias_v_guarded(...)`: bounded + ramped bias set.
- `set_zctrl_setpoint_a_guarded(...)`: bounded + ramped setpoint set.
- `set_scan_frame_guarded(...)`: optional bounded/ramped frame update.

Guarded write attempts are available through `nanonis.guarded_write_audit_log()`.

Bias-dependent topo query example:

```powershell
python scripts/bias_dependent_topo_query.py --start-bias-mv 50 --stop-bias-mv 150 --step-bias-mv 25 --start-current-pa 100
```

Bias-dependent topo query with trajectory journal enabled:

```powershell
python scripts/bias_dependent_topo_query.py --start-bias-mv 50 --stop-bias-mv 150 --step-bias-mv 25 --start-current-pa 100 --trajectory-enable
```

Read trajectory tail:

```powershell
python scripts/trajectory_reader.py --directory artifacts/trajectory --limit 20
```

Follow trajectory stream:

```powershell
python scripts/trajectory_reader.py --directory artifacts/trajectory --follow --interval-s 0.5
```

Bridge doctor (port/protocol/trajectory checks):

```powershell
python scripts/bridge_doctor.py --command-probe
```

## Phase 7 test matrix

- `tests/test_simulator_integration.py` covers connect/disconnect cycles, read loops, and guarded write scenarios.
- Simulator tests are gated by env var to avoid accidental runs on non-simulator setups.

Run simulator integration (read path):

```powershell
set NANONIS_RUN_SIMULATOR_TESTS=1
python -m pytest -q -m simulator -k "not simulator_writes"
```

Run simulator guarded write integration:

```powershell
set NANONIS_RUN_SIMULATOR_TESTS=1
set NANONIS_RUN_SIMULATOR_WRITE_TESTS=1
python -m pytest -q -m simulator_writes
```

Detailed test runbook: `docs/test_runbook.md`

Trajectory model: `docs/trajectory_model.md`

## Project plan

Detailed phased plan: `PLAN.md`

## Documentation index

- Simulator quickstart: `docs/quickstart_simulator.md`
- Safety model: `docs/safety_model.md`
- Porting to real controller: `docs/porting_to_real_controller.md`
- Architecture overview: `docs/architecture.md`
- Trajectory model: `docs/trajectory_model.md`
- Test runbook: `docs/test_runbook.md`
- Example notebook: `docs/notebooks/simulator_demo.ipynb`
