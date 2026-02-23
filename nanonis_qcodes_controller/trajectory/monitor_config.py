from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

DEFAULT_SIGNAL_LABELS: tuple[str, ...] = (
    "Z Position",
    "Tunnel Current",
)

DEFAULT_SPEC_LABELS: tuple[str, ...] = (
    "Bias",
    "Z Setpoint",
    "Z Controller Enabled",
    "Z Controller I Gain",
    "Scan Status Code",
    "Scan Frame Center X",
    "Scan Frame Center Y",
    "Scan Frame Width",
    "Scan Frame Height",
    "Scan Frame Angle",
)


@dataclass(frozen=True)
class MonitorConfig:
    run_name: str
    interval_s: float = 0.1
    rotate_entries: int = 6000
    action_window_s: float = 2.5
    db_directory: str = "artifacts/trajectory"
    db_name: str = "trajectory-monitor.sqlite3"
    signal_labels: tuple[str, ...] = DEFAULT_SIGNAL_LABELS
    spec_labels: tuple[str, ...] = DEFAULT_SPEC_LABELS

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


def default_staged_config_path() -> Path:
    return Path("artifacts") / "trajectory" / "monitor-config.json"


def load_staged_monitor_config(path: Path | None = None) -> MonitorConfig:
    config_path = default_staged_config_path() if path is None else Path(path)
    if not config_path.exists():
        return MonitorConfig(run_name="")

    with config_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    return _config_from_dict(raw)


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


def _config_from_dict(raw: Any) -> MonitorConfig:
    data = raw if isinstance(raw, dict) else {}
    return MonitorConfig(
        run_name=_coerce_string(data.get("run_name"), default=""),
        interval_s=_coerce_float(data.get("interval_s"), default=0.1),
        rotate_entries=_coerce_int(data.get("rotate_entries"), default=6000),
        action_window_s=_coerce_float(data.get("action_window_s"), default=2.5),
        db_directory=_coerce_string(data.get("db_directory"), default="artifacts/trajectory"),
        db_name=_coerce_string(data.get("db_name"), default="trajectory-monitor.sqlite3"),
        signal_labels=_coerce_labels(data.get("signal_labels"), default=DEFAULT_SIGNAL_LABELS),
        spec_labels=_coerce_labels(data.get("spec_labels"), default=DEFAULT_SPEC_LABELS),
    )


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
