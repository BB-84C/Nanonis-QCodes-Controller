# Structured set/get/act Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `nqctl set/get/act` use only `arg_fields`/`response_fields` runtime semantics and remove `set_cmd.value_arg` from YAML/capabilities.

**Architecture:** Shift command execution from legacy scalar-centric keys (`value_arg`, `args`, `payload_index`-only usage) to structured field descriptors. Keep command-level compatibility where safe, but route all argument binding, validation, coercion, and ordering through field lists derived from quickSend ground truth.

**Tech Stack:** Python CLI (`argparse`), QCoDeS driver runtime, YAML manifest generation, pytest.

---

### Task 1: Add failing runtime tests for structured get/set/act behavior

**Files:**
- Modify: `tests/test_qcodes_driver.py`
- Modify: `tests/test_cli.py`

**Step 1: Write failing `get` test for multi-field response emission**

Add a test where a parameter has multiple `response_fields` and verify `get` path returns structured values for all fields, not just one scalar.

**Step 2: Write failing `set` partial-update test**

Add a scan-buffer-style test:
- provide only `Pixels`,
- runtime reads current values,
- runtime sends complete ordered set args with unchanged fields filled.

**Step 3: Write failing `set/act` empty-input and required-arg tests**

Assert clear validation errors when required args are missing and cannot be auto-filled.

**Step 4: Write failing capabilities test for removed `set_cmd.value_arg`**

Assert capabilities `set_cmd` excludes `value_arg`.

**Step 5: Run focused tests (expect fail)**

Run: `python -m pytest tests/test_qcodes_driver.py tests/test_cli.py -q`

Expected: FAIL before implementation.

### Task 2: Remove `set_cmd.value_arg` from schema generation and output

**Files:**
- Modify: `nanonis_qcodes_controller/qcodes_driver/manifest_generator.py`
- Modify: `nanonis_qcodes_controller/qcodes_driver/extensions.py`
- Modify: `nanonis_qcodes_controller/cli.py`

**Step 1: Stop emitting `set_cmd.value_arg` in generator**

Ensure generated set command metadata is fully represented by `arg_fields` ordering and `required/default` flags.

**Step 2: Make loader backward-compatible for existing files**

If `value_arg` exists in legacy YAML, infer required field flags without requiring it in new output.

**Step 3: Remove `value_arg` from capabilities serialization**

`_collect_parameter_capabilities` should not include `set_cmd.value_arg`.

**Step 4: Run focused tests**

Run: `python -m pytest tests/test_qcodes_extensions.py tests/test_cli.py -q`

Expected: PASS.

### Task 3: Rework runtime set/get execution to structured fields

**Files:**
- Modify: `nanonis_qcodes_controller/qcodes_driver/instrument.py`
- Modify: `nanonis_qcodes_controller/cli.py`
- Test: `tests/test_qcodes_driver.py`

**Step 1: Implement structured read extraction helper**

Map backend response payload to `response_fields` ordered output.

**Step 2: Implement set-argument planner from `arg_fields`**

Algorithm:
- parse user provided overrides,
- if required args missing and `get_cmd` exists, read current state,
- map current response to missing set fields by normalized names,
- build final backend args in `arg_fields` order.

**Step 3: Update CLI `set` parser usage to support repeatable `--arg key=value` canonical mode**

Retain scalar shorthand only when exactly one writable target field can be determined.

**Step 4: Update CLI `get` payload formatting**

Emit multi-field structure when more than one response field exists.

**Step 5: Run focused tests**

Run: `python -m pytest tests/test_qcodes_driver.py tests/test_cli.py -q`

Expected: PASS.

### Task 4: Tighten action execution to pure `arg_fields` contract

**Files:**
- Modify: `nanonis_qcodes_controller/qcodes_driver/instrument.py`
- Test: `tests/test_qcodes_driver.py`

**Step 1: Ensure action arg resolution uses only `arg_fields`**

No fallback to removed legacy keys.

**Step 2: Add explicit required/default and unknown-arg validation tests**

Cover empty-input behavior and informative error text.

**Step 3: Run focused tests**

Run: `python -m pytest tests/test_qcodes_driver.py -q`

Expected: PASS.

### Task 5: Regenerate manifests and run full verification

**Files:**
- Modify: `config/parameters.yaml`
- Modify: `nanonis_qcodes_controller/resources/config/parameters.yaml`

**Step 1: Regenerate manifest**

Run: `python scripts/generate_parameters_manifest.py --output config/parameters.yaml`

**Step 2: Sync packaged copy**

Run: `python -c "from pathlib import Path; src=Path('config/parameters.yaml'); dst=Path('nanonis_qcodes_controller/resources/config/parameters.yaml'); dst.write_text(src.read_text(encoding='utf-8'), encoding='utf-8')"`

**Step 3: Run full checks**

Run:
- `ruff check .`
- `black --check .`
- `mypy nanonis_qcodes_controller`
- `python -m pytest $(git ls-files "tests/*.py")`

Expected: all PASS.
