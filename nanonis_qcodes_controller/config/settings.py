from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .default_files import resolve_packaged_default

DEFAULT_RUNTIME_CONFIG_FILE = Path("config/default_runtime.yaml")
DEFAULT_PORTS = (3364, 6501, 6502, 6503, 6504)
DEFAULT_RAMP_INTERVAL_S = 0.05
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class NanonisConnectionSettings:
    host: str = "127.0.0.1"
    ports: tuple[int, ...] = DEFAULT_PORTS
    timeout_s: float = 2.0
    retry_count: int = 1
    backend: str = "adapter"


@dataclass(frozen=True)
class SafetySettings:
    allow_writes: bool = False
    dry_run: bool = True
    default_ramp_interval_s: float = DEFAULT_RAMP_INTERVAL_S


@dataclass(frozen=True)
class TrajectorySettings:
    enabled: bool = False
    directory: str = "artifacts/trajectory"
    queue_size: int = 2048
    max_events_per_file: int = 5000


@dataclass(frozen=True)
class RuntimeSettings:
    nanonis: NanonisConnectionSettings = field(default_factory=NanonisConnectionSettings)
    safety: SafetySettings = field(default_factory=SafetySettings)
    trajectory: TrajectorySettings = field(default_factory=TrajectorySettings)


def load_settings(
    config_file: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> RuntimeSettings:
    env_values = os.environ if env is None else env
    config_path, require_exists = _resolve_config_path(config_file=config_file, env=env_values)
    file_values = _load_config_mapping(config_path, require_exists=require_exists)

    defaults_connection = NanonisConnectionSettings()
    defaults_safety = SafetySettings()
    defaults_trajectory = TrajectorySettings()

    nanonis_file = _as_mapping(file_values.get("nanonis"))
    safety_file = _as_mapping(file_values.get("safety"))
    trajectory_file = _as_mapping(file_values.get("trajectory"))

    host_value = _first_set(
        env_values.get("NANONIS_HOST"),
        nanonis_file.get("host"),
        defaults_connection.host,
    )
    host = str(host_value).strip()
    if not host:
        raise ValueError("Nanonis host cannot be empty.")

    ports_value = _first_set(
        env_values.get("NANONIS_PORTS"),
        nanonis_file.get("ports"),
        defaults_connection.ports,
    )
    ports = _parse_ports(ports_value)

    timeout_value = _first_set(
        env_values.get("NANONIS_TIMEOUT_S"),
        nanonis_file.get("timeout_s"),
        defaults_connection.timeout_s,
    )
    timeout_s = _parse_positive_float(timeout_value, field_name="NANONIS_TIMEOUT_S")

    retry_value = _first_set(
        env_values.get("NANONIS_RETRY_COUNT"),
        nanonis_file.get("retry_count"),
        defaults_connection.retry_count,
    )
    retry_count = _parse_non_negative_int(retry_value, field_name="NANONIS_RETRY_COUNT")

    backend_value = _first_set(
        env_values.get("NANONIS_BACKEND"),
        nanonis_file.get("backend"),
        defaults_connection.backend,
    )
    backend = str(backend_value).strip()
    if not backend:
        raise ValueError("Nanonis backend cannot be empty.")

    allow_writes_value = _first_set(
        env_values.get("NANONIS_ALLOW_WRITES"),
        safety_file.get("allow_writes"),
        defaults_safety.allow_writes,
    )
    allow_writes = _parse_bool(allow_writes_value, field_name="NANONIS_ALLOW_WRITES")

    dry_run_value = _first_set(
        env_values.get("NANONIS_DRY_RUN"),
        safety_file.get("dry_run"),
        defaults_safety.dry_run,
    )
    dry_run = _parse_bool(dry_run_value, field_name="NANONIS_DRY_RUN")

    default_ramp_interval_value = _first_set(
        env_values.get("NANONIS_DEFAULT_RAMP_INTERVAL_S"),
        safety_file.get("default_ramp_interval_s"),
        defaults_safety.default_ramp_interval_s,
    )
    default_ramp_interval_s = _parse_positive_float(
        default_ramp_interval_value,
        field_name="NANONIS_DEFAULT_RAMP_INTERVAL_S",
    )

    trajectory_enabled_value = _first_set(
        env_values.get("NANONIS_TRAJECTORY_ENABLED"),
        trajectory_file.get("enabled"),
        defaults_trajectory.enabled,
    )
    trajectory_enabled = _parse_bool(
        trajectory_enabled_value,
        field_name="NANONIS_TRAJECTORY_ENABLED",
    )

    trajectory_directory_value = _first_set(
        env_values.get("NANONIS_TRAJECTORY_DIR"),
        trajectory_file.get("directory"),
        defaults_trajectory.directory,
    )
    trajectory_directory = str(trajectory_directory_value).strip()
    if not trajectory_directory:
        raise ValueError("Trajectory directory cannot be empty.")

    trajectory_queue_size_value = _first_set(
        env_values.get("NANONIS_TRAJECTORY_QUEUE_SIZE"),
        trajectory_file.get("queue_size"),
        defaults_trajectory.queue_size,
    )
    trajectory_queue_size = _parse_positive_int(
        trajectory_queue_size_value,
        field_name="NANONIS_TRAJECTORY_QUEUE_SIZE",
    )

    trajectory_max_events_value = _first_set(
        env_values.get("NANONIS_TRAJECTORY_MAX_EVENTS_PER_FILE"),
        trajectory_file.get("max_events_per_file"),
        defaults_trajectory.max_events_per_file,
    )
    trajectory_max_events_per_file = _parse_positive_int(
        trajectory_max_events_value,
        field_name="NANONIS_TRAJECTORY_MAX_EVENTS_PER_FILE",
    )

    return RuntimeSettings(
        nanonis=NanonisConnectionSettings(
            host=host,
            ports=ports,
            timeout_s=timeout_s,
            retry_count=retry_count,
            backend=backend,
        ),
        safety=SafetySettings(
            allow_writes=allow_writes,
            dry_run=dry_run,
            default_ramp_interval_s=default_ramp_interval_s,
        ),
        trajectory=TrajectorySettings(
            enabled=trajectory_enabled,
            directory=trajectory_directory,
            queue_size=trajectory_queue_size,
            max_events_per_file=trajectory_max_events_per_file,
        ),
    )


def _resolve_config_path(
    config_file: str | Path | None, env: Mapping[str, str]
) -> tuple[Path | None, bool]:
    if config_file is not None:
        return Path(config_file).expanduser(), True

    env_path = env.get("NANONIS_CONFIG_FILE")
    if env_path:
        return Path(env_path).expanduser(), True

    if DEFAULT_RUNTIME_CONFIG_FILE.exists():
        return DEFAULT_RUNTIME_CONFIG_FILE, False

    return resolve_packaged_default("default_runtime.yaml"), False


def _load_config_mapping(config_path: Path | None, *, require_exists: bool) -> dict[str, Any]:
    if config_path is None:
        return {}
    if not config_path.exists():
        if require_exists:
            raise ValueError(f"Runtime config file does not exist: {config_path}")
        return {}

    with config_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)

    if loaded is None:
        return {}

    if not isinstance(loaded, dict):
        raise ValueError("Runtime config file must contain a top-level mapping.")

    return dict(loaded)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("Config section must be a mapping.")
    return value


def _parse_ports(value: object) -> tuple[int, ...]:
    entries: Sequence[object]
    if isinstance(value, str):
        entries = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        entries = value
    else:
        raise ValueError("Ports must be a comma-separated string or sequence.")

    if not entries:
        raise ValueError("At least one candidate port must be provided.")

    parsed: list[int] = []
    for entry in entries:
        token = str(entry).strip()
        parsed.extend(_parse_port_token(token))

    return tuple(sorted(set(parsed)))


def _parse_port_token(token: str) -> list[int]:
    if "-" not in token:
        return [_validate_port(int(token))]

    left, right = token.split("-", maxsplit=1)
    start = _validate_port(int(left.strip()))
    end = _validate_port(int(right.strip()))
    if start > end:
        raise ValueError(f"Invalid TCP port range: {token}")

    return list(range(start, end + 1))


def _validate_port(port: int) -> int:
    if port < 1 or port > 65535:
        raise ValueError(f"Invalid TCP port: {port}")
    return port


def _parse_bool(value: object, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0

    normalized = str(value).strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False

    raise ValueError(f"Invalid boolean value for {field_name}: {value}")


def _parse_float(value: object, *, field_name: str) -> float:
    try:
        return float(str(value))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a float.") from exc


def _parse_positive_float(value: object, *, field_name: str) -> float:
    parsed = _parse_float(value, field_name=field_name)
    if parsed <= 0:
        raise ValueError(f"{field_name} must be positive.")
    return parsed


def _parse_non_negative_int(value: object, *, field_name: str) -> int:
    parsed = int(str(value))
    if parsed < 0:
        raise ValueError(f"{field_name} must be non-negative.")
    return parsed


def _parse_positive_int(value: object, *, field_name: str) -> int:
    parsed = int(str(value))
    if parsed <= 0:
        raise ValueError(f"{field_name} must be positive.")
    return parsed


def _first_set(*values: object) -> object:
    for value in values:
        if value is not None:
            return value
    raise ValueError("A default value is required.")
