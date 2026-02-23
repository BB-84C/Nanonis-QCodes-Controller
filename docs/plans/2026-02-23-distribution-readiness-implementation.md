# Distribution Readiness Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship a private-index-ready release process with packaged defaults, a stable v1 API contract, and repeatable verification gates.

**Architecture:** Keep runtime override behavior unchanged while moving built-in defaults to package resources so installs work outside repo checkout. Define a narrow stable Python API contract and enforce it with tests/docs. Add CI workflows for quality/build and private publishing with immutable artifact promotion.

**Tech Stack:** Python 3.10+, setuptools, pytest, ruff, black, mypy, GitHub Actions, private Python package index (devpi/Artifactory/GitHub Packages compatible).

---

### Task 1: Package Built-in Default YAML Files

**Files:**
- Create: `nanonis_qcodes_controller/resources/__init__.py`
- Create: `nanonis_qcodes_controller/resources/config/default_runtime.yaml`
- Create: `nanonis_qcodes_controller/resources/config/default_trajectory_monitor.yaml`
- Create: `nanonis_qcodes_controller/resources/config/parameters.yaml`
- Modify: `pyproject.toml`
- Test: `tests/test_packaged_defaults_resources.py`

**Step 1: Write the failing test**

```python
from importlib import resources


def test_packaged_default_files_exist() -> None:
    config_root = resources.files("nanonis_qcodes_controller.resources").joinpath("config")
    assert config_root.joinpath("default_runtime.yaml").is_file()
    assert config_root.joinpath("default_trajectory_monitor.yaml").is_file()
    assert config_root.joinpath("parameters.yaml").is_file()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_packaged_defaults_resources.py -v`
Expected: FAIL because resource package/files are missing.

**Step 3: Write minimal implementation**

```toml
[tool.setuptools.package-data]
nanonis_qcodes_controller = ["resources/config/*.yaml"]
```

Also add the `resources/config/*.yaml` files and a package marker `nanonis_qcodes_controller/resources/__init__.py`.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_packaged_defaults_resources.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add pyproject.toml nanonis_qcodes_controller/resources tests/test_packaged_defaults_resources.py
git commit -m "build: package default yaml resources"
```

### Task 2: Add Default File Resolver with Package Resource Fallback

**Files:**
- Create: `nanonis_qcodes_controller/config/default_files.py`
- Modify: `nanonis_qcodes_controller/config/settings.py`
- Modify: `nanonis_qcodes_controller/qcodes_driver/extensions.py`
- Modify: `nanonis_qcodes_controller/trajectory/monitor_config.py`
- Test: `tests/test_default_file_resolution.py`

**Step 1: Write the failing tests**

```python
from pathlib import Path

from nanonis_qcodes_controller.config.default_files import resolve_packaged_default


def test_resolve_packaged_parameters_file_returns_existing_path() -> None:
    path = resolve_packaged_default("parameters.yaml")
    assert isinstance(path, Path)
    assert path.exists()


def test_loader_uses_packaged_defaults_when_repo_config_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    # No ./config directory in cwd.
    from nanonis_qcodes_controller.config import load_settings

    settings = load_settings(config_file=None)
    assert settings.nanonis.host
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_default_file_resolution.py -v`
Expected: FAIL due missing resolver and cwd-coupled defaults.

**Step 3: Write minimal implementation**

```python
# nanonis_qcodes_controller/config/default_files.py
from importlib import resources
from pathlib import Path


def resolve_packaged_default(name: str) -> Path:
    candidate = resources.files("nanonis_qcodes_controller.resources").joinpath("config", name)
    return Path(str(candidate))
```

Then wire fallback usage into:
- `load_settings` default config resolution,
- `DEFAULT_PARAMETERS_FILE` resolution path in `qcodes_driver/extensions.py`,
- trajectory monitor defaults resolution in `trajectory/monitor_config.py`.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_default_file_resolution.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add nanonis_qcodes_controller/config/default_files.py nanonis_qcodes_controller/config/settings.py nanonis_qcodes_controller/qcodes_driver/extensions.py nanonis_qcodes_controller/trajectory/monitor_config.py tests/test_default_file_resolution.py
git commit -m "feat: load built-in defaults from package resources"
```

### Task 3: Lock v1 Stable Python API Contract with Tests

**Files:**
- Create: `tests/test_public_api_contract.py`
- Modify: `README.md`
- Modify: `docs/architecture.md`

**Step 1: Write the failing test**

```python
def test_stable_python_api_symbols_import() -> None:
    from nanonis_qcodes_controller.client import create_client
    from nanonis_qcodes_controller.config import load_settings
    from nanonis_qcodes_controller.qcodes_driver import QcodesNanonisSTM

    assert callable(create_client)
    assert callable(load_settings)
    assert QcodesNanonisSTM.__name__ == "QcodesNanonisSTM"
```

**Step 2: Run test to verify current behavior and contract visibility gap**

Run: `pytest tests/test_public_api_contract.py -v`
Expected: PASS/FAIL acceptable, but docs should still fail contract review (stable/provisional not explicitly documented).

**Step 3: Write minimal implementation**

Add a documented API support section to:
- `README.md`
- `docs/architecture.md`

Include explicit stable/provisional boundaries.

**Step 4: Run tests and doc checks**

Run:
- `pytest tests/test_public_api_contract.py -v`
- `pytest tests/test_docs_parameters_manifest.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_public_api_contract.py README.md docs/architecture.md
git commit -m "docs: define and test v1 stable python api contract"
```

### Task 4: Add Release Documentation and Changelog Baseline

**Files:**
- Create: `CHANGELOG.md`
- Create: `docs/release_private_index.md`
- Modify: `README.md`
- Test: `tests/test_release_docs.py`

**Step 1: Write the failing test**

```python
from pathlib import Path


def test_release_docs_exist_and_linked() -> None:
    assert Path("CHANGELOG.md").exists()
    assert Path("docs/release_private_index.md").exists()
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "docs/release_private_index.md" in readme
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_release_docs.py -v`
Expected: FAIL because files/link do not exist yet.

**Step 3: Write minimal implementation**

Create:
- `CHANGELOG.md` with Keep-a-Changelog style starter.
- `docs/release_private_index.md` with:
  - build commands,
  - publish commands/CI path,
  - smoke checks,
  - rollback procedure.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_release_docs.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add CHANGELOG.md docs/release_private_index.md README.md tests/test_release_docs.py
git commit -m "docs: add private index release runbook and changelog"
```

### Task 5: Add CI Quality + Build Workflow

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: `pyproject.toml` (if test/build extras need adjustment)

**Step 1: Write a failing workflow acceptance check (local script test)**

```python
from pathlib import Path


def test_ci_workflow_exists_with_required_jobs() -> None:
    workflow = Path(".github/workflows/ci.yml")
    text = workflow.read_text(encoding="utf-8")
    assert "ruff check ." in text
    assert "black --check ." in text
    assert "mypy nanonis_qcodes_controller" in text
    assert "pytest" in text
    assert "python -m build" in text
```

Place this in `tests/test_ci_workflow_contract.py`.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_ci_workflow_contract.py -v`
Expected: FAIL because workflow does not exist.

**Step 3: Write minimal implementation**

Create `.github/workflows/ci.yml` that runs on PR/push:
- setup Python,
- install `.[dev,qcodes,nanonis]`,
- run lint/type/tests,
- run `python -m build`,
- upload `dist/*` as artifacts.

**Step 4: Run tests/build locally**

Run:
- `pytest tests/test_ci_workflow_contract.py -v`
- `python -m build`

Expected: test PASS and build emits wheel/sdist under `dist/`.

**Step 5: Commit**

```bash
git add .github/workflows/ci.yml tests/test_ci_workflow_contract.py
git commit -m "ci: add quality and build workflow"
```

### Task 6: Add Private Publish Workflow with Immutable Promotion Inputs

**Files:**
- Create: `.github/workflows/publish-private.yml`
- Modify: `docs/release_private_index.md`

**Step 1: Write a failing workflow contract test**

```python
from pathlib import Path


def test_publish_workflow_uses_dist_artifacts_and_no_rebuild() -> None:
    text = Path(".github/workflows/publish-private.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in text
    assert "actions/download-artifact" in text
    assert "twine upload" in text
```

Put in `tests/test_publish_workflow_contract.py`.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_publish_workflow_contract.py -v`
Expected: FAIL because publish workflow does not exist.

**Step 3: Write minimal implementation**

Create `.github/workflows/publish-private.yml` with:
- manual trigger + tag trigger,
- artifact download from prior CI build,
- publish using private-index secrets,
- post-publish smoke install job in fresh venv.

Update `docs/release_private_index.md` with required secrets and promotion flow.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_publish_workflow_contract.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add .github/workflows/publish-private.yml docs/release_private_index.md tests/test_publish_workflow_contract.py
git commit -m "ci: add private index publish workflow"
```

### Task 7: Final Verification and Release-Candidate Checklist

**Files:**
- Modify: `docs/release_private_index.md`
- Modify: `CHANGELOG.md`

**Step 1: Add failing checklist test for required release commands**

Create `tests/test_release_checklist_contract.py`:

```python
from pathlib import Path


def test_release_runbook_lists_required_verification_commands() -> None:
    text = Path("docs/release_private_index.md").read_text(encoding="utf-8")
    for cmd in ["ruff check .", "black --check .", "mypy nanonis_qcodes_controller", "pytest", "python -m build"]:
        assert cmd in text
```

**Step 2: Run test to verify it fails/passes appropriately**

Run: `pytest tests/test_release_checklist_contract.py -v`
Expected: FAIL if any required command is missing.

**Step 3: Update docs/changelog minimally**

Ensure release runbook includes all gates and add an `Unreleased` section entry in `CHANGELOG.md` for this feature set.

**Step 4: Run full project verification**

Run:
- `ruff check .`
- `black --check .`
- `mypy nanonis_qcodes_controller`
- `pytest`
- `python -m build`

Expected: all PASS.

**Step 5: Commit**

```bash
git add docs/release_private_index.md CHANGELOG.md tests/test_release_checklist_contract.py
git commit -m "chore: finalize distribution readiness checklist"
```
