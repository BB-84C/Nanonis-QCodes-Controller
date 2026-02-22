from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_FILE = Path("config/default.yaml")
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
class ScalarLimitSettings:
    min: float
    max: float
    max_step: float
    max_slew_per_s: float | None = None
    cooldown_s: float = 0.0
    require_confirmation: bool = False
    ramp_interval_s: float = DEFAULT_RAMP_INTERVAL_S


@dataclass(frozen=True)
class SafetySettings:
    allow_writes: bool = False
    dry_run: bool = True
    default_ramp_interval_s: float = DEFAULT_RAMP_INTERVAL_S
    limits: Mapping[str, ScalarLimitSettings] = field(
        default_factory=lambda: _default_scalar_limits()
    )


@dataclass(frozen=True)
class TrajectorySettings:
    enabled: bool = False
    directory: str = "artifacts/trajectory"
    queue_size: int = 2048
    max_events_per_file: int = 5000


@dataclass(frozen=True)
class BridgeSettings:
    nanonis: NanonisConnectionSettings = field(default_factory=NanonisConnectionSettings)
    safety: SafetySettings = field(default_factory=SafetySettings)
    trajectory: TrajectorySettings = field(default_factory=TrajectorySettings)


def load_settings(
    config_file: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> BridgeSettings:
    env_values = os.environ if env is None else env
    config_path = _resolve_config_path(config_file=config_file, env=env_values)
    file_values = _load_config_mapping(config_path)

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

    limits = _parse_scalar_limits(
        safety_file.get("limits"),
        defaults=defaults_safety.limits,
        default_ramp_interval_s=default_ramp_interval_s,
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

    return BridgeSettings(
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
            limits=limits,
        ),
        trajectory=TrajectorySettings(
            enabled=trajectory_enabled,
            directory=trajectory_directory,
            queue_size=trajectory_queue_size,
            max_events_per_file=trajectory_max_events_per_file,
        ),
    )


def _default_scalar_limits() -> dict[str, ScalarLimitSettings]:
    return {
        "bias_v": ScalarLimitSettings(
            min=-5.0,
            max=5.0,
            max_step=0.05,
            ramp_interval_s=DEFAULT_RAMP_INTERVAL_S,
        ),
        "setpoint_a": ScalarLimitSettings(
            min=0.0,
            max=1.0e-6,
            max_step=5.0e-12,
            ramp_interval_s=DEFAULT_RAMP_INTERVAL_S,
        ),
        "scan_frame_center_x_m": ScalarLimitSettings(
            min=-1.0e-3,
            max=1.0e-3,
            max_step=1.0e-9,
            ramp_interval_s=DEFAULT_RAMP_INTERVAL_S,
        ),
        "scan_frame_center_y_m": ScalarLimitSettings(
            min=-1.0e-3,
            max=1.0e-3,
            max_step=1.0e-9,
            ramp_interval_s=DEFAULT_RAMP_INTERVAL_S,
        ),
        "scan_frame_width_m": ScalarLimitSettings(
            min=1.0e-12,
            max=1.0e-3,
            max_step=1.0e-9,
            ramp_interval_s=DEFAULT_RAMP_INTERVAL_S,
        ),
        "scan_frame_height_m": ScalarLimitSettings(
            min=1.0e-12,
            max=1.0e-3,
            max_step=1.0e-9,
            ramp_interval_s=DEFAULT_RAMP_INTERVAL_S,
        ),
        "scan_frame_angle_deg": ScalarLimitSettings(
            min=-180.0,
            max=180.0,
            max_step=1.0,
            ramp_interval_s=DEFAULT_RAMP_INTERVAL_S,
        ),
    }


def _parse_scalar_limits(
    value: object,
    *,
    defaults: Mapping[str, ScalarLimitSettings],
    default_ramp_interval_s: float,
) -> dict[str, ScalarLimitSettings]:
    if value is None:
        configured: Mapping[str, Any] = {}
    else:
        configured = _as_mapping(value)

    channels = sorted(set(defaults) | set(configured))
    parsed_limits: dict[str, ScalarLimitSettings] = {}

    for channel in channels:
        channel_config = _as_mapping(configured.get(channel))
        default_limit = defaults.get(channel)

        min_value = _parse_float(
            _first_set(channel_config.get("min"), default_limit.min if default_limit else None),
            field_name=f"safety.limits.{channel}.min",
        )
        max_value = _parse_float(
            _first_set(channel_config.get("max"), default_limit.max if default_limit else None),
            field_name=f"safety.limits.{channel}.max",
        )
        if max_value <= min_value:
            raise ValueError(
                f"safety.limits.{channel}: max ({max_value}) must be greater than min ({min_value})."
            )

        max_step = _parse_positive_float(
            _first_set(
                channel_config.get("max_step"),
                default_limit.max_step if default_limit else None,
            ),
            field_name=f"safety.limits.{channel}.max_step",
        )

        max_slew_raw = channel_config.get(
            "max_slew_per_s",
            default_limit.max_slew_per_s if default_limit else None,
        )
        if max_slew_raw is None:
            max_slew_per_s: float | None = None
        else:
            max_slew_per_s = _parse_positive_float(
                max_slew_raw,
                field_name=f"safety.limits.{channel}.max_slew_per_s",
            )

        cooldown_s = _parse_non_negative_float(
            channel_config.get("cooldown_s", default_limit.cooldown_s if default_limit else 0.0),
            field_name=f"safety.limits.{channel}.cooldown_s",
        )

        require_confirmation = _parse_bool(
            channel_config.get(
                "require_confirmation",
                default_limit.require_confirmation if default_limit else False,
            ),
            field_name=f"safety.limits.{channel}.require_confirmation",
        )

        ramp_interval_s = _parse_positive_float(
            channel_config.get(
                "ramp_interval_s",
                default_limit.ramp_interval_s if default_limit else default_ramp_interval_s,
            ),
            field_name=f"safety.limits.{channel}.ramp_interval_s",
        )

        parsed_limits[channel] = ScalarLimitSettings(
            min=min_value,
            max=max_value,
            max_step=max_step,
            max_slew_per_s=max_slew_per_s,
            cooldown_s=cooldown_s,
            require_confirmation=require_confirmation,
            ramp_interval_s=ramp_interval_s,
        )

    return parsed_limits


def _resolve_config_path(config_file: str | Path | None, env: Mapping[str, str]) -> Path | None:
    if config_file is not None:
        return Path(config_file).expanduser()

    env_path = env.get("NANONIS_CONFIG_FILE")
    if env_path:
        return Path(env_path).expanduser()

    if DEFAULT_CONFIG_FILE.exists():
        return DEFAULT_CONFIG_FILE

    return None


def _load_config_mapping(config_path: Path | None) -> dict[str, Any]:
    if config_path is None or not config_path.exists():
        return {}

    with config_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)

    if loaded is None:
        return {}

    if not isinstance(loaded, dict):
        raise ValueError("Config file must contain a top-level mapping.")

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


def _parse_non_negative_float(value: object, *, field_name: str) -> float:
    parsed = _parse_float(value, field_name=field_name)
    if parsed < 0:
        raise ValueError(f"{field_name} must be non-negative.")
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
