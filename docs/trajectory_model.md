# Trajectory Model

## Objective
Capture monitor data in a queryable SQLite store for post-run analysis and action inspection.

## Storage model
Each monitor run writes to one SQLite database (`artifacts/trajectory/trajectory-monitor.sqlite3` by default).

The run schema is organized as three trajectories:
- `signal_samples`: dense sampled signal values.
- `spec_samples`: dense sampled spec/state values.
- `action_events`: sparse detected spec-change events.

All three use the same run-relative timeline:
- `dt_s`: seconds since run start, based on monitor monotonic scheduling.
- `run_start_utc`: ISO-8601 UTC run start stamp stored in run metadata/catalog segment metadata.

## Timing and segmentation
- Sampling is scheduled at fixed `interval_s`.
- Signal and spec trajectories rotate by segment using `rotate_entries`.
- Default dense rotation is `6000` entries per segment for both signal and spec catalogs.

## Action events
- Action events are emitted on spec value changes (`action_kind=spec-change`).
- `detected_at_utc` is stored as an ISO-8601 UTC timestamp.
- Each event includes a signal window in run time:
  - `signal_window_start_dt_s = dt_s - action_window_s`
  - `signal_window_end_dt_s = dt_s + action_window_s`
- `action_window_s` is configurable; default is `2.5` seconds.

## Practical query paths
- CLI action queries: `nqctl trajectory action list` and `nqctl trajectory action show`.
- Include sampled context around an action with `--with-signal-window` on `action show`.
