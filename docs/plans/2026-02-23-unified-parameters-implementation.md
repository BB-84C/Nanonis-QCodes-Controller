# Unified Parameters Manifest Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace split parameter manifests and scaffold flow with one deterministic `config/parameters.yaml` generated from `nanonis_spm.Nanonis`, while preserving curated writable definitions and allowing nullable per-channel safety limits.

**Architecture:** Implement a generator pipeline that introspects `Nanonis` Get/Set methods, extracts prose descriptions from docstrings, infers setter value/selectors, and emits a stable YAML manifest. Then simplify runtime loading to a single manifest path, make safety-limit checks nullable-aware in policy, and remove extra/scaffold CLI and merge scaffolding.

**Tech Stack:** Python 3.10+, argparse, inspect, PyYAML, existing QCodes driver and safety policy modules, pytest/ruff/black/mypy.

---

### Task 1: Add generator core with docstring and setter-arg inference

**Files:**
- Create: `nanonis_qcodes_controller/qcodes_driver/manifest_generator.py`
- Create: `tests/test_parameter_manifest_generator.py`

**Step 1: Write the failing test**

```python
def test_extract_description_drops_arguments_and_returns() -> None:
    doc = """LockIn.ModAmpGet
Returns the modulation amplitude of the specified Lock-In modulator.
Arguments:
-- Modulator number (int)
Return arguments:
-- Amplitude (float32)
"""
    assert extract_description(doc) == (
        "Returns the modulation amplitude of the specified Lock-In modulator."
    )


def test_infer_set_mapping_uses_value_arg_and_selector_args() -> None:
    args = ["Modulator_number", "Amplitude_"]
    arg_docs = {
        "Modulator_number": "Modulator number (int)",
        "Amplitude_": "Amplitude (float32)",
    }
    mapping = infer_set_mapping(args=args, arg_docs=arg_docs)
    assert mapping.value_arg == "Amplitude_"
    assert mapping.fixed_args == {"Modulator_number": 1}
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_parameter_manifest_generator.py`
Expected: FAIL (`manifest_generator` helpers missing)

**Step 3: Write minimal implementation**

```python
@dataclass(frozen=True)
class InferredSetMapping:
    value_arg: str
    fixed_args: dict[str, Any]


def extract_description(doc: str | None) -> str:
    # Keep prose only; cut before "Arguments:" / "Return arguments"
    ...


def infer_set_mapping(*, args: Sequence[str], arg_docs: Mapping[str, str]) -> InferredSetMapping:
    # Prefer value-like float/bool/string arg based on doc text
    # Keep selector/index/number args in fixed_args (default 1)
    ...
```

Also include:
- command discovery helper (`discover_nanonis_methods`)
- argument doc parser (`parse_argument_doc_lines`)
- stable parameter name normalizer

**Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_parameter_manifest_generator.py`
Expected: PASS

**Step 5: Commit**

```bash
git add nanonis_qcodes_controller/qcodes_driver/manifest_generator.py tests/test_parameter_manifest_generator.py
git commit -m "feat: add Nanonis manifest generation core utilities"
```

### Task 2: Add full-manifest generator script and metadata

**Files:**
- Create: `scripts/generate_parameters_manifest.py`
- Modify: `nanonis_qcodes_controller/qcodes_driver/manifest_generator.py`
- Test: `tests/test_parameter_manifest_generator.py`

**Step 1: Write the failing test**

```python
def test_build_manifest_merges_get_set_pairs_and_emits_meta_counts() -> None:
    manifest = build_manifest_from_commands(fake_commands)
    assert "parameters" in manifest
    assert "meta" in manifest
    assert manifest["meta"]["parameters_emitted"] >= 1
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_parameter_manifest_generator.py::test_build_manifest_merges_get_set_pairs_and_emits_meta_counts`
Expected: FAIL (`build_manifest_from_commands` missing)

**Step 3: Write minimal implementation**

```python
def build_manifest_from_nanonis(*, curated: Mapping[str, Any]) -> dict[str, Any]:
    # Discover methods, merge Get/Set siblings, apply curated overlay, return stable dict
    ...


def main() -> int:
    # argparse: --output config/parameters.yaml --curated <path> --overwrite
    # write YAML sort_keys=False
    ...
```

Include generated `meta` fields:
- `generated_by`
- `generated_at_utc`
- `source_package`
- `methods_seen`
- `get_set_commands_imported`
- `parameters_emitted`
- `with_description_count`
- `writable_count`

**Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_parameter_manifest_generator.py`
Expected: PASS

**Step 5: Commit**

```bash
git add scripts/generate_parameters_manifest.py nanonis_qcodes_controller/qcodes_driver/manifest_generator.py tests/test_parameter_manifest_generator.py
git commit -m "feat: add deterministic unified parameters manifest generator"
```

### Task 3: Generate and adopt `config/parameters.yaml`

**Files:**
- Create: `config/parameters.yaml`
- Delete: `config/default_parameters.yaml`
- Delete: `config/extra_parameters.yaml`

**Step 1: Write the failing test**

```python
def test_default_parameters_file_path_points_to_unified_manifest() -> None:
    from nanonis_qcodes_controller.qcodes_driver.extensions import DEFAULT_PARAMETERS_FILE
    assert str(DEFAULT_PARAMETERS_FILE).endswith("config/parameters.yaml")
```

Add this assertion in `tests/test_qcodes_extensions.py`.

**Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_qcodes_extensions.py::test_default_parameters_file_path_points_to_unified_manifest`
Expected: FAIL (constant still points to old file)

**Step 3: Write minimal implementation**

Run generator:

```bash
python scripts/generate_parameters_manifest.py --output config/parameters.yaml
```

Then remove old split manifests.

**Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_qcodes_extensions.py::test_default_parameters_file_path_points_to_unified_manifest`
Expected: PASS

**Step 5: Commit**

```bash
git add config/parameters.yaml tests/test_qcodes_extensions.py
git rm config/default_parameters.yaml config/extra_parameters.yaml
git commit -m "chore: adopt unified parameters manifest and remove split files"
```

### Task 4: Update parameter loader for single-manifest and nullable safety

**Files:**
- Modify: `nanonis_qcodes_controller/qcodes_driver/extensions.py`
- Modify: `tests/test_qcodes_extensions.py`
- Modify: `nanonis_qcodes_controller/qcodes_driver/__init__.py`

**Step 1: Write the failing test**

```python
def test_load_parameter_specs_accepts_nullable_safety_fields(tmp_path: Path) -> None:
    path = tmp_path / "params.yaml"
    path.write_text(
        """
version: 1
parameters:
  lockin_mod_amplitude_v:
    type: float
    get_cmd: {command: LockIn_ModAmpGet, payload_index: 0, args: {Modulator_number: 1}}
    set_cmd: {command: LockIn_ModAmpSet, value_arg: Amplitude_, args: {Modulator_number: 1}}
    safety: {min: null, max: null, max_step: null, max_slew_per_s: null, cooldown_s: null, ramp_enabled: true, ramp_interval_s: null}
""",
        encoding="utf-8",
    )
    specs = load_parameter_specs(path)
    assert specs[0].safety is not None
    assert specs[0].safety.max_step is None
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_qcodes_extensions.py::test_load_parameter_specs_accepts_nullable_safety_fields`
Expected: FAIL (loader currently requires numeric safety for writable)

**Step 3: Write minimal implementation**

```python
@dataclass(frozen=True)
class SafetySpec:
    min_value: float | None
    max_value: float | None
    max_step: float | None
    max_slew_per_s: float | None = None
    cooldown_s: float | None = None
    ramp_enabled: bool = True
    ramp_interval_s: float | None = None
```

Adjust parser validation to enforce numeric checks only when field is non-null.

Add optional `description: str` to `ParameterSpec` and parse it from YAML.

**Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_qcodes_extensions.py`
Expected: PASS

**Step 5: Commit**

```bash
git add nanonis_qcodes_controller/qcodes_driver/extensions.py nanonis_qcodes_controller/qcodes_driver/__init__.py tests/test_qcodes_extensions.py
git commit -m "feat: support unified manifest schema with nullable safety and descriptions"
```

### Task 5: Update policy engine for nullable per-channel limits

**Files:**
- Modify: `nanonis_qcodes_controller/safety/policy.py`
- Modify: `tests/test_safety_policy.py`

**Step 1: Write the failing test**

```python
def test_single_step_allows_channel_with_nullable_limits() -> None:
    policy = WritePolicy(
        allow_writes=True,
        dry_run=True,
        limits={
            "lockin_mod_amplitude_v": ChannelLimit(
                min_value=None,
                max_value=None,
                max_step=None,
                max_slew_per_s=None,
                cooldown_s=None,
                ramp_interval_s=0.05,
            )
        },
    )
    plan = policy.plan_scalar_write_single_step(
        channel="lockin_mod_amplitude_v", current_value=0.0, target_value=3.0
    )
    assert plan.steps == (3.0,)
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_safety_policy.py::test_single_step_allows_channel_with_nullable_limits`
Expected: FAIL (bound/step checks assume numeric values)

**Step 3: Write minimal implementation**

```python
if limit.min_value is not None and target < limit.min_value:
    raise PolicyViolation(...)
if limit.max_value is not None and target > limit.max_value:
    raise PolicyViolation(...)
if limit.max_step is not None and abs(delta) > limit.max_step:
    raise PolicyViolation(...)
```

Also treat `cooldown_s is None` as disabled cooldown.

**Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_safety_policy.py`
Expected: PASS

**Step 5: Commit**

```bash
git add nanonis_qcodes_controller/safety/policy.py tests/test_safety_policy.py
git commit -m "feat: allow nullable channel limits in write policy"
```

### Task 6: Remove extra/scaffold runtime paths from driver and CLI

**Files:**
- Modify: `nanonis_qcodes_controller/qcodes_driver/instrument.py`
- Modify: `nanonis_qcodes_controller/cli.py`
- Modify: `tests/test_qcodes_driver.py`
- Modify: `tests/test_cli.py`

**Step 1: Write the failing test**

```python
def test_cli_parameters_group_does_not_include_scaffold_command() -> None:
    parser = cli._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["parameters", "scaffold"])
```

```python
def test_driver_constructor_no_extra_parameters_argument(tmp_path: Path) -> None:
    sig = inspect.signature(QcodesNanonisSTM.__init__)
    assert "extra_parameters_file" not in sig.parameters
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_cli.py tests/test_qcodes_driver.py`
Expected: FAIL (scaffold and extra arg still present)

**Step 3: Write minimal implementation**

```python
def _add_runtime_args(...):
    parser.add_argument("--parameters-file", default=str(DEFAULT_PARAMETERS_FILE), ...)
    # remove --extra-parameters-file
```

Remove from CLI:
- `parameters scaffold` parser branch
- `_cmd_parameters_scaffold`
- `_build_parameter_scaffold`
- `_resolve_extra_parameters_file`
- scaffold action descriptor entry

Update instrument init to use one parameters file load path.

**Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_cli.py tests/test_qcodes_driver.py`
Expected: PASS

**Step 5: Commit**

```bash
git add nanonis_qcodes_controller/cli.py nanonis_qcodes_controller/qcodes_driver/instrument.py tests/test_cli.py tests/test_qcodes_driver.py
git commit -m "refactor: remove extra/scaffold parameter paths and use unified manifest"
```

### Task 7: Update docs and remove old scaffold references

**Files:**
- Modify: `README.md`
- Modify: `docs/cli_contract.md`
- Modify: `docs/extension_workflow.md`
- Modify: `docs/quickstart_simulator.md`
- Modify: `docs/architecture.md`
- Modify: `PLAN.md`
- Delete or repurpose: `scripts/scaffold_extension_manifest.py`

**Step 1: Write the failing test**

```python
def test_docs_reference_unified_parameters_manifest() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "config/parameters.yaml" in readme
    assert "extra_parameters.yaml" not in readme
```

Add to new file: `tests/test_docs_parameters_manifest.py`.

**Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_docs_parameters_manifest.py`
Expected: FAIL (docs still mention old files/commands)

**Step 3: Write minimal implementation**

Document new workflow:
- one manifest in `config/parameters.yaml`
- generator command usage
- no `parameters scaffold`
- no `extra_parameters.yaml`

**Step 4: Run test to verify it passes**

Run: `python -m pytest -q tests/test_docs_parameters_manifest.py`
Expected: PASS

**Step 5: Commit**

```bash
git add README.md docs/cli_contract.md docs/extension_workflow.md docs/quickstart_simulator.md docs/architecture.md PLAN.md tests/test_docs_parameters_manifest.py
git rm scripts/scaffold_extension_manifest.py
git commit -m "docs: migrate guidance to unified parameters manifest workflow"
```

### Task 8: End-to-end verification and regen idempotence

**Files:**
- Modify: `tests/test_parameter_manifest_generator.py` (idempotence assertion)
- Modify: `config/parameters.yaml` (only if regeneration changes)

**Step 1: Write the failing test**

```python
def test_generator_output_is_stable_for_same_input(tmp_path: Path) -> None:
    first = build_manifest_from_commands(fake_commands)
    second = build_manifest_from_commands(fake_commands)
    assert first == second
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest -q tests/test_parameter_manifest_generator.py::test_generator_output_is_stable_for_same_input`
Expected: FAIL if ordering/meta normalization is unstable

**Step 3: Write minimal implementation**

Ensure deterministic sorting and metadata normalization in generator output.

Re-generate manifest once:

```bash
python scripts/generate_parameters_manifest.py --output config/parameters.yaml
```

**Step 4: Run full verification**

Run:
- `python -m pytest -q`
- `ruff check .`
- `black --check .`
- `mypy nanonis_qcodes_controller`

Expected: all PASS

**Step 5: Commit**

```bash
git add .
git commit -m "feat: ship unified Nanonis parameters manifest with nullable safety support"
```

## Execution Notes
- Follow @superpowers:test-driven-development during code execution (test first per task).
- Before claiming completion, follow @superpowers:verification-before-completion.
- Use frequent commits shown above, unless user requests squashing strategy.
