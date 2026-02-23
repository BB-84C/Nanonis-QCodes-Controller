# Test Runbook

## Purpose
This runbook defines how to run fast local tests and simulator integration tests separately.

## Fast local suite (no simulator)
Use this for normal development iteration.

```powershell
python -m pytest -q -m "not simulator"
python -m ruff check .
python -m black --check .
python -m mypy nanonis_qcodes_controller
```

## Simulator integration suite (read path)
Use this on a machine with STM Simulator running.

```powershell
$env:NANONIS_RUN_SIMULATOR_TESTS = "1"
python -m pytest -q -m simulator -k "not simulator_writes"
```

## Simulator guarded write integration
This performs bounded/ramped write operations and restores initial values.

```powershell
$env:NANONIS_RUN_SIMULATOR_TESTS = "1"
$env:NANONIS_RUN_SIMULATOR_WRITE_TESTS = "1"
python -m pytest -q -m simulator_writes
```

## Notes
- Keep STM Simulator open and idle before starting simulator tests.
- Simulator tests skip automatically unless `NANONIS_RUN_SIMULATOR_TESTS=1` is set.
- Guarded write tests skip automatically unless `NANONIS_RUN_SIMULATOR_WRITE_TESTS=1` is set.
- If a write test fails, verify instrument state manually and rerun with conservative limits.

## Trajectory monitor smoke (SQLite)
Use this to validate monitor staging + run + action queries:

```powershell
nqctl trajectory monitor config clear
nqctl trajectory monitor config set --run-name smoke-run-001
nqctl trajectory monitor run --iterations 25
nqctl trajectory action list --db-path artifacts/trajectory/trajectory-monitor.sqlite3 --run-name smoke-run-001
# Run show only when action list count > 0.
nqctl trajectory action show --db-path artifacts/trajectory/trajectory-monitor.sqlite3 --run-name smoke-run-001 --action-idx 0 --with-signal-window
```

Expected:
- `trajectory monitor run` fails fast if `run_name` is empty.
- `run_name` is cleared after run completion/interruption.
- Action rows expose `detected_at_utc` (ISO UTC), `dt_s`, and configured action window (`2.5` seconds default).
- Signal/spec dense catalogs rotate every `6000` entries unless overridden.
