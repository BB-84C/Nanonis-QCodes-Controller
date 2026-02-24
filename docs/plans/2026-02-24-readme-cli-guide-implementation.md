# README CLI Guide Refresh Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Update README to remove legacy `parameters` authoring workflow language, clarify `act` vs action metadata, and provide complete practical `nqctl` usage guidance.

**Architecture:** Keep this as a docs-only change centered in `README.md`, with no CLI behavior changes. Reorganize CLI documentation by user intent (inspect, execute, trajectory) and include explicit terminology for `act`/`actions list`/`capabilities.action_commands` to reduce ambiguity.

**Tech Stack:** Markdown docs, pytest docs contract tests.

---

### Task 1: Remove legacy parameter-workflow messaging

**Files:**
- Modify: `README.md`
- Test: `tests/test_docs_parameters_manifest.py`

**Step 1: Edit README config section to remove discover/validate workflow emphasis**

Update the Configure/manifest guidance to keep:
- `config/parameters.yaml` as unified manifest source of truth,
- brief generator script mention,

and remove the legacy "discover/scaffold/validate" workflow instructions from README.

**Step 2: Remove the `## Parameter extension workflow` section**

Delete the section and command examples for:
- `nqctl parameters discover`
- `nqctl parameters validate`

**Step 3: Run docs manifest test**

Run: `python -m pytest tests/test_docs_parameters_manifest.py -q`

Expected: PASS.

### Task 2: Clarify action terminology and command usage

**Files:**
- Modify: `README.md`

**Step 1: Add a new subsection explaining `act` vs action metadata**

Document three distinct concepts clearly:
- `nqctl act <action_name> --arg key=value`: executes one backend action command.
- `nqctl actions list`: lists high-level CLI action descriptors.
- `nqctl capabilities`: includes `action_commands.items[*]` (machine-readable executable action inventory).

**Step 2: Expand CLI usage coverage for current commands**

Add grouped command usage with concise purpose + examples for:
- `capabilities`, `observables list`, `actions list`, `policy show`, `backend commands`, `doctor`
- `get`, `set`, `ramp`, `act`
- `trajectory tail`, `trajectory follow`, `trajectory action list/show`,
  `trajectory monitor config show/set/clear`, `trajectory monitor list-signals`,
  `trajectory monitor list-specs`, `trajectory monitor run`

**Step 3: Keep examples consistent with current CLI contract**

Ensure all command names and flags match parser definitions in
`nanonis_qcodes_controller/cli.py`.

### Task 3: Validate docs integrity and consistency

**Files:**
- Verify: `README.md`

**Step 1: Run docs link/contract tests**

Run: `python -m pytest tests/test_docs_parameters_manifest.py tests/test_release_docs.py -q`

Expected: PASS.

**Step 2: Smoke-check help surface**

Run: `python -m nanonis_qcodes_controller.cli --help`

Expected: exits successfully and lists command groups aligned with README wording.

**Step 3: Commit docs update**

```bash
git add README.md
git commit -m "docs: refresh README command guide for action-driven workflow"
```
