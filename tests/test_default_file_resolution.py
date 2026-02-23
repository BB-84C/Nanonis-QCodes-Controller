from __future__ import annotations

import pytest

from nanonis_qcodes_controller.config import default_files
from nanonis_qcodes_controller.config import settings as settings_module
from nanonis_qcodes_controller.config.default_files import resolve_packaged_default
from nanonis_qcodes_controller.config.settings import load_settings
from nanonis_qcodes_controller.qcodes_driver.extensions import (
    DEFAULT_PARAMETERS_FILE,
    load_parameter_specs,
)
from nanonis_qcodes_controller.trajectory.monitor_config import load_monitor_defaults


def test_resolve_packaged_default_returns_existing_path() -> None:
    resolved = resolve_packaged_default("parameters.yaml")

    assert resolved.is_file()


def test_resolve_packaged_default_uses_as_file_without_exiting_context(
    tmp_path, monkeypatch
) -> None:
    resource_file = tmp_path / "virtual.yaml"
    resource_file.write_text("key: value\n", encoding="utf-8")
    state = {"as_file_calls": 0, "context_exited": False}

    class FakeTraversable:
        def joinpath(self, *parts: str) -> FakeTraversable:
            assert parts in (("config",), ("virtual.yaml",))
            return self

        def __str__(self) -> str:
            return str(resource_file)

    class FakeAsFileContext:
        def __enter__(self):
            state["as_file_calls"] += 1
            return resource_file

        def __exit__(self, exc_type, exc, tb):
            state["context_exited"] = True
            resource_file.unlink(missing_ok=True)
            return False

    monkeypatch.setattr(default_files.resources, "files", lambda package: FakeTraversable())
    monkeypatch.setattr(default_files.resources, "as_file", lambda traversable: FakeAsFileContext())

    resolve_packaged_default("virtual.yaml")
    resolved = resolve_packaged_default("virtual.yaml")

    assert state["as_file_calls"] == 1
    assert state["context_exited"] is False
    assert resolved == resource_file
    assert resolved.is_file()


def test_load_settings_falls_back_to_packaged_runtime_defaults(tmp_path, monkeypatch) -> None:
    calls: list[str] = []
    real_resolver = settings_module.resolve_packaged_default

    def _tracking_resolver(name: str):
        calls.append(name)
        return real_resolver(name)

    monkeypatch.setattr(settings_module, "resolve_packaged_default", _tracking_resolver)
    monkeypatch.chdir(tmp_path)

    settings = load_settings()

    assert calls == ["default_runtime.yaml"]
    assert settings.safety.allow_writes is False
    assert settings.safety.dry_run is True


def test_load_parameter_specs_falls_back_when_default_path_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    specs = load_parameter_specs(DEFAULT_PARAMETERS_FILE)

    assert len(specs) > 0


def test_load_monitor_defaults_falls_back_when_repo_defaults_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    defaults = load_monitor_defaults()

    assert defaults.interval_s > 0


def test_explicit_missing_paths_still_raise(tmp_path) -> None:
    missing = tmp_path / "missing.yaml"

    with pytest.raises(ValueError, match="does not exist"):
        load_settings(config_file=missing)

    with pytest.raises(ValueError, match="does not exist"):
        load_parameter_specs(missing)

    with pytest.raises(ValueError, match="does not exist"):
        load_monitor_defaults(path=missing)
