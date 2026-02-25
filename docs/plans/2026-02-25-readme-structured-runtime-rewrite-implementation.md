# README Structured Runtime Rewrite Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fully rewrite README CLI command guide to match structured runtime semantics for `capabilities`, `showall`, `set`, `get`, and `act`.

**Architecture:** Keep this as a docs-only rewrite centered on `README.md`. Replace the existing command-guide sections with a cohesive flow that explains command intent, canonical syntax (`--arg` for set/act), and deterministic defaulting behavior for partial writes.

**Tech Stack:** Markdown, pytest docs checks, CLI help smoke checks.

---

### Task 1: Rewrite inspect/introspect and command-contract sections

**Files:**
- Modify: `README.md`

**Step 1: Rewrite Inspect section with current payload split**

Document:
- `nqctl capabilities` returns only `parameters` and `action_commands`
- `nqctl showall` returns legacy full payload

**Step 2: Update examples for command discovery and policy checks**

Keep concise examples for:
- `nqctl backend commands --match ...`
- `nqctl doctor --command-probe`
- `nqctl policy show`

**Step 3: Ensure wording avoids old payload assumptions**

No references to old full payload under `capabilities`.

### Task 2: Rewrite execute-operations section around structured args

**Files:**
- Modify: `README.md`

**Step 1: Rewrite `get` behavior docs**

Explain that `get` can return structured multi-field values and include example with a multi-response parameter.

**Step 2: Rewrite `set` docs with canonical syntax**

Make `--arg key=value` the primary form and clearly mark positional `set <param> <value>` as scalar shorthand.

**Step 3: Add partial-update defaulting mechanism bullets**

Describe in order:
- explicit args override,
- one `get` snapshot for missing required args,
- name-based field matching (not index alignment),
- optional fallback defaults when unresolved.

**Step 4: Rewrite `act` docs similarly**

Show multi-arg action invocation and required/default handling through `arg_fields`.

### Task 3: Rewrite `act` metadata explanation and trajectory/help sections

**Files:**
- Modify: `README.md`

**Step 1: Refresh `act` vs metadata subsection**

Clarify:
- `actions list` = high-level descriptors
- `capabilities.action_commands.items[*]` = executable command schema

**Step 2: Refresh trajectory section language for consistency**

Keep command examples but align wording with the new structured command guide style.

**Step 3: Update output/help block**

Include `showall`/`set` help discoverability examples.

### Task 4: Verify docs consistency and CLI-help alignment

**Files:**
- Verify: `README.md`

**Step 1: Run docs tests**

Run: `python -m pytest tests/test_docs_parameters_manifest.py tests/test_release_docs.py -q`

Expected: PASS

**Step 2: Run CLI help smoke checks**

Run:
- `python -m nanonis_qcodes_controller.cli --help`
- `python -m nanonis_qcodes_controller.cli set --help`
- `python -m nanonis_qcodes_controller.cli act --help`

Expected: all commands print help successfully.
