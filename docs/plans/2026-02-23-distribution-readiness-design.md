# Distribution and Release Readiness Design

Date: 2026-02-23

## Objective
Define a practical, low-risk distribution strategy that ships the project through a private package index first, with clear release gates and a narrow stable API contract for v1.

## Scope

### In scope
- Private-index-first distribution workflow for Python package artifacts.
- v1 support contract for CLI and selected Python API symbols.
- Packaging/runtime requirements so installed usage works outside repo checkout.
- Pre-publish verification gates and post-publish smoke checks.
- Release documentation and rollback requirements.

### Out of scope
- Public PyPI release process.
- API expansion beyond selected stable v1 Python surface.
- Major CLI contract redesign.

## Approaches Considered

1. **Private package index (selected)**
   - Pros: controlled rollout, strong reproducibility/auditability, straightforward promotion flow.
   - Cons: requires internal index credentials and CI publish plumbing.

2. **Git tag installs (`pip install git+...@tag`)**
   - Pros: lowest initial setup overhead.
   - Cons: weaker artifact governance, less predictable dependency resolution, harder staged promotion.

3. **Container-first distribution**
   - Pros: strongest runtime consistency.
   - Cons: higher operational overhead and weaker fit for Python import workflows.

## Confirmed Decisions
- Primary release channel is a private package index.
- v1 stable Python API surface is intentionally narrow:
  - `nanonis_qcodes_controller.qcodes_driver.QcodesNanonisSTM`
  - `nanonis_qcodes_controller.client.create_client`
  - `nanonis_qcodes_controller.config.load_settings`
- CLI remains a stable contract for documented `nqctl` behavior.
- Other Python imports remain provisional/internal for now.

## Support Contract (v1)

### Stable
- `nqctl` documented command behavior and output format (JSON/text as documented).
- The three Python API symbols listed above.

### Provisional/Internal
- Other symbols exported from `client`, `safety`, `trajectory`, and manifest internals.
- Behavior may change in minor/patch updates while the package is in `0.x`.

### Versioning
- Continue on `0.x`, but use SemVer discipline:
  - no silent breaking changes in patch releases,
  - all behavior breaks explicitly called out in release notes.

## Packaging and Runtime Design
- Publish both `sdist` and `wheel` artifacts; prefer wheel for normal installs.
- Package built-in defaults with the distribution:
  - `config/parameters.yaml`
  - `config/default_runtime.yaml`
  - `config/default_trajectory_monitor.yaml`
- Runtime resolution order:
  1. built-in packaged defaults,
  2. explicit user overrides (CLI args, env vars, config paths).
- Runtime-generated artifacts must be written to user/workspace locations, never into site-packages.

## Release Workflow (Private Index First)
1. Prepare release from `main`:
   - bump version,
   - add release notes.
2. Build artifacts deterministically with `python -m build`.
3. Run required CI gates:
   - `ruff check .`
   - `black --check .`
   - `mypy nanonis_qcodes_controller`
   - `pytest`
4. Publish artifacts to private index via CI secrets (no local manual credentialed publish).
5. Perform fresh-venv smoke tests from private index install:
   - CLI smoke (`nqctl capabilities`, representative read path),
   - import smoke for the 3 stable Python APIs.
6. Promote the exact same artifact between channels/stages (no rebuild during promotion).

## Distribution Readiness Gate
Before each distribution, all must pass:
- Installed-path correctness outside repo checkout.
- Compatibility smoke coverage for the stable CLI and stable Python API surface.
- Error contract validation (invalid input, connection failures, policy blocks, command unavailable).
- Safety defaults verified on fresh install (`allow_writes=false`, `dry_run=true`).
- Release notes complete (changes, migration notes, supported Python versions, install instructions).
- Rollback tested to prior version without config/artifact corruption.

## Risks and Mitigations
- **Risk:** runtime defaults tied to cwd/repo layout.
  - **Mitigation:** load defaults from packaged resources.
- **Risk:** accidental expansion of "supported" Python surface.
  - **Mitigation:** document stable API set explicitly and add import-contract tests.
- **Risk:** release drift across environments.
  - **Mitigation:** publish once, promote immutable artifact.

## Implementation Handoff
Next step is a concrete implementation plan covering:
- package-data/resource loading updates,
- CI release pipeline setup,
- API-compat and smoke tests,
- release documentation templates/checklists.
