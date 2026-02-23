from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from nanonis_qcodes_controller.config.default_files import resolve_packaged_default

DEFAULT_MONITOR_DEFAULTS_FILE = Path("config/default_trajectory_monitor.yaml")


@dataclass(frozen=True)
class MonitorDefaults:
    interval_s: float
    rotate_entries: int
    action_window_s: float
    db_directory: str
    db_name: str
    signal_labels: tuple[str, ...]
    spec_labels: tuple[str, ...]


@dataclass(frozen=True)
class MonitorConfig:
    run_name: str
    interval_s: float
    rotate_entries: int
    action_window_s: float
    db_directory: str
    db_name: str
    signal_labels: tuple[str, ...]
    spec_labels: tuple[str, ...]

    def require_runnable(self) -> None:
        if not self.run_name.strip():
            raise ValueError("run_name must be non-empty before starting monitor run.")

    def validate(self) -> None:
        if self.interval_s <= 0:
            raise ValueError("interval_s must be positive.")
        if self.rotate_entries < 1:
            raise ValueError("rotate_entries must be at least 1.")
        if self.action_window_s < 0:
            raise ValueError("action_window_s must be non-negative.")


def default_monitor_defaults_path() -> Path:
    return DEFAULT_MONITOR_DEFAULTS_FILE


def default_staged_config_path() -> Path:
    return Path("artifacts") / "trajectory" / "monitor-config.json"


def load_monitor_defaults(path: Path | None = None) -> MonitorDefaults:
    if path is None:
        defaults_path = default_monitor_defaults_path()
        if not defaults_path.exists():
            defaults_path = resolve_packaged_default("default_trajectory_monitor.yaml")
    else:
        defaults_path = Path(path)
    if not defaults_path.exists():
        raise ValueError(f"Trajectory monitor defaults file does not exist: {defaults_path}")

    with defaults_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)

    root = _as_mapping(loaded, context="root")
    defaults = _as_mapping(root.get("defaults"), context="defaults")

    interval_s = _required_float(defaults, field_name="interval_s")
    rotate_entries = _required_int(defaults, field_name="rotate_entries")
    action_window_s = _required_float(defaults, field_name="action_window_s")
    db_directory = _required_string(defaults, field_name="db_directory")
    db_name = _required_string(defaults, field_name="db_name")
    signal_labels = _required_labels(defaults, field_name="signal_labels")
    spec_labels = _required_labels(defaults, field_name="spec_labels")

    if interval_s <= 0:
        raise ValueError("defaults.interval_s must be positive.")
    if rotate_entries < 1:
        raise ValueError("defaults.rotate_entries must be at least 1.")
    if action_window_s < 0:
        raise ValueError("defaults.action_window_s must be non-negative.")

    return MonitorDefaults(
        interval_s=interval_s,
        rotate_entries=rotate_entries,
        action_window_s=action_window_s,
        db_directory=db_directory,
        db_name=db_name,
        signal_labels=signal_labels,
        spec_labels=spec_labels,
    )


def default_monitor_config(
    *, run_name: str = "", defaults_path: Path | None = None
) -> MonitorConfig:
    defaults = load_monitor_defaults(path=defaults_path)
    return MonitorConfig(
        run_name=str(run_name),
        interval_s=defaults.interval_s,
        rotate_entries=defaults.rotate_entries,
        action_window_s=defaults.action_window_s,
        db_directory=defaults.db_directory,
        db_name=defaults.db_name,
        signal_labels=defaults.signal_labels,
        spec_labels=defaults.spec_labels,
    )


def load_staged_monitor_config(path: Path | None = None) -> MonitorConfig:
    defaults = load_monitor_defaults()
    config_path = default_staged_config_path() if path is None else Path(path)
    if not config_path.exists():
        return default_monitor_config(run_name="")

    with config_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    return _config_from_dict(raw, defaults=defaults)


def save_staged_monitor_config(config: MonitorConfig, path: Path | None = None) -> Path:
    config_path = default_staged_config_path() if path is None else Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "run_name": config.run_name,
        "interval_s": config.interval_s,
        "rotate_entries": config.rotate_entries,
        "action_window_s": config.action_window_s,
        "db_directory": config.db_directory,
        "db_name": config.db_name,
        "signal_labels": list(config.signal_labels),
        "spec_labels": list(config.spec_labels),
    }

    with config_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=2)

    return config_path


def clear_staged_run_name(path: Path | None = None) -> MonitorConfig:
    config = load_staged_monitor_config(path=path)
    updated = replace(config, run_name="")
    save_staged_monitor_config(updated, path=path)
    return updated


def _config_from_dict(raw: Any, *, defaults: MonitorDefaults) -> MonitorConfig:
    data = raw if isinstance(raw, dict) else {}
    return MonitorConfig(
        run_name=_coerce_string(data.get("run_name"), default=""),
        interval_s=_coerce_float(data.get("interval_s"), default=defaults.interval_s),
        rotate_entries=_coerce_int(data.get("rotate_entries"), default=defaults.rotate_entries),
        action_window_s=_coerce_float(
            data.get("action_window_s"), default=defaults.action_window_s
        ),
        db_directory=_coerce_string(data.get("db_directory"), default=defaults.db_directory),
        db_name=_coerce_string(data.get("db_name"), default=defaults.db_name),
        signal_labels=_coerce_labels(data.get("signal_labels"), default=defaults.signal_labels),
        spec_labels=_coerce_labels(data.get("spec_labels"), default=defaults.spec_labels),
    )


def _as_mapping(raw_value: Any, *, context: str) -> dict[str, Any]:
    if raw_value is None:
        return {}
    if not isinstance(raw_value, dict):
        raise ValueError(f"{context} must be a mapping.")
    return {str(key): value for key, value in raw_value.items()}


def _required_float(mapping: dict[str, Any], *, field_name: str) -> float:
    if field_name not in mapping:
        raise ValueError(f"defaults.{field_name} is required.")
    value = mapping[field_name]
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"defaults.{field_name} must be numeric.") from exc


def _required_int(mapping: dict[str, Any], *, field_name: str) -> int:
    if field_name not in mapping:
        raise ValueError(f"defaults.{field_name} is required.")
    value = mapping[field_name]
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"defaults.{field_name} must be an integer.") from exc


def _required_string(mapping: dict[str, Any], *, field_name: str) -> str:
    if field_name not in mapping:
        raise ValueError(f"defaults.{field_name} is required.")
    value = mapping[field_name]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"defaults.{field_name} must be a non-empty string.")
    return value


def _required_labels(mapping: dict[str, Any], *, field_name: str) -> tuple[str, ...]:
    if field_name not in mapping:
        raise ValueError(f"defaults.{field_name} is required.")
    value = mapping[field_name]
    if not isinstance(value, list):
        raise ValueError(f"defaults.{field_name} must be a list of labels.")
    labels = tuple(str(item).strip() for item in value if str(item).strip())
    if not labels:
        raise ValueError(f"defaults.{field_name} must contain at least one label.")
    return labels


def _coerce_string(value: Any, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return default


def _coerce_float(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_labels(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(label) for label in value)
    return default
