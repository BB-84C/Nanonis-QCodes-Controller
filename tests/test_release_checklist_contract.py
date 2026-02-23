from __future__ import annotations

from pathlib import Path


def test_release_runbook_lists_required_verification_commands() -> None:
    release_doc = Path("docs/release_private_index.md").read_text(encoding="utf-8")

    required_commands = [
        "ruff check .",
        "black --check .",
        "mypy nanonis_qcodes_controller",
        "pytest",
        "python -m build",
    ]

    missing_commands = [command for command in required_commands if command not in release_doc]

    assert not missing_commands, (
        "Missing required verification commands in docs/release_private_index.md: "
        + ", ".join(missing_commands)
    )
