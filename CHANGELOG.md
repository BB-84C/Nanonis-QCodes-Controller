# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

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
