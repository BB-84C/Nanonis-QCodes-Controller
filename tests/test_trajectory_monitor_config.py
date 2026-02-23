from __future__ import annotations

import json
from dataclasses import replace

import pytest

from nanonis_qcodes_controller.trajectory.monitor_config import (
    MonitorConfig,
    clear_staged_run_name,
    default_monitor_config,
    load_monitor_defaults,
    load_staged_monitor_config,
    save_staged_monitor_config,
)


def test_require_runnable_enforces_run_name() -> None:
    config = default_monitor_config(run_name="")

    with pytest.raises(ValueError, match="run_name"):
        config.require_runnable()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"interval_s": 0.0}, "interval_s"),
        ({"interval_s": -0.1}, "interval_s"),
        ({"rotate_entries": 0}, "rotate_entries"),
        ({"action_window_s": -0.1}, "action_window_s"),
    ],
)
def test_validate_rejects_invalid_values(kwargs: dict[str, float | int], message: str) -> None:
    config = replace(default_monitor_config(run_name="run-1"), **kwargs)

    with pytest.raises(ValueError, match=message):
        config.validate()


def test_load_staged_monitor_config_returns_defaults_when_missing(tmp_path) -> None:
    path = tmp_path / "missing" / "monitor-config.json"

    loaded = load_staged_monitor_config(path=path)

    assert loaded == default_monitor_config(run_name="")
    assert "Tunnel Current" in loaded.signal_labels
    assert "Z Position" in loaded.signal_labels
    assert "Z Position" not in loaded.spec_labels
    assert "Z Setpoint" in loaded.spec_labels
    assert "Z Controller I Gain" in loaded.spec_labels


def test_save_and_load_staged_monitor_config_round_trip(tmp_path) -> None:
    path = tmp_path / "state" / "monitor-config.json"
    config = MonitorConfig(
        run_name="run-abc",
        interval_s=0.2,
        rotate_entries=123,
        action_window_s=3.25,
        db_directory="artifacts/custom",
        db_name="custom.sqlite3",
        signal_labels=("Signal A", "Signal B"),
        spec_labels=("Spec 1", "Spec 2"),
    )

    saved_path = save_staged_monitor_config(config, path=path)
    loaded = load_staged_monitor_config(path=path)

    assert saved_path == path
    assert loaded == config


def test_load_monitor_defaults_from_yaml(tmp_path) -> None:
    path = tmp_path / "trajectory-defaults.yaml"
    path.write_text(
        """
version: 1
defaults:
  interval_s: 0.25
  rotate_entries: 42
  action_window_s: 7.5
  db_directory: artifacts/custom-trajectory
  db_name: custom.sqlite3
  signal_labels:
    - Signal A
  spec_labels:
    - Spec A
    - Spec B
""".strip()
        + "\n",
        encoding="utf-8",
    )

    defaults = load_monitor_defaults(path=path)

    assert defaults.interval_s == 0.25
    assert defaults.rotate_entries == 42
    assert defaults.action_window_s == 7.5
    assert defaults.db_directory == "artifacts/custom-trajectory"
    assert defaults.db_name == "custom.sqlite3"
    assert defaults.signal_labels == ("Signal A",)
    assert defaults.spec_labels == ("Spec A", "Spec B")


def test_clear_staged_run_name_preserves_other_fields(tmp_path) -> None:
    path = tmp_path / "state" / "monitor-config.json"
    original = MonitorConfig(
        run_name="run-xyz",
        interval_s=0.5,
        rotate_entries=999,
        action_window_s=1.5,
        db_directory="artifacts/other",
        db_name="other.sqlite3",
        signal_labels=("Signal X",),
        spec_labels=("Spec X", "Spec Y"),
    )
    save_staged_monitor_config(original, path=path)

    cleared = clear_staged_run_name(path=path)
    reloaded = load_staged_monitor_config(path=path)

    assert cleared.run_name == ""
    assert cleared.interval_s == original.interval_s
    assert cleared.rotate_entries == original.rotate_entries
    assert cleared.action_window_s == original.action_window_s
    assert cleared.db_directory == original.db_directory
    assert cleared.db_name == original.db_name
    assert cleared.signal_labels == original.signal_labels
    assert cleared.spec_labels == original.spec_labels
    assert reloaded == cleared


def test_load_staged_monitor_config_treats_string_labels_as_single_entry(tmp_path) -> None:
    path = tmp_path / "state" / "monitor-config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "run_name": "run-abc",
                "signal_labels": "Signal A",
                "spec_labels": "Spec A",
            },
            handle,
            ensure_ascii=True,
            indent=2,
        )

    loaded = load_staged_monitor_config(path=path)

    assert loaded.signal_labels == ("Signal A",)
    assert loaded.spec_labels == ("Spec A",)


def test_load_staged_monitor_config_uses_defaults_for_null_string_fields(tmp_path) -> None:
    path = tmp_path / "state" / "monitor-config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "run_name": None,
                "db_directory": None,
                "db_name": None,
            },
            handle,
            ensure_ascii=True,
            indent=2,
        )

    loaded = load_staged_monitor_config(path=path)

    assert loaded.run_name == ""
    assert loaded.db_directory == "artifacts/trajectory"
    assert loaded.db_name == "trajectory-monitor.sqlite3"


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (None, 0.1),
        ("not-a-number", 0.1),
    ],
)
def test_load_staged_monitor_config_uses_default_interval_for_null_or_invalid_values(
    tmp_path, raw_value, expected: float
) -> None:
    path = tmp_path / "state" / "monitor-config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(
            {"run_name": "run-abc", "interval_s": raw_value}, handle, ensure_ascii=True, indent=2
        )

    loaded = load_staged_monitor_config(path=path)

    assert loaded.interval_s == expected


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (None, 6000),
        ("not-an-int", 6000),
    ],
)
def test_load_staged_monitor_config_uses_default_rotate_entries_for_null_or_invalid_values(
    tmp_path, raw_value, expected: int
) -> None:
    path = tmp_path / "state" / "monitor-config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(
            {"run_name": "run-abc", "rotate_entries": raw_value},
            handle,
            ensure_ascii=True,
            indent=2,
        )

    loaded = load_staged_monitor_config(path=path)

    assert loaded.rotate_entries == expected


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (None, 2.5),
        ("still-not-a-number", 2.5),
    ],
)
def test_load_staged_monitor_config_uses_default_action_window_for_null_or_invalid_values(
    tmp_path, raw_value, expected: float
) -> None:
    path = tmp_path / "state" / "monitor-config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(
            {"run_name": "run-abc", "action_window_s": raw_value},
            handle,
            ensure_ascii=True,
            indent=2,
        )

    loaded = load_staged_monitor_config(path=path)

    assert loaded.action_window_s == expected
