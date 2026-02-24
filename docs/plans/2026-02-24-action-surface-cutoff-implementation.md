# Action Surface Cutoff Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ensure manifest discovery excludes helper methods by importing only `nanonis_spm.Nanonis` callables declared at `Bias_Set` and after.

**Architecture:** Keep manifest schema and CLI behavior unchanged. Constrain discovery in `discover_nanonis_commands` using class declaration order and an explicit `Bias_Set` anchor, then keep name-sorted output for deterministic generated files. Add unit tests that lock this behavior and fail fast when anchor is missing.

**Tech Stack:** Python 3.11+, pytest, import monkeypatching, existing manifest generator.

---

### Task 1: Add failing discovery cutoff tests

**Files:**
- Modify: `tests/test_parameter_manifest_generator.py`
- Test: `tests/test_parameter_manifest_generator.py`

**Step 1: Write the failing test for pre-anchor exclusion**

```python
def test_discover_nanonis_commands_ignores_methods_before_bias_set(monkeypatch):
    class FakeNanonis:
        def quickSend(self):
            return None

        def decodeArray(self):
            return None

        def Bias_Set(self, Bias_value_V: float):  # noqa: N802
            del Bias_value_V
            return None

        def Bias_Get(self):  # noqa: N802
            return None

        def Scan_Action(self, Scan_action: int):  # noqa: N802
            del Scan_action
            return None

    class FakeModule:
        Nanonis = FakeNanonis

    monkeypatch.setattr(
        "nanonis_qcodes_controller.qcodes_driver.manifest_generator.importlib.import_module",
        lambda _name: FakeModule,
    )

    commands = discover_nanonis_commands()
    names = [item.command for item in commands]
    assert names == ["Bias_Get", "Bias_Set", "Scan_Action"]
```

**Step 2: Write the failing test for missing anchor error**

```python
def test_discover_nanonis_commands_requires_bias_set_anchor(monkeypatch):
    class FakeNanonis:
        def quickSend(self):
            return None

        def Scan_Action(self, Scan_action: int):  # noqa: N802
            del Scan_action
            return None

    class FakeModule:
        Nanonis = FakeNanonis

    monkeypatch.setattr(
        "nanonis_qcodes_controller.qcodes_driver.manifest_generator.importlib.import_module",
        lambda _name: FakeModule,
    )

    with pytest.raises(ValueError, match="Bias_Set"):
        discover_nanonis_commands()
```

**Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_parameter_manifest_generator.py -k discover_nanonis_commands -q`

Expected: FAIL because current discovery includes pre-anchor helper methods and does not enforce anchor presence.

**Step 4: Commit test-only change**

```bash
git add tests/test_parameter_manifest_generator.py
git commit -m "test: pin nanonis discovery anchor behavior"
```

### Task 2: Implement Bias_Set anchor cutoff in command discovery

**Files:**
- Modify: `nanonis_qcodes_controller/qcodes_driver/manifest_generator.py`
- Test: `tests/test_parameter_manifest_generator.py`

**Step 1: Add declaration-order callable collection and anchor slicing**

```python
ordered_members = [
    (name, member)
    for name, member in nanonis_spm.Nanonis.__dict__.items()
    if callable(member) and not name.startswith("_")
]

anchor_index = next(
    (index for index, (name, _member) in enumerate(ordered_members) if name == "Bias_Set"),
    None,
)
if anchor_index is None:
    raise ValueError("Could not find Bias_Set anchor in nanonis_spm.Nanonis callables.")

anchored_members = ordered_members[anchor_index:]
```

**Step 2: Build `CommandInfo` from anchored members and keep existing filters**

```python
for name, member in anchored_members:
    if compiled_pattern is not None and compiled_pattern.search(name) is None:
        continue
    signature = inspect.signature(member)
    ...
```

**Step 3: Keep stable alphabetical output contract**

```python
discovered.sort(key=lambda item: item.command)
```

**Step 4: Run focused tests to verify pass**

Run: `python -m pytest tests/test_parameter_manifest_generator.py -k discover_nanonis_commands -q`

Expected: PASS

**Step 5: Commit implementation change**

```bash
git add nanonis_qcodes_controller/qcodes_driver/manifest_generator.py tests/test_parameter_manifest_generator.py
git commit -m "fix: anchor nanonis command discovery at Bias_Set"
```

### Task 3: Document anchor-based discovery rule

**Files:**
- Modify: `docs/extension_workflow.md`

**Step 1: Add one note describing discovery boundary**

```markdown
- Command discovery is anchored at `Bias_Set`; callable members declared before
  that method are ignored to avoid importing internal helper methods.
```

**Step 2: Run markdown linting/checks used in repo (if configured)**

Run: `python -m pytest tests/test_parameter_manifest_generator.py -q`

Expected: PASS (docs-only change should not impact tests)

**Step 3: Commit docs update**

```bash
git add docs/extension_workflow.md
git commit -m "docs: document Bias_Set anchor for command discovery"
```

### Task 4: Final verification before release or tagging

**Files:**
- Verify: `nanonis_qcodes_controller/qcodes_driver/manifest_generator.py`
- Verify: `tests/test_parameter_manifest_generator.py`
- Verify: `docs/extension_workflow.md`

**Step 1: Run static checks**

Run: `ruff check .`

Expected: PASS

**Step 2: Run formatting check**

Run: `black --check .`

Expected: PASS

**Step 3: Run typing check**

Run: `mypy nanonis_qcodes_controller`

Expected: PASS

**Step 4: Run full tests**

Run: `python -m pytest $(git ls-files "tests/*.py")`

Expected: PASS

**Step 5: Commit verification-ready state**

```bash
git add nanonis_qcodes_controller/qcodes_driver/manifest_generator.py tests/test_parameter_manifest_generator.py docs/extension_workflow.md
git commit -m "fix: restrict manifest discovery to Nanonis command surface"
```
