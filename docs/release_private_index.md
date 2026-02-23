# Private Index Release Runbook

## Scope
Use this runbook for package releases to the private Python index. This project follows a private-index-first strategy.

Default release path: promote immutable `dist` artifacts produced by CI. Local build + publish is an exception-only fallback for maintainer recovery scenarios.

## 1) Required GitHub secrets

Configure these repository secrets before running publish:
- `PRIVATE_INDEX_REPOSITORY_URL`: Twine repository URL for private index upload.
- `PRIVATE_INDEX_USERNAME`: Twine username.
- `PRIVATE_INDEX_PASSWORD`: Twine password or token.
- `PRIVATE_INDEX_PIP_INDEX_URL`: Pip index URL used by post-publish smoke install.

## 2) Immutable artifact promotion flow (primary)
- Build and test in CI (`.github/workflows/ci.yml`) and produce `dist` artifact.
- Start `.github/workflows/publish-private.yml` via `workflow_dispatch`.
- Provide `build_run_id` for the already-built artifact and `package_version` for smoke install.
- Publish preflight validates `build_run_id` via GitHub API (`GITHUB_TOKEN`) and fails unless it is a successful `push` run of `.github/workflows/ci.yml` from the `main` branch in this repository.
- Publish preflight validates downloaded wheel/sdist metadata version equals `package_version` before upload.
- Publish workflow must only download and promote the existing `dist` artifact; do not rebuild during publish.
- Do not publish to public PyPI from this repository.

## 3) Smoke checks
After publish completes, validate install and basic CLI behavior from the private index:

```powershell
python -m venv .venv-smoke
.\.venv-smoke\Scripts\python -m pip install --index-url <private-index-url> nanonis-qcodes-controller==<version>
.\.venv-smoke\Scripts\nqctl capabilities
```

Confirm:
- Package resolves from the private index.
- `nqctl capabilities` runs successfully.

## Pre-release verification checklist
Run these commands before promoting or publishing release artifacts:

```powershell
ruff check .
black --check .
mypy nanonis_qcodes_controller
pytest
python -m build
```

## 4) Rollback
- If smoke checks fail, stop rollout and mark the release as blocked.
- Deprecate or remove the bad version in the private index per index policy.
- Bump version and republish with a fix; never overwrite an existing version.

## 5) Local fallback (maintainers only, exception path)
Use this path only when CI artifact promotion is unavailable and release owners explicitly approve a local recovery publish.

### Build local artifacts

```powershell
python -m pip install --upgrade build
python -m build
```

Expected output in `dist/`:
- wheel: `*.whl`
- source distribution: `*.tar.gz`

### Publish local artifacts
Use `twine upload` only with approved private-index credentials.

```powershell
python -m twine upload --repository-url <private-index-url> dist/*
```
