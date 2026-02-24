# Full Docstring and Structured Metadata Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Emit full docstrings and structured argument/response metadata for all generated get/set/action command entries in `parameters.yaml`.

**Architecture:** Extend manifest generation with additive metadata fields and keep existing concise descriptions. Propagate new fields through YAML loading and CLI capabilities payloads without breaking existing keys.

**Tech Stack:** Python, pytest, YAML manifest generation.

---

### Task 1: Add docstring section parsing helpers

**Files:**
- Modify: `nanonis_qcodes_controller/qcodes_driver/manifest_generator.py`
- Test: `tests/test_parameter_manifest_generator.py`

**Step 1:** Add helper(s) to parse docstring `Arguments` and `Return arguments` sections into ordered entries.

**Step 2:** Add helper(s) to normalize full docstring text for `docstring_full`.

**Step 3:** Add helper(s) to convert parsed entries into structured row dictionaries for arg/response fields.

**Step 4:** Add focused tests for parser behavior with representative docstring snippets.

**Step 5:** Run: `python -m pytest tests/test_parameter_manifest_generator.py -k docstring -q`

### Task 2: Emit new metadata in generated parameter/action entries

**Files:**
- Modify: `nanonis_qcodes_controller/qcodes_driver/manifest_generator.py`
- Test: `tests/test_parameter_manifest_generator.py`

**Step 1:** In `_build_generated_parameter_entry`, enrich `get_cmd` with `docstring_full` and `response_fields`.

**Step 2:** Enrich `set_cmd` with `docstring_full` and `arg_fields` (including required inference).

**Step 3:** In `_build_generated_action_entry`, enrich `action_cmd` with `docstring_full` and `arg_fields`.

**Step 4:** Add tests using `Scan_FrameGet`/`Scan_BufferSet`-style docs to verify structured metadata content.

**Step 5:** Run: `python -m pytest tests/test_parameter_manifest_generator.py -q`

### Task 3: Propagate new fields through loader and capabilities output

**Files:**
- Modify: `nanonis_qcodes_controller/qcodes_driver/extensions.py`
- Modify: `nanonis_qcodes_controller/cli.py`
- Test: `tests/test_qcodes_extensions.py`
- Test: `tests/test_cli.py`

**Step 1:** Extend command spec dataclasses with optional structured metadata fields.

**Step 2:** Parse new YAML fields in `_parse_read_command`, `_parse_write_command`, `_parse_action_command`.

**Step 3:** Include these fields in capabilities payload builders when present.

**Step 4:** Update/add tests asserting fields survive load and appear in capabilities output.

**Step 5:** Run: `python -m pytest tests/test_qcodes_extensions.py tests/test_cli.py -q`

### Task 4: Regenerate manifests and verify repository-wide checks

**Files:**
- Modify: `config/parameters.yaml`
- Modify: `nanonis_qcodes_controller/resources/config/parameters.yaml`

**Step 1:** Regenerate manifest:

Run: `python scripts/generate_parameters_manifest.py --output config/parameters.yaml`

**Step 2:** Sync packaged manifest copy.

**Step 3:** Run full checks:
- `ruff check .`
- `black --check .`
- `mypy nanonis_qcodes_controller`
- `python -m pytest $(git ls-files "tests/*.py")`
