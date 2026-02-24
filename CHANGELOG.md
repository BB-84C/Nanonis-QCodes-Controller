# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

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
