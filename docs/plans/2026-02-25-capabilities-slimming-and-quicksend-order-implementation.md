# Capabilities Slimming and QuickSend-Ordered Metadata Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `nqctl capabilities` return only `parameters` and `action_commands`, add `nqctl showall` for legacy full payload, and enforce quickSend `BodyType`/`ResponseType` ordering as the canonical source for `arg_fields`/`response_fields`.

**Architecture:** Add a command-source parser in manifest generation that reads `nanonis_spm.Nanonis` method source and extracts `quickSend` wire signatures. Rebuild command metadata fields from those signatures and docstring bullets by index, removing redundant keys (`args`, `arg_types`, `docstring_full`). Update extensions/CLI serialization and tests to enforce payload shape and key ordering.

**Tech Stack:** Python, argparse CLI, YAML manifest generation, pytest.

---

### Task 1: Add failing tests for new capabilities/showall contract and ordering

**Files:**
- Modify: `tests/test_cli.py`

**Step 1: Write failing test for slimmed `capabilities` payload**

Assert `payload.keys()` is exactly `{"parameters", "action_commands"}` for `_cmd_capabilities`.

**Step 2: Write failing test for new `showall` command**

Add parser/handler test for `nqctl showall` and assert payload includes legacy sections (`cli`, `observables`, `actions`, `policy`, `parameter_files`).

**Step 3: Write failing key-order tests**

Assert dictionary key order in emitted payload objects:
- `action_commands.items[0]`: first key `name`, second key `action_cmd`
- `action_cmd`: first key `command`
- `parameters.items[0]`: first key `label`

**Step 4: Run tests to confirm failures**

Run: `python -m pytest tests/test_cli.py -q`

Expected: FAIL due to old payload shape and missing `showall`.

### Task 2: Add quickSend signature extraction and slot mapping logic

**Files:**
- Modify: `nanonis_qcodes_controller/qcodes_driver/manifest_generator.py`
- Test: `tests/test_parameter_manifest_generator.py`

**Step 1: Add quickSend signature parser helpers**

Implement helpers to parse each method source and extract:
- `command_name`
- `body_type_tokens` (ordered)
- `response_type_tokens` (ordered)

**Step 2: Add command metadata record storing quickSend wire tokens**

Extend command discovery data model so each `CommandInfo` carries parsed quickSend tokens.

**Step 3: Rebuild `arg_fields`/`response_fields` from quickSend order**

For each command:
- map docstring argument bullets to `BodyType` index order,
- map docstring return bullets to `ResponseType` index order,
- include `wire_type` in each field.

Use placeholders when counts mismatch while preserving quickSend order.

**Step 4: Remove redundant command keys from generated entries**

Stop emitting `args`, `arg_types`, and `docstring_full` in generated `get_cmd`, `set_cmd`, and `action_cmd` blocks.

**Step 5: Add generator tests for global ordering behavior**

Add tests that validate:
- `Scan_BufferGet.response_fields` order matches `ResponseType`: number_of_channels, channel_indexes, pixels, lines,
- `Scan_BufferSet.arg_fields` order matches `BodyType`: channel_indexes, pixels, lines,
- representative non-scan command families also follow quickSend ordering.

**Step 6: Run focused tests**

Run: `python -m pytest tests/test_parameter_manifest_generator.py -q`

Expected: PASS.

### Task 3: Update extensions parser model for new schema and removed keys

**Files:**
- Modify: `nanonis_qcodes_controller/qcodes_driver/extensions.py`
- Modify: `nanonis_qcodes_controller/qcodes_driver/__init__.py`
- Test: `tests/test_qcodes_extensions.py`

**Step 1: Update command field dataclasses**

Keep/ensure support for:
- `response_fields` (with `wire_type`) for read commands,
- `arg_fields` (with `wire_type`) for write/action commands.

**Step 2: Remove parser dependence on deprecated keys**

Ensure loader works when `args`/`arg_types`/`docstring_full` are absent.

**Step 3: Add tests for new schema loading**

Assert parsed specs load correctly from YAML containing only the new minimal keys and structured fields.

**Step 4: Run focused tests**

Run: `python -m pytest tests/test_qcodes_extensions.py -q`

Expected: PASS.

### Task 4: Implement CLI command split and deterministic key ordering

**Files:**
- Modify: `nanonis_qcodes_controller/cli.py`
- Test: `tests/test_cli.py`

**Step 1: Add `showall` parser command**

Create new `showall` subcommand with the same optional backend-command flags old capabilities used.

**Step 2: Slim `_cmd_capabilities` payload**

Emit only:
- `parameters`
- `action_commands`

**Step 3: Move legacy payload to `_cmd_showall`**

Reuse/centralize previous full payload construction.

**Step 4: Enforce dict insertion order in payload builders**

Build capability item dicts in required key order.

**Step 5: Run focused CLI tests**

Run: `python -m pytest tests/test_cli.py -q`

Expected: PASS.

### Task 5: Regenerate manifests and perform full verification

**Files:**
- Modify: `config/parameters.yaml`
- Modify: `nanonis_qcodes_controller/resources/config/parameters.yaml`
- Optional docs updates: `README.md`, `docs/cli_contract.md`

**Step 1: Regenerate manifest**

Run: `python scripts/generate_parameters_manifest.py --output config/parameters.yaml`

**Step 2: Sync packaged manifest**

Run:
`python -c "from pathlib import Path; src=Path('config/parameters.yaml'); dst=Path('nanonis_qcodes_controller/resources/config/parameters.yaml'); dst.write_text(src.read_text(encoding='utf-8'), encoding='utf-8')"`

**Step 3: Update docs for command contract if needed**

Adjust README/CLI contract docs to reflect:
- slim `capabilities`
- new `showall`.

**Step 4: Run full checks**

Run:
- `ruff check .`
- `black --check .`
- `mypy nanonis_qcodes_controller`
- `python -m pytest $(git ls-files "tests/*.py")`

Expected: all PASS.
