# Trajectory Model

## Objective
Provide a non-blocking, append-only event journal that captures command outcomes and state transitions during long-running experiments.

## Event schema
Each event is a JSON object written as one line (`.jsonl`) with fields:

- `event_id`: unique ID
- `timestamp_utc`: ISO-8601 UTC timestamp string
- `event_type`: event category (`command_result`, `state_transition`, `write_audit`, ...)
- `payload`: event-specific data

## Non-blocking behavior
- Producers submit events with `put_nowait` semantics.
- If queue is full, events are dropped and drop count increases.
- Instrument command path is never blocked by trajectory writes.

## File layout
- Directory: configurable (`trajectory.directory`)
- Segment files: `trajectory-<run_id>-<segment_index>.jsonl`
- Rotation: configurable max events per file (`trajectory.max_events_per_file`)

## Reader modes
- Batch/tail: read latest `N` events from files
- Follow: poll for newly appended events

Implemented tools:
- `scripts/trajectory_reader.py`
- `nanonis_qcodes_controller.trajectory.read_events(...)`
- `nanonis_qcodes_controller.trajectory.follow_events(...)`

## Metrics
`TrajectoryStats` tracks:
- `submitted`
- `written`
- `dropped`
- `last_error`
- `active_file`
- `segment_index`
