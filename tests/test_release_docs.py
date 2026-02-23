from __future__ import annotations

from pathlib import Path


def test_release_documentation_exists_and_is_linked() -> None:
    changelog = Path("CHANGELOG.md")
    release_doc = Path("docs/release_private_index.md")
    readme = Path("README.md").read_text(encoding="utf-8")

    assert changelog.exists()
    assert release_doc.exists()
    assert "docs/release_private_index.md" in readme
