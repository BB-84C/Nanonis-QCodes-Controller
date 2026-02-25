# Parameter Schema Streamline (Methods-Only) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove scalar-oriented parameter schema keys (`unit`, `value_type`, `vals`, `snapshot_value`) and migrate runtime/CLI/QCodes behavior to fully structured methods-only command execution.

**Architecture:** Shift parameter semantics to command metadata only (`arg_fields`/`response_fields` + safety). Remove QCodes scalar registration path and expose structured method APIs as the authoritative interface for reads/writes/actions. Keep loader backward-compatible with old YAML by ignoring removed keys.

**Tech Stack:** Python, QCodes driver layer, YAML manifest generator/loader, pytest.

---

### Task 1: Add failing tests for methods-only schema and capabilities payload

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `tests/test_qcodes_extensions.py`
- Modify: `tests/test_parameter_manifest_generator.py`

**Step 1:** Add tests asserting generated parameters no longer include top-level `unit`, `value_type`, `vals`, `snapshot_value`.

**Step 2:** Add tests asserting `nqctl capabilities` parameter items omit the same keys.

**Step 3:** Add loader tests to ensure legacy keys are accepted/ignored (backward-compatible parse).

**Step 4:** Run focused tests (expected fail initially):

Run: `python -m pytest tests/test_cli.py tests/test_qcodes_extensions.py tests/test_parameter_manifest_generator.py -q`

### Task 2: Remove scalar keys from generator and emitted manifest

**Files:**
- Modify: `nanonis_qcodes_controller/qcodes_driver/manifest_generator.py`
- Modify: `config/parameters.yaml` (regenerated)
- Modify: `nanonis_qcodes_controller/resources/config/parameters.yaml` (synced)

**Step 1:** Stop emitting top-level parameter keys: `unit`, `type/value_type`, `vals`, `snapshot_value`.

**Step 2:** Keep generated parameter entries limited to `label`, `get_cmd`, `set_cmd`, `safety`.

**Step 3:** Regenerate `config/parameters.yaml` and sync packaged copy.

**Step 4:** Run focused generator tests:

Run: `python -m pytest tests/test_parameter_manifest_generator.py -q`

### Task 3: Update loader model to structured-only parameter contract

**Files:**
- Modify: `nanonis_qcodes_controller/qcodes_driver/extensions.py`
- Modify: `nanonis_qcodes_controller/qcodes_driver/__init__.py`
- Test: `tests/test_qcodes_extensions.py`

**Step 1:** Make parameter dataclass optional/default for removed scalar keys and ensure runtime logic does not require them.

**Step 2:** Parser should ignore legacy keys when present and construct structured specs from command blocks.

**Step 3:** Update tests for both minimal new schema and legacy compatibility.

**Step 4:** Run focused tests:

Run: `python -m pytest tests/test_qcodes_extensions.py -q`

### Task 4: Remove scalar QCodes parameter registration path

**Files:**
- Modify: `nanonis_qcodes_controller/qcodes_driver/instrument.py`
- Test: `tests/test_qcodes_driver.py`

**Step 1:** Replace `_register_parameters` scalar `add_parameter` behavior with methods-only interface (no scalar parameter assumptions).

**Step 2:** Ensure reads/writes route exclusively through:
- `get_parameter_snapshot`
- `set_parameter_fields`
- `execute_action`

**Step 3:** Update any internal logic still using `value_type`/`vals`-derived scalar conversion to field-type-driven conversion.

**Step 4:** Add tests that multi-arg commands are fully handled and all fields are considered (not first or first-k only).

**Step 5:** Run focused tests:

Run: `python -m pytest tests/test_qcodes_driver.py -q`

### Task 5: Update CLI payload and docs for streamlined schema

**Files:**
- Modify: `nanonis_qcodes_controller/cli.py`
- Modify: `README.md`
- Modify: `docs/cli_contract.md` (if contract examples include removed keys)
- Test: `tests/test_cli.py`

**Step 1:** Remove removed scalar keys from capabilities payload serialization.

**Step 2:** Keep CLI `get/set/act` output structured and field-driven.

**Step 3:** Update docs to reflect methods-only schema shape.

**Step 4:** Run focused tests:

Run: `python -m pytest tests/test_cli.py tests/test_docs_parameters_manifest.py tests/test_release_docs.py -q`

### Task 6: Full verification and release-readiness check

**Files:**
- Verify all modified files

**Step 1:** Run lint/format/type checks:
- `ruff check .`
- `black --check .`
- `mypy nanonis_qcodes_controller`

**Step 2:** Run full test suite:
- `python -m pytest $(git ls-files "tests/*.py")`

**Step 3:** If all pass, summarize breaking changes explicitly for release notes.
