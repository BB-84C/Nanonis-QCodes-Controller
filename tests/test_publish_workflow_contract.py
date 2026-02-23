from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _trigger_names(trigger_config: object) -> set[str]:
    if isinstance(trigger_config, str):
        return {trigger_config}
    if isinstance(trigger_config, list):
        return {value for value in trigger_config if isinstance(value, str)}
    if isinstance(trigger_config, dict):
        return {str(key) for key in trigger_config.keys()}
    return set()


def _jobs(workflow: dict[str, object]) -> dict[str, dict[str, Any]]:
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict), "workflow must define jobs"

    normalized: dict[str, dict[str, Any]] = {}
    for job_name, job_value in jobs.items():
        if isinstance(job_name, str) and isinstance(job_value, dict):
            normalized[job_name] = job_value
    return normalized


def _job_steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = job.get("steps")
    assert isinstance(steps, list), "job must define steps"
    return [step for step in steps if isinstance(step, dict)]


def _normalized_step_name(step: dict[str, Any]) -> str:
    name = step.get("name")
    if not isinstance(name, str):
        return ""
    return " ".join(name.lower().split())


def test_publish_private_workflow_contract() -> None:
    workflow_path = Path(".github/workflows/publish-private.yml")

    assert workflow_path.exists(), "publish-private workflow is required"

    workflow = yaml.load(workflow_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(workflow, dict)

    triggers = _trigger_names(workflow.get("on"))
    assert "workflow_dispatch" in triggers

    permissions = workflow.get("permissions")
    assert isinstance(permissions, dict), "workflow must define explicit permissions"
    assert permissions.get("actions") == "read"
    assert permissions.get("contents") == "read"

    jobs = _jobs(workflow)
    assert "publish" in jobs, "workflow must define publish job"
    assert "smoke" in jobs, "workflow must define smoke job"

    smoke_needs = jobs["smoke"].get("needs")
    if isinstance(smoke_needs, list):
        assert "publish" in smoke_needs
    else:
        assert smoke_needs == "publish"

    publish_steps = _job_steps(jobs["publish"])

    provenance_step_indexes = [
        index
        for index, step in enumerate(publish_steps)
        if "provenance" in _normalized_step_name(step)
        and "validate" in _normalized_step_name(step)
        and "build run" in _normalized_step_name(step)
    ]
    assert provenance_step_indexes, (
        "publish job must validate source build run provenance before download"
    )

    download_artifact_steps = [
        (index, step)
        for index, step in enumerate(publish_steps)
        if isinstance(step.get("uses"), str)
        and "actions/download-artifact" in str(step.get("uses"))
    ]
    assert download_artifact_steps, "workflow must download immutable dist artifacts"
    first_download_index, _ = download_artifact_steps[0]
    assert min(provenance_step_indexes) < first_download_index, (
        "provenance validation must happen before artifact download"
    )

    provenance_step = publish_steps[min(provenance_step_indexes)]
    provenance_run = provenance_step.get("run")
    assert isinstance(provenance_run, str), "provenance validation step must execute a script"
    assert 'payload.get("event")' in provenance_run
    assert '"push"' in provenance_run
    assert 'payload.get("head_branch")' in provenance_run
    assert '"main"' in provenance_run

    for _, step in download_artifact_steps:
        step_inputs = step.get("with")
        assert isinstance(step_inputs, dict), "download-artifact step must define inputs"
        assert step_inputs.get("run-id") == "${{ inputs.build_run_id }}", (
            "download-artifact run-id must be pinned to workflow_dispatch input"
        )

    artifact_version_step_indexes = [
        index
        for index, step in enumerate(publish_steps)
        if "artifact version" in _normalized_step_name(step)
        and "package_version" in str(step.get("run"))
    ]
    assert artifact_version_step_indexes, (
        "publish job must validate artifact version against package_version input"
    )

    twine_step_indexes = [
        index for index, step in enumerate(publish_steps) if "twine upload" in str(step.get("run"))
    ]
    assert twine_step_indexes, "publish job must upload via twine"
    assert min(artifact_version_step_indexes) < min(twine_step_indexes), (
        "artifact version validation must happen before twine upload"
    )

    run_commands = [
        run_value
        for step in publish_steps
        for run_value in [step.get("run")]
        if isinstance(run_value, str)
    ]
    assert not any("python -m build" in command for command in run_commands)
    assert any("twine upload" in command for command in run_commands)
