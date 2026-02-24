# README CLI Guide Design

Date: 2026-02-24

## Goal

Refresh `README.md` so it reflects the current manifest-driven workflow and CLI
contract:

1. Remove legacy parameter-authoring workflow messaging (`nqctl parameters
   discover`/`validate`) from README.
2. Explain the difference between `nqctl act`, `nqctl actions list`, and
   `action_commands` in `nqctl capabilities`.
3. Provide a practical command-usage guide covering the current `nqctl` surface
   that users operate day to day.

## Current Gaps

- README still has a "Parameter extension workflow" section centered on
  discover/regenerate/validate steps, which no longer matches the intended
  default user workflow.
- The "action" model can be confusing because three related concepts are shown
  in CLI output and help:
  - runtime execution command (`act`),
  - static action descriptors (`actions list`),
  - manifest-derived executable action commands (`capabilities.action_commands`).
- Command usage is spread across sections; readers do not get one concise,
  complete command map.

## Proposed Structure

### 1) Keep config section concise

- Retain mention that unified specs live in `config/parameters.yaml` with
  `parameters` and `actions` roots.
- Keep generator script mention as maintenance detail, without presenting
  discover/validate as standard README workflow.

### 2) Add "act vs actions" explainer

- Add a dedicated subsection under CLI usage:
  - `nqctl act`: executes one backend action command from manifest.
  - `nqctl actions list`: lists high-level CLI action descriptors.
  - `nqctl capabilities`: machine-readable action command inventory under
    `action_commands.items[*]`.

### 3) Replace fragmented examples with grouped command usage

- Introduce grouped command sections with one-line purpose + canonical examples:
  - Inspect and introspect: `capabilities`, `observables list`, `actions list`,
    `policy show`, `backend commands`, `doctor`.
  - Execute operations: `get`, `set`, `ramp`, `act`.
  - Trajectory utilities: `trajectory tail`, `follow`, `action list/show`,
    `monitor config/show/set/clear/list-signals/list-specs/run`.

## Non-Goals

- No CLI code behavior changes.
- No schema changes.
- No removal of internal/helper commands from CLI parser in this task.

## Validation

- Ensure README still references `config/parameters.yaml`.
- Run docs-related tests:
  - `python -m pytest tests/test_docs_parameters_manifest.py tests/test_release_docs.py`
