# Policy Set Command and Live Defaults Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `nqctl policy set` to update `safety.allow_writes` and `safety.dry_run`, and switch shipped defaults to live mode.

**Architecture:** Extend CLI policy subcommands with a write handler that updates runtime YAML safely and returns the effective settings payload. Keep `load_settings` precedence unchanged while flipping fallback defaults in `SafetySettings` and packaged default runtime config. Add tests for parser, handler behavior, and defaults.

**Tech Stack:** Python, argparse, PyYAML, pytest.

---

### Task 1: Add failing CLI tests for `policy set`

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `nanonis_qcodes_controller/cli.py`

**Step 1: Write failing parser test for `policy set` flags**

Add test asserting this parses:

```python
args = parser.parse_args(["policy", "set", "--allow-writes", "true", "--dry-run", "false"])
assert args.command == "policy"
assert args.policy_command == "set"
```

**Step 2: Write failing handler test for YAML mutation**

Use `tmp_path` runtime YAML and assert `_cmd_policy_set` writes:
- `safety.allow_writes = True`
- `safety.dry_run = False`

Also assert payload contains updated effective values.

**Step 3: Run focused tests to capture failures**

Run: `python -m pytest tests/test_cli.py -k "policy_set" --import-mode=importlib`

Expected: failing tests because command/handler is missing.

### Task 2: Implement `nqctl policy set`

**Files:**
- Modify: `nanonis_qcodes_controller/cli.py`

**Step 1: Add parser surface**

Under `policy` subparsers add:
- `policy set`
- `--allow-writes <bool>`
- `--dry-run <bool>`
- `--config-file` (same semantics as `policy show`)

**Step 2: Add bool parsing helper**

Add helper accepting string booleans (`1/0`, `true/false`, `yes/no`, `on/off`) with clear error on invalid input.

**Step 3: Implement `_cmd_policy_set`**

Behavior:
- Require at least one of `--allow-writes` or `--dry-run`.
- Load target YAML map (or initialize default structure if missing).
- Update only `safety.allow_writes` and/or `safety.dry_run`.
- Persist YAML.
- Reload via `load_settings(config_file=...)` and print payload with effective values.

**Step 4: Re-run focused tests**

Run: `python -m pytest tests/test_cli.py -k "policy_set" --import-mode=importlib`

Expected: PASS.

### Task 3: Add failing defaults tests for live mode

**Files:**
- Modify: `tests/test_default_file_resolution.py`
- Modify: `tests/test_smoke.py`
- Modify: `nanonis_qcodes_controller/config/settings.py`
- Modify: `nanonis_qcodes_controller/resources/config/default_runtime.yaml`

**Step 1: Update/add failing tests for fallback defaults**

Assert `SafetySettings()` default values are:
- `allow_writes is True`
- `dry_run is False`

**Step 2: Update/add failing tests for packaged YAML defaults**

Assert `config/default_runtime.yaml` resolves to:
- `safety.allow_writes == True`
- `safety.dry_run == False`

**Step 3: Run focused defaults tests (expected fail)**

Run: `python -m pytest tests/test_default_file_resolution.py tests/test_smoke.py --import-mode=importlib`

### Task 4: Implement default policy flip

**Files:**
- Modify: `nanonis_qcodes_controller/config/settings.py`
- Modify: `nanonis_qcodes_controller/resources/config/default_runtime.yaml`

**Step 1: Flip code fallback defaults**

Set:
- `SafetySettings.allow_writes = True`
- `SafetySettings.dry_run = False`

**Step 2: Flip packaged runtime defaults**

Set:
- `safety.allow_writes: true`
- `safety.dry_run: false`

**Step 3: Re-run focused defaults tests**

Run: `python -m pytest tests/test_default_file_resolution.py tests/test_smoke.py --import-mode=importlib`

Expected: PASS.

### Task 5: Update README policy usage

**Files:**
- Modify: `README.md`

**Step 1: Add `policy set` usage examples**

Document:
- `nqctl policy show`
- `nqctl policy set --allow-writes true --dry-run false`

**Step 2: Update defaults statement**

Change safety defaults sentence to reflect:
- `allow_writes=true`
- `dry_run=false`

**Step 3: Run release-doc checks**

Run: `python -m pytest tests/test_release_docs.py --import-mode=importlib`

### Task 6: Verify, commit, and publish next release

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `nanonis_qcodes_controller/version.py`
- Modify: `pyproject.toml`

**Step 1: Run verification commands**

Run:
- `python -m pytest tests/test_cli.py tests/test_default_file_resolution.py tests/test_smoke.py tests/test_release_docs.py --import-mode=importlib`

**Step 2: Commit feature changes**

Run:

```bash
git add nanonis_qcodes_controller/cli.py tests/test_cli.py nanonis_qcodes_controller/config/settings.py nanonis_qcodes_controller/resources/config/default_runtime.yaml tests/test_default_file_resolution.py tests/test_smoke.py README.md
git commit -m "Add policy set command and live runtime defaults"
```

**Step 3: Bump release version**

Set next version to `0.1.10` in:
- `nanonis_qcodes_controller/version.py`
- `pyproject.toml`
- `CHANGELOG.md`

Commit:

```bash
git add CHANGELOG.md nanonis_qcodes_controller/version.py pyproject.toml
git commit -m "release: bump version to 0.1.10"
```

**Step 4: Tag and publish artifacts**

Run:
- `git tag v0.1.10`
- `git push origin main`
- `git push origin v0.1.10`
- `python -m build`
- `gh release create v0.1.10 --title "v0.1.10" --notes "..."`
- `gh release upload v0.1.10 dist/nanonis_qcodes_controller-0.1.10-py3-none-any.whl dist/nanonis_qcodes_controller-0.1.10.tar.gz`
