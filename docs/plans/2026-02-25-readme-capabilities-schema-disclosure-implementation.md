# README Capabilities Schema Disclosure Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add formal JSON Schema disclosure in `README.md` for `nqctl capabilities` parameter and action command items.

**Architecture:** Keep the README update documentation-only and aligned with runtime payload shape emitted by CLI collectors. Add one focused section with two JSON Schema blocks that match current keys and nested command field structures.

**Tech Stack:** Markdown, JSON Schema Draft 2020-12, pytest docs contract tests.

---

### Task 1: Add README schema section for capabilities parameter and action items

**Files:**
- Modify: `README.md`
- Reference: `nanonis_qcodes_controller/cli.py` (`_collect_parameter_capabilities`, `_collect_action_command_capabilities`)

**Step 1: Add a new section in the CLI guide**
- Insert a section titled `Capabilities JSON schema` near the existing `nqctl capabilities` guidance.
- Add one short intro line clarifying these schemas describe `nqctl capabilities` response item shapes.

**Step 2: Add `parameters.items[*]` JSON Schema block**
- Use Draft 2020-12.
- Include fields:
  - `name`, `label`, `readable`, `writable`, `has_ramp`, `get_cmd`, `set_cmd`, `safety`.
- Define nested schemas for:
  - `arg_fields` items
  - `response_fields` items
  - `get_cmd` and `set_cmd`
  - `safety`

**Step 3: Add `action_commands.items[*]` JSON Schema block**
- Use Draft 2020-12.
- Include fields:
  - `name`, `action_cmd`, `safety_mode`
- Define nested `action_cmd.arg_fields` item schema and `safety_mode` enum.

**Step 4: Keep content concise and contract-focused**
- Do not include deprecated field explanations.
- Do not introduce behavior prose beyond schema disclosure.

### Task 2: Verify docs consistency

**Files:**
- Verify: `README.md`
- Test: `tests/test_docs_parameters_manifest.py`
- Test: `tests/test_release_docs.py`

**Step 1: Run focused docs tests**

Run: `python -m pytest tests/test_docs_parameters_manifest.py tests/test_release_docs.py -q`

**Step 2: Run formatting/lint check for touched markdown if needed**
- Confirm README renders correctly with fenced `json` blocks.

**Step 3: Review final diff for scope control**
- Ensure only README and plan docs changed for this request.
