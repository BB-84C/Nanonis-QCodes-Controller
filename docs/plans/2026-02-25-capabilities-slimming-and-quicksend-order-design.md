# Capabilities Slimming and QuickSend-Ordered Metadata Design

Date: 2026-02-25

## Goal

Align CLI contract and manifest metadata with agent-oriented needs:

1. `nqctl capabilities` returns only `parameters` and `action_commands`.
2. Add `nqctl showall` to expose the legacy full payload.
3. Use `quickSend(command, Body, BodyType, ResponseType)` as the only ordering
   source for command argument/response metadata.
4. Remove redundant command keys: `args`, `arg_types`, `docstring_full`.
5. Ensure consistent JSON key ordering for easier agent consumption.

## Contract Changes

### `nqctl capabilities`

Return only:

- `parameters`
- `action_commands`

No `cli`, `observables`, `actions`, `policy`, `parameter_files`, or
`backend_commands` in this command.

### `nqctl showall`

Return legacy full payload (old capabilities behavior), including optional
backend command list when requested.

## Metadata Model Changes

### Remove redundant keys

Drop from generated YAML and capabilities payload command blocks:

- `args`
- `arg_types`
- `docstring_full`

### Keep and strengthen structured keys

- `get_cmd`: `command`, `payload_index`, `description`, `response_fields`
- `set_cmd`: `command`, `value_arg`, `description`, `arg_fields`
- `action_cmd`: `command`, `description`, `arg_fields`

Each field entry adds protocol type token:

- `wire_type` (raw quickSend token, e.g. `i`, `*i`, `+*i`, `f`)

Field shapes:

- `arg_fields[*]`: `{name, type, unit, required, description, wire_type}`
- `response_fields[*]`: `{index, name, type, unit, description, wire_type}`

## Ground Truth and Mapping Rules

### Ordering source

For each command, parse source and extract `quickSend(...)` call.

- `BodyType` order => canonical `arg_fields` order.
- `ResponseType` order => canonical `response_fields` order.

### Docstring mapping

Parse `Arguments:` / `Return arguments` bullet entries and map by index onto
the corresponding quickSend slots.

If counts differ, preserve quickSend slot ordering and fill missing metadata
with safe placeholders while recording diagnostics.

## JSON Key Ordering Requirements

- `action_commands.items[i]`: `name`, `action_cmd`, `safety_mode`
- `action_cmd`: `command` appears first
- `parameters.items[i]`: `label` appears first

## Validation and Coverage

- Add command-source parser tests for quickSend extraction and slot ordering.
- Add generator tests confirming arg/response field ordering follows
  `BodyType`/`ResponseType` globally (sampled across command families).
- Add CLI tests for:
  - slim capabilities payload shape,
  - new `showall` command behavior,
  - object key ordering expectations.
