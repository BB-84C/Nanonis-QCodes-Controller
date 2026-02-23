from __future__ import annotations

from pathlib import Path


def test_readme_references_unified_parameters_manifest() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "config/parameters.yaml" in readme
    assert "config/default_parameters.yaml" not in readme
    assert "config/extra_parameters.yaml" not in readme
