# Trajectory Monitor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an inference-only trajectory monitor that polls signals/specs at high rate, writes all trajectories to SQLite, and records sparse action events with configurable signal context windows.

**Architecture:** Add a new SQLite-backed monitor pipeline separate from the existing JSONL journal internals. The monitor uses staged CLI config, enforces one-time `run_name` usage per run, computes per-sample `dt_s` from a single run start timestamp, and stores three logical trajectories (`signal_samples`, `spec_samples`, `action_events`) with catalogs for labels/vals metadata.

**Tech Stack:** Python 3.11+, sqlite3 stdlib, argparse CLI, existing QCodes instrument APIs, pytest/ruff/black/mypy.

---

### Task 1: Add monitor config model + staged persistence

**Files:**
- Create: `nanonis_qcodes_controller/trajectory/monitor_config.py`
- Create: `tests/test_trajectory_monitor_config.py`
- Modify: `nanonis_qcodes_controller/trajectory/__init__.py`

**Step 1: Write the failing test**

```python
def test_run_requires_non_empty_run_name(tmp_path: Path) -> None:
    config = MonitorConfig(run_name="", interval_s=0.1)
    with pytest.raises(ValueError):
        config.require_runnable()
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_trajectory_monitor_config.py::test_run_requires_non_empty_run_name`
Expected: FAIL (`MonitorConfig` or `require_runnable` missing)

**Step 3: Write minimal implementation**

```python
@dataclass(frozen=True)
class MonitorConfig:
    run_name: str
    interval_s: float = 0.1
    rotate_entries: int = 6000
    action_window_s: float = 2.5

    def require_runnable(self) -> None:
        if not self.run_name.strip():
            raise ValueError("run_name is required. Use trajectory monitor config set first.")
```

Also implement:
- `load_staged_monitor_config(path: Path) -> MonitorConfig`
- `save_staged_monitor_config(path: Path, config: MonitorConfig) -> None`
- `clear_staged_run_name(path: Path) -> None`

**Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_trajectory_monitor_config.py`
Expected: PASS

**Step 5: Commit**

```bash
git add nanonis_qcodes_controller/trajectory/monitor_config.py tests/test_trajectory_monitor_config.py nanonis_qcodes_controller/trajectory/__init__.py
git commit -m "feat: add staged trajectory monitor config model"
```

### Task 2: Add SQLite schema + repository methods

**Files:**
- Create: `nanonis_qcodes_controller/trajectory/sqlite_store.py`
- Create: `tests/test_trajectory_sqlite_store.py`

**Step 1: Write the failing test**

```python
def test_initialize_schema_creates_required_tables(tmp_path: Path) -> None:
    db = TrajectorySQLiteStore(tmp_path / "traj.sqlite3")
    db.initialize_schema()
    names = db.table_names()
    assert {"runs", "signal_samples", "spec_samples", "action_events"}.issubset(names)
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_trajectory_sqlite_store.py::test_initialize_schema_creates_required_tables`
Expected: FAIL (`TrajectorySQLiteStore` missing)

**Step 3: Write minimal implementation**

```python
class TrajectorySQLiteStore:
    def __init__(self, db_path: Path) -> None:
        self._conn = sqlite3.connect(db_path)

    def initialize_schema(self) -> None:
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()
```

Include schema for:
- `runs`
- `signal_catalog`
- `spec_catalog`
- `signal_samples`
- `spec_samples`
- `action_events`
- `monitor_errors`

**Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_trajectory_sqlite_store.py`
Expected: PASS

**Step 5: Commit**

```bash
git add nanonis_qcodes_controller/trajectory/sqlite_store.py tests/test_trajectory_sqlite_store.py
git commit -m "feat: add sqlite schema for trajectory monitor"
```

### Task 3: Implement dense sampling writer (signals/specs)

**Files:**
- Create: `nanonis_qcodes_controller/trajectory/monitor.py`
- Create: `tests/test_trajectory_monitor_sampling.py`
- Modify: `nanonis_qcodes_controller/trajectory/__init__.py`

**Step 1: Write the failing test**

```python
def test_sampling_writes_dt_and_segment_rotation(tmp_path: Path) -> None:
    runner = TrajectoryMonitorRunner(..., interval_s=0.1, rotate_entries=3)
    runner.run_iterations(7)
    rows = runner.store.fetch_signal_samples()
    assert rows[-1]["sample_idx"] == 6
    assert rows[-1]["segment_id"] == 2
    assert all(row["dt_s"] >= 0 for row in rows)
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_trajectory_monitor_sampling.py::test_sampling_writes_dt_and_segment_rotation`
Expected: FAIL (`TrajectoryMonitorRunner` missing)

**Step 3: Write minimal implementation**

```python
start_monotonic = time.monotonic()
sample_idx = 0
while running:
    dt_s = time.monotonic() - start_monotonic
    segment_id = sample_idx // rotate_entries
    store.insert_signal_sample(..., sample_idx=sample_idx, segment_id=segment_id, dt_s=dt_s)
    store.insert_spec_sample(...)
    sample_idx += 1
```

Use actual poll timing (do not quantize `dt_s` to interval multiples).

**Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_trajectory_monitor_sampling.py`
Expected: PASS

**Step 5: Commit**

```bash
git add nanonis_qcodes_controller/trajectory/monitor.py tests/test_trajectory_monitor_sampling.py nanonis_qcodes_controller/trajectory/__init__.py
git commit -m "feat: add dense signal/spec trajectory sampling runner"
```

### Task 4: Implement spec-change detection + sparse action events

**Files:**
- Modify: `nanonis_qcodes_controller/trajectory/monitor.py`
- Modify: `nanonis_qcodes_controller/trajectory/sqlite_store.py`
- Create: `tests/test_trajectory_monitor_actions.py`

**Step 1: Write the failing test**

```python
def test_action_event_emitted_on_spec_change_with_window_bounds(tmp_path: Path) -> None:
    runner = TrajectoryMonitorRunner(..., action_window_s=2.5)
    runner.run_iterations(5)  # fake source changes one spec once
    events = runner.store.fetch_action_events()
    assert len(events) == 1
    assert events[0]["signal_window_start_dt_s"] == pytest.approx(events[0]["dt_s"] - 2.5)
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_trajectory_monitor_actions.py::test_action_event_emitted_on_spec_change_with_window_bounds`
Expected: FAIL (no action event detection)

**Step 3: Write minimal implementation**

```python
if old_value != new_value:
    store.insert_action_event(
        run_id=run_id,
        detected_at_utc=now_utc_iso(),
        dt_s=dt_s,
        spec_label=label,
        old_value_json=json.dumps(old_value),
        new_value_json=json.dumps(new_value),
        signal_window_start_dt_s=dt_s - action_window_s,
        signal_window_end_dt_s=dt_s + action_window_s,
    )
```

No duplication of signal rows into action table.

**Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_trajectory_monitor_actions.py`
Expected: PASS

**Step 5: Commit**

```bash
git add nanonis_qcodes_controller/trajectory/monitor.py nanonis_qcodes_controller/trajectory/sqlite_store.py tests/test_trajectory_monitor_actions.py
git commit -m "feat: add sparse action trajectory with configurable context window"
```

### Task 5: Add CLI config/list/run commands with single-use run name

**Files:**
- Modify: `nanonis_qcodes_controller/cli.py`
- Create: `tests/test_cli_trajectory_monitor.py`

**Step 1: Write the failing test**

```python
def test_monitor_run_fails_when_staged_run_name_missing(monkeypatch, tmp_path: Path) -> None:
    code = cli.main(["trajectory", "monitor", "run", "--config-path", str(tmp_path / "monitor.json")])
    assert code == cli.EXIT_ERROR
```

Also add tests for:
- `config set` writes run name
- `run` clears staged run name after completion
- `list-signals` and `list-specs` output labels/vals metadata

**Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_cli_trajectory_monitor.py`
Expected: FAIL (commands not implemented)

**Step 3: Write minimal implementation**

Add subcommands:
- `nqctl trajectory monitor config show`
- `nqctl trajectory monitor config set ...`
- `nqctl trajectory monitor config clear`
- `nqctl trajectory monitor list-signals`
- `nqctl trajectory monitor list-specs`
- `nqctl trajectory monitor run`

Enforce:
- `run` requires staged non-empty `run_name`
- clear staged `run_name` at end of run

**Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_cli_trajectory_monitor.py`
Expected: PASS

**Step 5: Commit**

```bash
git add nanonis_qcodes_controller/cli.py tests/test_cli_trajectory_monitor.py
git commit -m "feat: add trajectory monitor config and run cli commands"
```

### Task 6: Add action query commands and signal-window expansion

**Files:**
- Modify: `nanonis_qcodes_controller/cli.py`
- Modify: `nanonis_qcodes_controller/trajectory/sqlite_store.py`
- Create: `tests/test_cli_trajectory_actions.py`

**Step 1: Write the failing test**

```python
def test_action_show_with_signal_window_returns_context(tmp_path: Path) -> None:
    payload = run_cli_json(
        "trajectory", "action", "show",
        "--db-path", str(tmp_path / "traj.sqlite3"),
        "--action-idx", "1",
        "--with-signal-window",
    )
    assert "signal_window" in payload
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_cli_trajectory_actions.py::test_action_show_with_signal_window_returns_context`
Expected: FAIL (command missing)

**Step 3: Write minimal implementation**

Add subcommands:
- `nqctl trajectory action list --db-path ... [--run-name ...]`
- `nqctl trajectory action show --db-path ... --action-idx <n> [--with-signal-window]`

Window resolution SQL:

```sql
SELECT * FROM signal_samples
WHERE run_id = ? AND dt_s BETWEEN ? AND ?
ORDER BY sample_idx ASC
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_cli_trajectory_actions.py`
Expected: PASS

**Step 5: Commit**

```bash
git add nanonis_qcodes_controller/cli.py nanonis_qcodes_controller/trajectory/sqlite_store.py tests/test_cli_trajectory_actions.py
git commit -m "feat: add action trajectory query commands"
```

### Task 7: Update docs for new trajectory model + CLI workflow

**Files:**
- Modify: `docs/trajectory_model.md`
- Modify: `docs/cli_contract.md`
- Modify: `docs/quickstart_simulator.md`
- Modify: `docs/test_runbook.md`

**Step 1: Write the failing test**

Create a doc guard test:

```python
def test_docs_reference_trajectory_monitor_run_name_enforcement() -> None:
    text = Path("docs/cli_contract.md").read_text(encoding="utf-8")
    assert "config set" in text and "run_name" in text and "cleared after each run" in text
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_docs_trajectory_contract.py`
Expected: FAIL (new contract text missing)

**Step 3: Write minimal implementation**

Document:
- SQLite tables and timing model (`dt_s` + run start)
- default interval (`0.1`) and rotation (`6000`)
- configurable action window (default `2.5`)
- required `config set` before each `run`

**Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_docs_trajectory_contract.py`
Expected: PASS

**Step 5: Commit**

```bash
git add docs/trajectory_model.md docs/cli_contract.md docs/quickstart_simulator.md docs/test_runbook.md tests/test_docs_trajectory_contract.py
git commit -m "docs: describe sqlite trajectory monitor and run-name workflow"
```

### Task 8: Final verification + compatibility guard

**Files:**
- Modify (if needed): `nanonis_qcodes_controller/trajectory/reader.py`
- Modify (if needed): `tests/test_trajectory_journal.py`

**Step 1: Write the failing test**

```python
def test_legacy_jsonl_reader_still_operates() -> None:
    # existing behavior unchanged while sqlite monitor is primary
    ...
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_trajectory_journal.py`
Expected: FAIL only if regressions introduced

**Step 3: Write minimal implementation**

Apply only compatibility fixes needed to keep existing JSONL trajectory commands working while new monitor path is added.

**Step 4: Run full verification**

Run:
- `python -m pytest -q`
- `ruff check .`
- `black --check .`
- `mypy nanonis_qcodes_controller`

Expected: all PASS

**Step 5: Commit**

```bash
git add .
git commit -m "feat: ship sqlite-based trajectory monitor with inference actions"
```
