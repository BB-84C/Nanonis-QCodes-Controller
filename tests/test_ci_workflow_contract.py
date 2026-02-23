from __future__ import annotations

from pathlib import Path

import yaml


def _collect_steps(workflow: dict[str, object]) -> list[dict[str, object]]:
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    assert jobs, "Workflow must define at least one job"

    steps: list[dict[str, object]] = []
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        job_steps = job.get("steps")
        if not isinstance(job_steps, list):
            continue
        for step in job_steps:
            if isinstance(step, dict):
                steps.append(step)

    return steps


def test_ci_workflow_exists_and_includes_required_steps() -> None:
    workflow_path = Path(".github/workflows/ci.yml")

    assert workflow_path.exists()

    workflow = yaml.load(workflow_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(workflow, dict)

    trigger_config = workflow.get("on")
    if isinstance(trigger_config, str):
        triggers = {trigger_config}
    elif isinstance(trigger_config, list):
        triggers = {value for value in trigger_config if isinstance(value, str)}
    elif isinstance(trigger_config, dict):
        triggers = {str(value) for value in trigger_config.keys()}
    else:
        triggers = set()

    assert "push" in triggers
    assert "pull_request" in triggers

    steps = _collect_steps(workflow)

    run_commands: list[str] = []
    for step in steps:
        run_value = step.get("run")
        if isinstance(run_value, str):
            run_commands.append(run_value)
    required_checks = [
        "ruff",
        "black --check",
        "mypy nanonis_qcodes_controller",
        "pytest",
        "python -m build",
    ]
    for required_check in required_checks:
        assert any(required_check in command for command in run_commands)

    artifact_steps = [
        step
        for step in steps
        if isinstance(step.get("uses"), str) and "actions/upload-artifact" in str(step.get("uses"))
    ]
    assert artifact_steps, "Workflow must upload build artifacts"

    has_dist_artifact_path = False
    for step in artifact_steps:
        with_section = step.get("with")
        if not isinstance(with_section, dict):
            continue
        path_value = with_section.get("path")
        if isinstance(path_value, str) and "dist" in path_value:
            has_dist_artifact_path = True
            break

    assert has_dist_artifact_path, "Artifact upload step must reference dist output"
