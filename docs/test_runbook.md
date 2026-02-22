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
set NANONIS_RUN_SIMULATOR_TESTS=1
python -m pytest -q -m simulator -k "not simulator_writes"
```

## Simulator guarded write integration
This performs bounded/ramped write operations and restores initial values.

```powershell
set NANONIS_RUN_SIMULATOR_TESTS=1
set NANONIS_RUN_SIMULATOR_WRITE_TESTS=1
python -m pytest -q -m simulator_writes
```

## Notes
- Keep STM Simulator open and idle before starting simulator tests.
- Simulator tests skip automatically unless `NANONIS_RUN_SIMULATOR_TESTS=1` is set.
- Guarded write tests skip automatically unless `NANONIS_RUN_SIMULATOR_WRITE_TESTS=1` is set.
- If a write test fails, verify instrument state manually and rerun with conservative limits.

## Phase 8 trajectory smoke
Generate operations and trajectory events without manual GUI interactions:

```powershell
python scripts/bias_dependent_topo_query.py --start-bias-mv 50 --stop-bias-mv 50 --step-bias-mv 25 --start-current-pa 100 --trajectory-enable --trajectory-dir artifacts/trajectory_phase8
python scripts/trajectory_reader.py --directory artifacts/trajectory_phase8 --limit 20
```
