# Quickstart (STM Simulator)

## Goal
Get a clean machine from zero to a working simulator demo in under one hour.

## Prerequisites
- Windows machine with Nanonis STM Simulator installed.
- Python 3.10+ available in PATH.
- STM Simulator running before probe/integration commands.

## 1) Install

End users (run the CLI against your simulator):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install nspmctl
```

Contributors (run the test suite):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -e ".[dev]"
```

## 2) Configure
- Defaults work out of the box against the simulator on `127.0.0.1`
  (ports `3364, 6501-6504`, `allow_writes=true`, `dry_run=false`).
- Optional: copy `.env.example` to `.env` to override host/ports/policy
  via environment variables.
- Optional: tune `config/default_runtime.yaml` (repo checkout) or pass
  `--config-file path/to/runtime.yaml` (installed wheel) for a custom
  policy / per-channel limits.
- The parameter manifest is shipped inside the package at
  `nspmctl/resources/config/parameters.yaml`; override with
  `--parameters-file your-manifest.yaml`.

## 3) Connectivity checks

```powershell
nspmctl doctor --command-probe
```

Expected: one or more candidate ports and at least one recommended port.

## 4) CLI smoke

```powershell
nspmctl capabilities
nspmctl get bias_v
nspmctl get current_a
```

The first `nspmctl` call against the simulator auto-spawns a warm
daemon in the background; subsequent calls converge to ~100 ms p50.

## 5) Embedded Python API smoke

```python
from nspmctl.controller import NanonisController

nanonis = NanonisController("nanonis_demo", auto_connect=True)
try:
    print(nanonis.get_parameter_value("bias_v"))
    print(nanonis.get_parameter_value("current_a"))
    print(nanonis.get_parameter_value("zctrl_setpoint_a"))
finally:
    nanonis.close()
```

Note: the embedded API bypasses the daemon and pays the full import +
connect cost on construction. Prefer the CLI for hot loops; reach for
the Python API when composing with custom Python tooling.

## 6) Guarded-write smoke
- Default runtime policy is live (`allow_writes=true`, `dry_run=false`)
  but every `set` / `ramp` / `act` is gated by per-channel safety limits.
- Use the demo script to verify single-step guarded writes:

```powershell
python tests/guarded_write_demo.py --channel bias_v --target 1.8
```

## 7) Test matrix

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
- Test runbook: `docs/test_runbook.md`
- CLI contract: `docs/cli_contract.md`
