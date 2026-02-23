# Trajectory Monitor Redesign (Inference-Only GUI Change Capture)

Date: 2026-02-22

## Objective
Implement a high-rate polling monitor that infers user-driven controller changes from state transitions and stores trajectories in SQLite with low-overhead schemas.

This design replaces the current event-style JSONL trajectory as the primary monitoring path for inference workloads.

## Scope

### In scope
- Polling-based inference of spec changes (no exact GUI event capture).
- SQLite storage for all trajectory outputs.
- Three logical trajectories:
  - signal trajectory (dense, high-rate)
  - specs trajectory (dense, high-rate)
  - action trajectory (sparse, change-only)
- CLI for monitor config/list/check/run.

### Out of scope
- Exact GUI click/menu/action telemetry from Nanonis GUI internals.
- Full daemon lifecycle in this first implementation (foreground first).

## Confirmed decisions
- Timing model: store one run start timestamp + per-row `dt_s` (actual elapsed seconds).
- Sampling interval target default: `0.1` s.
- SQLite for all three trajectories.
- Specs identity in logs: `spec_label` only (no payload index logging).
- For specs metadata: store `spec_label` and `vals`.
- Action window size configurable, default `+-2.5` s.
- `nqctl trajectory monitor config set` must run before `run`.
- `run_name` is single-use and cleared after each run to force explicit naming for every new run.

## Architecture

### Runtime flow
1. User stages monitor config with `trajectory monitor config set` (must include `run_name`).
2. User runs `trajectory monitor run`.
3. Runner validates config, opens SQLite DB, creates run metadata.
4. Poll loop executes at target interval:
   - samples selected signals
   - samples selected specs
   - computes actual `dt_s` from run start (monotonic-based elapsed converted to seconds)
   - writes rows into `signal_samples` and `spec_samples`
   - detects spec changes against prior spec snapshot and writes `action_events`
5. On completion/interrupt:
   - flush and close DB
   - clear staged `run_name` so next run requires explicit rename.

### Timing model
- `run_start_utc` stored once in run metadata.
- Each sample row stores:
  - `sample_idx` (monotonic integer)
  - `dt_s` (actual elapsed time from run start)
- No per-row ISO timestamp for dense tables.
- `action_events` keep ISO `detected_at_utc` in addition to `dt_s`.

This preserves actual timing under drift/jitter while minimizing write size.

## SQLite data model

### Core tables
- `runs`
  - `run_id` (PK)
  - `run_name` (unique per DB)
  - `run_start_utc`
  - `target_interval_s`
  - `signal_rotate_entries` (default 6000)
  - `spec_rotate_entries` (default 6000)
  - `action_window_s` (default 2.5)
  - `created_by_cli_version`

- `signal_catalog`
  - `run_id`
  - `signal_label`
  - `unit`
  - `value_type`

- `spec_catalog`
  - `run_id`
  - `spec_label`
  - `vals_json`
  - `unit`
  - `value_type`

- `signal_samples`
  - `run_id`
  - `sample_idx`
  - `segment_id` (increments every 6000 rows)
  - `dt_s`
  - `values_json` (map of `signal_label -> value`)

- `spec_samples`
  - `run_id`
  - `sample_idx`
  - `segment_id` (increments every 6000 rows)
  - `dt_s`
  - `values_json` (map of `spec_label -> value`)

- `action_events` (no cap/rotation)
  - `run_id`
  - `action_idx` (auto increment)
  - `detected_at_utc` (ISO)
  - `dt_s`
  - `spec_label`
  - `old_value_json`
  - `new_value_json`
  - `signal_window_start_dt_s`
  - `signal_window_end_dt_s`

- `monitor_errors` (optional but recommended)
  - `run_id`
  - `dt_s`
  - `where_context`
  - `error_text`

### Rotation semantics
- Rotation is logical via `segment_id` only for `signal_samples` and `spec_samples`.
- Segment boundary rule: `segment_id = sample_idx // rotate_entries`.
- `action_events` never rotate and are append-only.

## Action-window strategy
- Do not embed `+-window` signal rows into action entries.
- Store only window bounds (`start_dt_s`, `end_dt_s`) in `action_events`.
- Resolve contextual signal rows on query (`dt_s BETWEEN start AND end`).

Rationale: avoids duplication, minimizes writes, keeps action table compact.

## CLI contract (phase 1 foreground)

### Config management
- `nqctl trajectory monitor config show`
- `nqctl trajectory monitor config set --run-name <name> [options]`
- `nqctl trajectory monitor config clear`

`config set` options include:
- `--signals <label1,label2,...>` (default includes Z position/current)
- `--specs <label1,label2,...>`
- `--interval-s <float>` (default 0.1)
- `--rotate-entries <int>` (default 6000 for dense tables)
- `--action-window-s <float>` (default 2.5)
- `--directory <path>`
- `--db-name <name>`

### Discoverability
- `nqctl trajectory monitor list-signals`
- `nqctl trajectory monitor list-specs`

Both list commands return selectable labels and metadata (unit, type, vals where applicable).

### Run/explore
- `nqctl trajectory monitor run`
- `nqctl trajectory action list [--run-name ...]`
- `nqctl trajectory action show --run-name <name> --action-idx <n> [--with-signal-window]`

### Enforced run-name lifecycle
- `run` fails if staged config has empty `run_name`.
- After each run, `run_name` is cleared automatically.
- User must call `config set --run-name ...` before next run.

## Validation and constraints
- `spec_label` must be unique in selected spec set for a run.
- `signal_label` must be unique in selected signal set for a run.
- `interval_s > 0`, `rotate_entries >= 1`, `action_window_s >= 0`.
- Config and DB path must be writable before run start.

## Migration note
- Existing JSONL trajectory reader remains temporarily for backward compatibility.
- New monitor path writes SQLite and becomes the primary inference trajectory path.

## Testing strategy
- Unit tests:
  - config validation and run-name enforcement
  - dt_s monotonicity and non-negative constraints
  - change detection and action-window bounds
  - segment_id rotation at 6000 boundaries
- Integration tests:
  - monitor run against fake client with deterministic changing specs
  - action query returns expected signal context window
  - run_name cleared after monitor completion

## Future phase (daemon lifecycle)
- Add optional `start/stop/status` commands with PID/lock files.
- Foreground `run` remains the canonical debug path.
