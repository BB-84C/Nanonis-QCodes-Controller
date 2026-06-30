# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

## [0.2.0] - 2026-06-30

This release rebuilds the project around a single thesis: a thin, fast
CLI over `nanonis-spm` for agent-driven Nanonis SPM controller
automation. It is intentionally **breaking**. The package, CLI command,
and internal API surface are all renamed; everything not on that
critical path was cut. Command names, argument shapes, and JSON
response schemas of the surviving subcommands are preserved.

### Breaking changes
- Renamed PyPI package, Python package, CLI command, and instrument
  class. See the migration map at the bottom of this entry.
- The previous embedded instrument wrapper no longer derives from an
  external framework base class; it is now a plain class with the same
  constructor and method surface.
- The heavy optional framework integration that used to back the
  embedded driver is gone; `nanonis-spm` is now a hard runtime
  dependency (previously gated behind an extra).
- Removed the trajectory subsystem entirely. The following CLI
  subcommands no longer exist (the agent contract on
  `get`/`set`/`ramp`/`act` payloads also no longer contains the
  `"trajectory"` block):
  - `trajectory tail`, `trajectory follow`
  - `trajectory action list`, `trajectory action show`
  - `trajectory monitor config show|set|clear`
  - `trajectory monitor list-signals`, `trajectory monitor list-specs`
  - `trajectory monitor run`
- Removed the `NANONIS_TRAJECTORY_*` environment variables and the
  `trajectory:` section in `config/default_runtime.yaml`.

### Added
- `nspmctl daemon start|stop|status|restart|logs` subcommand group.
- Persistent background daemon (`nspmctl/daemon.py`) that holds one warm
  `NanonisController` + open TCP socket so subsequent calls only pay
  loopback IPC + Python CLI startup, not the full nanonis-spm import +
  parameter-manifest parse + TCP connect every time.
- Automatic, lazy daemon spawn: on a cold first call, the request runs
  inline AND a background daemon is started so the next agent tool call
  is warm. No manual `daemon start` required for the common case.
- `--no-daemon` CLI flag and `NSPMCTL_NO_DAEMON=1` environment variable
  for diagnostics, CI, and emergencies.
- Ultra-thin `nspmctl/__main__.py` entry that performs daemon routing
  using only stdlib + `nspmctl.daemon` and refuses to import the heavy
  CLI / client / controller surface on the warm path. New unit tests
  enforce this invariant (`tests/test_daemon.py`).

### Changed
- Manifest yaml loading now uses libyaml's `CSafeLoader` when available
  (~6x faster on the 651KB `parameters.yaml`) with a pure-Python
  `SafeLoader` fallback.
- `nspmctl.controller.extensions` caches parsed manifest roots via
  `functools.lru_cache`, so a single CLI invocation parses the manifest
  at most once instead of twice (once for parameter specs, once for
  action specs).
- Daemon idle timeout: 30 minutes.

### Performance
End-to-end `nspmctl get bias_v` on the simulator:

| Mode                                  | p50      | speedup vs 0.1.10 |
|---------------------------------------|---------:|-------------------|
| `nqctl get bias_v` (0.1.10 baseline)  | 3070 ms  | -                 |
| `nspmctl --no-daemon get bias_v`      |  525 ms  | 5.8 x             |
| `nspmctl get bias_v` (warm daemon)    |  105 ms  | 29 x              |

Raw `nanonis_spm` end-to-end floor on this machine: ~110 ms (import +
TCP connect + one Bias_Get + close). After the daemon eats the import
and connect once, warm CLI calls converge toward the loopback IPC +
Python startup floor (~80-100 ms).

### Migration from v0.1.x

| Old (v0.1.x)                                                            | New (v0.2.0)                                                  |
|-------------------------------------------------------------------------|---------------------------------------------------------------|
| `pip install nanonis-qcodes-controller`                                 | `pip install nspmctl`                                         |
| `nqctl <subcommand>`                                                    | `nspmctl <subcommand>`                                        |
| `import nanonis_qcodes_controller`                                      | `import nspmctl`                                              |
| `from nanonis_qcodes_controller.qcodes_driver import QcodesNanonisSTM`  | `from nspmctl.controller import NanonisController`            |
| `nanonis_qcodes_controller.config.load_settings`                        | `nspmctl.config.load_settings`                                |
| `nanonis_qcodes_controller.client.create_client`                        | `nspmctl.client.create_client`                                |

The new `NanonisController` keeps the same constructor signature and
method names; the only required edit is the import line.

## [0.1.10] - 2026-02-26

### Added
- Added `nqctl policy set` to update runtime policy flags directly from CLI (`--allow-writes`, `--dry-run`) with persisted config updates.

### Changed
- Switched packaged runtime policy defaults to live mode (`allow_writes=true`, `dry_run=false`) in both runtime YAML defaults and code fallback defaults.
- Updated README policy guidance with `nqctl policy set` usage examples.

## [0.1.9] - 2026-02-26

### Fixed
- Fixed CLI argument parsing for negative scientific-notation positional values (for example `-1e-11`) so `nqctl set` and `nqctl ramp` no longer misclassify them as option flags.
- Updated `nqctl ramp` step handling to accept signed input and use positive step magnitude internally, preserving expected decreasing-ramp behavior.

## [0.1.8] - 2026-02-25

### Changed
- Migrated parameter handling to a methods-only schema driven by structured command metadata (`arg_fields`/`response_fields`), removing scalar-oriented parameter keys from generated manifests and CLI capability payloads.
- Removed scalar loader/public API exports (`ScalarParameterSpec`, `load_scalar_parameter_specs`) from `qcodes_driver` and aligned extension tests with methods-only plus legacy-key compatibility parsing.
- Updated driver behavior and tests to use the structured methods interface (`get_parameter_snapshot`, `set_parameter_fields`, `execute_action`) as the authoritative contract.
- Updated CLI contract docs to define `nqctl set <parameter> --arg key=value` as the structured set surface.

## [0.1.7] - 2026-02-25

### Added
- Added contract test coverage for private-index release checklist commands in `tests/test_release_checklist_contract.py`.

### Changed
- Updated `docs/release_private_index.md` to list required pre-release verification commands: `ruff check .`, `black --check .`, `mypy nanonis_qcodes_controller`, `pytest`, and `python -m build`.
- Expanded distribution readiness documentation and release tracking with concrete private-index release verification notes.
- Updated `nqctl capabilities` to expose rich `parameters.items[*]` metadata (`get_cmd`, `set_cmd`, validators, safety) for agent-driven planning of `get`/`set`/`ramp`.
- Updated `nqctl capabilities` to remove top-level parameter `description` output and keep descriptions on `get_cmd`/`set_cmd` only.
- Updated manifest generation to import all callable `nanonis_spm.Nanonis` methods; non-`Get`/`Set` methods now populate root `actions` entries with `action_cmd` metadata and action safety mode.
- Added `nqctl act <action_name> --arg key=value` with policy-aware action execution and action metadata in `nqctl capabilities` (`action_commands.items[*]`).
- Updated GitHub-release install examples in `README.md` to use `<version>` placeholders.
- Anchored command discovery at `Bias_Set`, ignored earlier callable helper methods, and dropped curated-only stale action entries so generated manifests now align with discovered backend action commands.
- Enriched all generated `get_cmd`/`set_cmd`/`action_cmd` entries with `docstring_full` plus structured metadata fields (`response_fields` and `arg_fields`) extracted from Nanonis docstrings.
- Extended YAML loaders and `nqctl capabilities` output to expose the new structured command metadata for agent-side planning and interpretation.
- Reworked README CLI guidance to remove legacy `parameters discover/validate` workflow emphasis and clarify `act` versus action metadata surfaces.
- Fixed structured `set` autofill to preserve existing multi-arg field values by name mapping from `get` snapshots (including tuple-like channel index parsing for `scan_buffer`).
