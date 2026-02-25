# Structured set/get/act Runtime Design

Date: 2026-02-25

## Goal

Make runtime behavior fully follow manifest structured metadata:

- `nqctl get` uses `response_fields` as source-of-truth output shape.
- `nqctl set` uses `arg_fields` as source-of-truth input shape.
- `nqctl act` uses `arg_fields` as source-of-truth input shape.

Also remove `set_cmd.value_arg` from YAML and capabilities payload.

## Problem

Current contract mismatch:

- `get` returns only one scalar (`payload_index`) even when backend returns multiple
  fields.
- `set` assumes one target argument (`value_arg`) and cannot represent partial
  multi-arg updates directly.
- `act` is multi-arg but still has legacy/default behaviors that are not purely
  `arg_fields`-driven.

## Decision

Adopt structured-first runtime semantics (single model for all commands).

### 1) Read behavior

`get` returns all available response fields in order, keyed by normalized field
name (plus optional raw list for exact index mapping when needed).

### 2) Write behavior

`set` supports repeatable `--arg key=value` and validates strictly against
`set_cmd.arg_fields`.

Missing required args are auto-filled only when both are true:

- matching `get_cmd` exists, and
- field can be mapped from current response snapshot.

If any required arg remains unresolved, fail with explicit error.

### 3) Action behavior

`act` remains `--arg key=value` and strictly follows `action_cmd.arg_fields` for
name validation, typing, required/default behavior, and ordering.

## Partial update policy

Example: update only `Pixels` for `scan_buffer` while preserving others.

- Input: `nqctl set scan_buffer --arg Pixels=512`
- Runtime:
  1. parse provided args,
  2. read current values through `get_cmd`,
  3. map current response to missing required set fields,
  4. send full backend set args in `arg_fields` order.

## Schema changes

- Remove `set_cmd.value_arg` from generated YAML.
- Keep only structured metadata for command signatures:
  - `arg_fields`
  - `response_fields`
- Keep command-level `description`.

## CLI contract updates

- `nqctl get <parameter>` returns structured value payload for multi-field
  responses.
- `nqctl set <parameter> --arg key=value [--arg ...]` is canonical write form.
- Legacy scalar `set <parameter> <value>` can remain as compatibility shorthand
  only when exactly one writable target field exists.

## Validation strategy

- Add runtime tests for partial update merge behavior (`scan_buffer`-style case).
- Add tests for strict arg validation and empty-input behavior on set/act.
- Add tests confirming `get` emits full structured response for multi-field
  parameters.
