# Multi-Field Ramp Arg-Fields Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement `nqctl ramp` multi-field structured args using `--arg key=start:end:step[:interval]` plus fixed `--arg key=value` support.

**Architecture:** Replace positional ramp parsing in CLI with an arg-fields parser that classifies ramp tuples and fixed fields. Add a multi-field ramp execution path in the instrument driver that schedules per-field updates and sends structured `set` calls while preserving unspecified fields through existing autofill behavior. Keep safety/policy checks enforced through the existing write pipeline.

**Tech Stack:** Python, argparse CLI, QCodes driver (`instrument.py`), pytest.

---

### Task 1: Add failing CLI parser tests for multi-field ramp args

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `nanonis_qcodes_controller/cli.py`

**Step 1: Write failing test for 3-part tuple parsing**

Add test asserting:
- input `--arg Width=10e-9:20e-9:1e-9 --interval-s 0.1`
- parser classifies `Width` as ramp tuple with interval defaulted to global.

**Step 2: Write failing test for 4-part tuple parsing**

Add test asserting:
- input `--arg Height=8e-9:16e-9:1e-9:0.14 --interval-s 0.1`
- parser classifies interval override as `0.14`.

**Step 3: Write failing test for mixed fixed + ramp args**

Add test asserting:
- `--arg Width=... --arg Angle=0`
- `Angle` is fixed field, `Width` is ramp field.

**Step 4: Run focused test file to capture failures**

Run: `python -m pytest tests/test_cli.py -q`

### Task 2: Implement ramp arg parsing contract in CLI

**Files:**
- Modify: `nanonis_qcodes_controller/cli.py`

**Step 1: Add ramp arg parser helper**

Implement helper that converts repeatable `--arg` into:
- `ramp_fields` map (`start`, `end`, `step`, `interval_s`)
- `fixed_fields` map

Rules:
- tuple supports only 3 or 4 parts
- all numeric values parsed via existing float helpers
- step positive, interval positive when provided

**Step 2: Update ramp argparse surface**

Change `ramp` command arguments:
- remove positional `start/end/step`
- add repeatable `--arg key=value`
- keep required `--interval-s`

**Step 3: Update ramp handler `_cmd_ramp`**

Build parsed spec from `--arg` values and pass to driver multi-field planning/execution API.

**Step 4: Update help text and examples**

Use approved contract example:
- `nqctl ramp scan_frame --arg Width=10e-9:20e-9:1e-9 --arg Height=8e-9:16e-9:1e-9:0.14 --interval-s 0.1`

**Step 5: Run focused CLI tests**

Run: `python -m pytest tests/test_cli.py -q`

### Task 3: Add failing driver tests for multi-field ramp scheduling and autofill preservation

**Files:**
- Modify: `tests/test_qcodes_driver.py`
- Modify: `nanonis_qcodes_controller/qcodes_driver/instrument.py`

**Step 1: Add failing test for multi-field ramp plan generation**

Assert plan captures:
- multiple ramp fields
- per-field interval override behavior
- merged tick schedule

**Step 2: Add failing test for execution with fixed fields**

Assert each `set` call includes:
- current ramp values for active fields
- fixed field values unchanged

**Step 3: Add failing regression test for unspecified set fields unchanged**

Use scan-buffer-style parameter and assert fields not in ramp args are autofilled and preserved.

**Step 4: Run focused driver tests (expected fail)**

Run: `python -m pytest tests/test_qcodes_driver.py -q`

### Task 4: Implement multi-field ramp execution in driver

**Files:**
- Modify: `nanonis_qcodes_controller/qcodes_driver/instrument.py`

**Step 1: Add internal data structures**

Introduce typed structures for:
- ramp field definition
- fixed field definition
- merged timeline steps

**Step 2: Add multi-field planning API**

Implement `plan_parameter_ramp_fields(...)` that:
- validates writable spec and safety
- builds per-field targets from start/end/step
- merges by per-field intervals with global fallback

**Step 3: Add multi-field execution API**

Implement `ramp_parameter_fields(...)` that:
- executes timeline via repeated `set_parameter_fields`
- honors `plan_only`/policy mode
- records write audit entries

**Step 4: Preserve existing scalar ramp compatibility**

Retain `plan_parameter_ramp` / `ramp_parameter` behavior by delegating to the new engine for single-field ramps.

**Step 5: Run focused driver tests**

Run: `python -m pytest tests/test_qcodes_driver.py -q`

### Task 5: Update capabilities/docs output and user docs

**Files:**
- Modify: `README.md`
- Modify: `docs/cli_contract.md`
- Modify: `nanonis_qcodes_controller/cli.py` (payload/help text if needed)

**Step 1: Update README ramp examples**

Replace scalar positional example with approved `--arg` contract.

**Step 2: Update CLI contract command description**

Document `ramp` as arg-fields driven with tuple syntax.

**Step 3: Ensure output payload includes ramp fields/fixed fields metadata**

If added in handler, document briefly and keep payload deterministic.

**Step 4: Run doc-focused tests**

Run: `python -m pytest tests/test_docs_parameters_manifest.py tests/test_release_docs.py -q`

### Task 6: Full verification and release readiness

**Files:**
- Verify all modified files

**Step 1: Run quality checks**

Run:
- `ruff check .`
- `black --check .`
- `mypy nanonis_qcodes_controller`

**Step 2: Run full test suite**

Run: `python -m pytest`

**Step 3: Summarize contract changes**

Prepare release-note bullets for:
- new ramp arg tuple contract
- per-field interval override behavior
- unspecified field preservation behavior
