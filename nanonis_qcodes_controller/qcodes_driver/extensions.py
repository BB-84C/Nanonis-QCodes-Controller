from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from nanonis_qcodes_controller.config.default_files import resolve_packaged_default

DEFAULT_PARAMETERS_FILE = Path("config/parameters.yaml")

ScalarValueType = Literal["float", "int", "bool", "str"]
ValidatorKind = Literal["numbers", "ints", "bool", "enum", "none"]
ActionSafetyMode = Literal["alwaysAllowed", "guarded", "blocked"]

_ALLOWED_VALUE_TYPES: frozenset[str] = frozenset({"float", "int", "bool", "str"})
_ALLOWED_VALIDATOR_KINDS: frozenset[str] = frozenset({"numbers", "ints", "bool", "enum", "none"})
_ALLOWED_ACTION_SAFETY_MODES: frozenset[str] = frozenset({"alwaysAllowed", "guarded", "blocked"})


@dataclass(frozen=True)
class ResponseFieldSpec:
    index: int
    name: str
    type: str
    unit: str
    description: str


@dataclass(frozen=True)
class ArgFieldSpec:
    name: str
    type: str
    required: bool
    description: str


@dataclass(frozen=True)
class ReadCommandSpec:
    command: str
    payload_index: int = 0
    args: Mapping[str, Any] = field(default_factory=dict)
    description: str = ""
    docstring_full: str = ""
    response_fields: tuple[ResponseFieldSpec, ...] = ()


@dataclass(frozen=True)
class WriteCommandSpec:
    command: str
    value_arg: str
    args: Mapping[str, Any] = field(default_factory=dict)
    description: str = ""
    docstring_full: str = ""
    arg_fields: tuple[ArgFieldSpec, ...] = ()


@dataclass(frozen=True)
class ActionCommandSpec:
    command: str
    args: Mapping[str, Any] = field(default_factory=dict)
    arg_types: Mapping[str, ScalarValueType] = field(default_factory=dict)
    description: str = ""
    docstring_full: str = ""
    arg_fields: tuple[ArgFieldSpec, ...] = ()


@dataclass(frozen=True)
class ActionSpec:
    name: str
    action_cmd: ActionCommandSpec
    safety_mode: ActionSafetyMode = "guarded"


@dataclass(frozen=True)
class ValidatorSpec:
    kind: ValidatorKind
    min_value: float | None = None
    max_value: float | None = None
    choices: tuple[Any, ...] = ()


@dataclass(frozen=True)
class SafetySpec:
    min_value: float | None
    max_value: float | None
    max_step: float | None
    max_slew_per_s: float | None = None
    cooldown_s: float | None = None
    ramp_enabled: bool = True
    ramp_interval_s: float | None = None


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    label: str
    unit: str
    value_type: ScalarValueType
    get_cmd: ReadCommandSpec | None
    set_cmd: WriteCommandSpec | None
    vals: ValidatorSpec | None
    safety: SafetySpec | None
    snapshot_value: bool = True
    description: str = ""

    @property
    def readable(self) -> bool:
        return self.get_cmd is not None

    @property
    def writable(self) -> bool:
        return self.set_cmd is not None


def _resolve_manifest_path(parameter_file: str | Path) -> Path:
    manifest_path = Path(parameter_file).expanduser()
    if not manifest_path.exists():
        if manifest_path == DEFAULT_PARAMETERS_FILE.expanduser():
            return resolve_packaged_default("parameters.yaml")
        raise ValueError(f"Parameter file does not exist: {manifest_path}")
    return manifest_path


def _parse_scalar_value_type(value: Any, *, field_name: str) -> ScalarValueType:
    normalized = str(value).strip().lower()
    if normalized not in _ALLOWED_VALUE_TYPES:
        allowed = ", ".join(sorted(_ALLOWED_VALUE_TYPES))
        raise ValueError(f"{field_name} must be one of: {allowed}. Received: {value}")
    return cast(ScalarValueType, normalized)


def _infer_scalar_value_type(value: Any) -> ScalarValueType:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "str"


def load_parameter_specs(parameter_file: str | Path) -> tuple[ParameterSpec, ...]:
    manifest_path = _resolve_manifest_path(parameter_file)

    with manifest_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)

    if loaded is None:
        return ()

    root = _as_mapping(loaded, context="root")
    defaults = _as_mapping(root.get("defaults"), context="defaults")
    parameters_raw = root.get("parameters", {})
    if not isinstance(parameters_raw, dict):
        raise ValueError("Parameter file field 'parameters' must be a mapping keyed by name.")

    specs: list[ParameterSpec] = []
    for name in sorted(parameters_raw):
        spec_mapping = _as_mapping(parameters_raw[name], context=f"parameters.{name}")
        specs.append(_parse_parameter_spec(name=name, mapping=spec_mapping, defaults=defaults))

    return tuple(specs)


def load_action_specs(parameter_file: str | Path) -> tuple[ActionSpec, ...]:
    manifest_path = _resolve_manifest_path(parameter_file)

    with manifest_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)

    if loaded is None:
        return ()

    root = _as_mapping(loaded, context="root")
    actions_raw = root.get("actions", {})
    if actions_raw in (None, False):
        return ()
    if not isinstance(actions_raw, dict):
        raise ValueError("Parameter file field 'actions' must be a mapping keyed by action name.")

    specs: list[ActionSpec] = []
    for name in sorted(actions_raw):
        spec_mapping = _as_mapping(actions_raw[name], context=f"actions.{name}")
        specs.append(_parse_action_spec(name=str(name), mapping=spec_mapping))

    return tuple(specs)


def _parse_parameter_spec(
    *,
    name: str,
    mapping: Mapping[str, Any],
    defaults: Mapping[str, Any],
) -> ParameterSpec:
    label = str(mapping.get("label", name)).strip() or name
    unit = str(mapping.get("unit", "")).strip()
    description = str(mapping.get("description", "")).strip()

    raw_value_type = mapping.get("value_type", mapping.get("type", "float"))
    value_type_text = str(raw_value_type).strip().lower()
    if value_type_text not in _ALLOWED_VALUE_TYPES:
        allowed = ", ".join(sorted(_ALLOWED_VALUE_TYPES))
        raise ValueError(
            f"parameters.{name}.value_type must be one of: {allowed}. Received: {raw_value_type}"
        )
    value_type = cast(ScalarValueType, value_type_text)

    get_cmd = _parse_read_command(mapping.get("get_cmd"), context=f"parameters.{name}.get_cmd")
    set_cmd = _parse_write_command(mapping.get("set_cmd"), context=f"parameters.{name}.set_cmd")

    if get_cmd is None and set_cmd is None:
        raise ValueError(f"Parameter '{name}' must define at least one of get_cmd or set_cmd.")

    vals = _parse_vals(
        mapping.get("vals"), value_type=value_type, context=f"parameters.{name}.vals"
    )
    safety = _parse_safety(
        mapping.get("safety"),
        vals=vals,
        defaults=defaults,
        context=f"parameters.{name}.safety",
        writable=set_cmd is not None,
    )

    snapshot_value = _parse_bool(
        mapping.get("snapshot_value", defaults.get("snapshot_value", True)),
        field_name=f"parameters.{name}.snapshot_value",
    )

    if set_cmd is not None and safety is None:
        raise ValueError(f"Writable parameter '{name}' must include safety settings.")

    return ParameterSpec(
        name=name,
        label=label,
        unit=unit,
        description=description,
        value_type=value_type,
        get_cmd=get_cmd,
        set_cmd=set_cmd,
        vals=vals,
        safety=safety,
        snapshot_value=snapshot_value,
    )


def _parse_action_spec(*, name: str, mapping: Mapping[str, Any]) -> ActionSpec:
    action_cmd = _parse_action_command(
        mapping.get("action_cmd"), context=f"actions.{name}.action_cmd"
    )
    safety_mode = _parse_action_safety_mode(
        mapping.get("safety"),
        context=f"actions.{name}.safety",
    )
    return ActionSpec(name=name, action_cmd=action_cmd, safety_mode=safety_mode)


def _parse_action_command(value: Any, *, context: str) -> ActionCommandSpec:
    mapping = _as_mapping(value, context=context)
    command = _parse_required_string(mapping.get("command"), field_name=f"{context}.command")
    args_mapping = _as_mapping(mapping.get("args"), context=f"{context}.args")
    arg_types_mapping = _as_mapping(mapping.get("arg_types"), context=f"{context}.arg_types")
    description = str(mapping.get("description", "")).strip()
    docstring_full = str(mapping.get("docstring_full", "")).strip()
    arg_fields = _parse_arg_fields(mapping.get("arg_fields"), context=f"{context}.arg_fields")

    parsed_arg_types: dict[str, ScalarValueType] = {}
    arg_names = sorted({*args_mapping.keys(), *arg_types_mapping.keys()})
    for arg_name in arg_names:
        normalized_name = str(arg_name)
        explicit_type = arg_types_mapping.get(arg_name)
        if explicit_type is not None:
            parsed_arg_types[normalized_name] = _parse_scalar_value_type(
                explicit_type,
                field_name=f"{context}.arg_types.{normalized_name}",
            )
            continue

        parsed_arg_types[normalized_name] = _infer_scalar_value_type(args_mapping.get(arg_name))

    return ActionCommandSpec(
        command=command,
        args={str(key): value for key, value in args_mapping.items()},
        arg_types=parsed_arg_types,
        description=description,
        docstring_full=docstring_full,
        arg_fields=arg_fields,
    )


def _parse_action_safety_mode(value: Any, *, context: str) -> ActionSafetyMode:
    if value is None:
        return "guarded"

    if isinstance(value, str):
        mode_raw = value
    else:
        mapping = _as_mapping(value, context=context)
        mode_raw = mapping.get("mode", "guarded")

    normalized = str(mode_raw).strip()
    lowered = normalized.lower()
    if lowered == "readonly":
        return "alwaysAllowed"

    canonical: dict[str, ActionSafetyMode] = {
        "alwaysallowed": "alwaysAllowed",
        "guarded": "guarded",
        "blocked": "blocked",
    }
    mode = canonical.get(lowered)
    if mode is None:
        allowed = ", ".join(sorted(_ALLOWED_ACTION_SAFETY_MODES))
        raise ValueError(f"{context}.mode must be one of: {allowed}. Received: {mode_raw}")
    return mode


def _parse_read_command(value: Any, *, context: str) -> ReadCommandSpec | None:
    if value is False or value is None:
        return None

    mapping = _as_mapping(value, context=context)
    command = _parse_required_string(mapping.get("command"), field_name=f"{context}.command")
    payload_index = int(mapping.get("payload_index", 0))
    if payload_index < 0:
        raise ValueError(f"{context}.payload_index must be non-negative.")
    args = _as_mapping(mapping.get("args"), context=f"{context}.args")
    description = str(mapping.get("description", "")).strip()
    docstring_full = str(mapping.get("docstring_full", "")).strip()
    response_fields = _parse_response_fields(
        mapping.get("response_fields"),
        context=f"{context}.response_fields",
    )
    return ReadCommandSpec(
        command=command,
        payload_index=payload_index,
        args=dict(args),
        description=description,
        docstring_full=docstring_full,
        response_fields=response_fields,
    )


def _parse_write_command(value: Any, *, context: str) -> WriteCommandSpec | None:
    if value is False or value is None:
        return None

    mapping = _as_mapping(value, context=context)
    command = _parse_required_string(mapping.get("command"), field_name=f"{context}.command")
    value_arg = _parse_required_string(mapping.get("value_arg"), field_name=f"{context}.value_arg")
    args = _as_mapping(mapping.get("args"), context=f"{context}.args")
    description = str(mapping.get("description", "")).strip()
    docstring_full = str(mapping.get("docstring_full", "")).strip()
    arg_fields = _parse_arg_fields(mapping.get("arg_fields"), context=f"{context}.arg_fields")
    return WriteCommandSpec(
        command=command,
        value_arg=value_arg,
        args=dict(args),
        description=description,
        docstring_full=docstring_full,
        arg_fields=arg_fields,
    )


def _parse_response_fields(value: Any, *, context: str) -> tuple[ResponseFieldSpec, ...]:
    if value in (None, False):
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{context} must be a list when provided.")

    parsed: list[ResponseFieldSpec] = []
    for index, item in enumerate(value):
        mapping = _as_mapping(item, context=f"{context}[{index}]")
        parsed.append(
            ResponseFieldSpec(
                index=int(mapping.get("index", index)),
                name=_parse_required_string(
                    mapping.get("name"), field_name=f"{context}[{index}].name"
                ),
                type=str(mapping.get("type", "")).strip() or "unknown",
                unit=str(mapping.get("unit", "")).strip(),
                description=str(mapping.get("description", "")).strip(),
            )
        )
    return tuple(parsed)


def _parse_arg_fields(value: Any, *, context: str) -> tuple[ArgFieldSpec, ...]:
    if value in (None, False):
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{context} must be a list when provided.")

    parsed: list[ArgFieldSpec] = []
    for index, item in enumerate(value):
        mapping = _as_mapping(item, context=f"{context}[{index}]")
        parsed.append(
            ArgFieldSpec(
                name=_parse_required_string(
                    mapping.get("name"), field_name=f"{context}[{index}].name"
                ),
                type=str(mapping.get("type", "")).strip() or "unknown",
                required=_parse_bool(
                    mapping.get("required", False),
                    field_name=f"{context}[{index}].required",
                ),
                description=str(mapping.get("description", "")).strip(),
            )
        )
    return tuple(parsed)


def _parse_vals(value: Any, *, value_type: ScalarValueType, context: str) -> ValidatorSpec | None:
    if value is None:
        if value_type == "bool":
            return ValidatorSpec(kind="bool")
        return None
    if value is False:
        return None

    mapping = _as_mapping(value, context=context)
    kind_raw = mapping.get("kind", _default_validator_kind(value_type))
    kind = str(kind_raw).strip().lower()
    if kind not in _ALLOWED_VALIDATOR_KINDS:
        allowed = ", ".join(sorted(_ALLOWED_VALIDATOR_KINDS))
        raise ValueError(f"{context}.kind must be one of: {allowed}. Received: {kind_raw}")

    min_value = None if mapping.get("min") is None else float(mapping["min"])
    max_value = None if mapping.get("max") is None else float(mapping["max"])
    if min_value is not None and max_value is not None and max_value < min_value:
        raise ValueError(f"{context}: max must be >= min.")

    choices_raw = mapping.get("choices", ())
    if isinstance(choices_raw, (list, tuple)):
        choices = tuple(choices_raw)
    else:
        raise ValueError(f"{context}.choices must be a list when provided.")

    return ValidatorSpec(
        kind=cast(ValidatorKind, kind),
        min_value=min_value,
        max_value=max_value,
        choices=choices,
    )


def _parse_safety(
    value: Any,
    *,
    vals: ValidatorSpec | None,
    defaults: Mapping[str, Any],
    context: str,
    writable: bool,
) -> SafetySpec | None:
    if value is None:
        if not writable:
            return None
        mapping: Mapping[str, Any] = {}
    else:
        mapping = _as_mapping(value, context=context)

    if not writable and not mapping:
        return None

    min_default = vals.min_value if vals is not None else None
    max_default = vals.max_value if vals is not None else None
    max_step_default = None

    min_value_raw = mapping.get("min", min_default)
    max_value_raw = mapping.get("max", max_default)
    max_step_raw = mapping.get("max_step", max_step_default)

    min_value = None if min_value_raw is None else float(min_value_raw)
    max_value = None if max_value_raw is None else float(max_value_raw)
    max_step = None if max_step_raw is None else float(max_step_raw)
    if min_value is not None and max_value is not None and max_value <= min_value:
        raise ValueError(f"{context}.max must be > min.")
    if max_step is not None and max_step <= 0:
        raise ValueError(f"{context}.max_step must be positive.")

    max_slew_raw = mapping.get("max_slew_per_s")
    max_slew_per_s = None if max_slew_raw is None else float(max_slew_raw)
    if max_slew_per_s is not None and max_slew_per_s <= 0:
        raise ValueError(f"{context}.max_slew_per_s must be positive when provided.")

    if "cooldown_s" in mapping:
        cooldown_s_raw = mapping.get("cooldown_s")
    else:
        cooldown_s_raw = 0.0
    cooldown_s = None if cooldown_s_raw is None else float(cooldown_s_raw)
    if cooldown_s is not None and cooldown_s < 0:
        raise ValueError(f"{context}.cooldown_s must be non-negative.")

    if "require_confirmation" in mapping:
        raise ValueError(
            f"{context}.require_confirmation is no longer supported. "
            "Use allow_writes/dry_run policy controls."
        )

    ramp_enabled = _parse_bool(
        mapping.get("ramp_enabled", True), field_name=f"{context}.ramp_enabled"
    )

    if "ramp_interval_s" in mapping:
        ramp_interval_raw = mapping.get("ramp_interval_s")
    else:
        ramp_interval_raw = defaults.get("ramp_default_interval_s")
    ramp_interval_s = None if ramp_interval_raw is None else float(ramp_interval_raw)
    if ramp_interval_s is not None and ramp_interval_s <= 0:
        raise ValueError(f"{context}.ramp_interval_s must be positive when provided.")

    return SafetySpec(
        min_value=min_value,
        max_value=max_value,
        max_step=max_step,
        max_slew_per_s=max_slew_per_s,
        cooldown_s=cooldown_s,
        ramp_enabled=ramp_enabled,
        ramp_interval_s=ramp_interval_s,
    )


def _default_validator_kind(value_type: ScalarValueType) -> ValidatorKind:
    if value_type == "float":
        return "numbers"
    if value_type == "int":
        return "ints"
    if value_type == "bool":
        return "bool"
    return "none"


def _parse_required_string(value: Any, *, field_name: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise ValueError(f"{field_name} is required and cannot be empty.")
    return text


def _as_mapping(value: Any, *, context: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a mapping.")
    return value


def _parse_bool(value: Any, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0

    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ValueError(f"Invalid boolean value for {field_name}: {value}")


@dataclass(frozen=True)
class ScalarParameterSpec:
    name: str
    command: str
    value_type: ScalarValueType = "float"
    unit: str = ""
    label: str | None = None
    payload_index: int = 0
    args: Mapping[str, Any] = field(default_factory=dict)
    snapshot_value: bool = True


def load_scalar_parameter_specs(parameter_file: str | Path) -> tuple[ScalarParameterSpec, ...]:
    parameter_path = Path(parameter_file).expanduser()
    if not parameter_path.exists():
        raise ValueError(f"Parameter file does not exist: {parameter_path}")

    with parameter_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)

    if loaded is None:
        return ()

    root = _as_mapping(loaded, context="root")
    parameters_raw = root.get("parameters", [])
    if not isinstance(parameters_raw, list):
        raise ValueError("Parameter file field 'parameters' must be a list.")

    specs: list[ScalarParameterSpec] = []
    for index, entry in enumerate(parameters_raw):
        context = f"parameters[{index}]"
        mapping = _as_mapping(entry, context=context)

        raw_value_type = mapping.get("value_type", mapping.get("type", "float"))
        value_type_text = str(raw_value_type).strip().lower()
        if value_type_text not in _ALLOWED_VALUE_TYPES:
            allowed = ", ".join(sorted(_ALLOWED_VALUE_TYPES))
            raise ValueError(
                f"{context}.value_type must be one of: {allowed}. Received: {raw_value_type}"
            )

        payload_index = int(mapping.get("payload_index", 0))
        if payload_index < 0:
            raise ValueError(f"{context}.payload_index must be non-negative.")

        args = _as_mapping(mapping.get("args"), context=f"{context}.args")
        specs.append(
            ScalarParameterSpec(
                name=_parse_required_string(mapping.get("name"), field_name=f"{context}.name"),
                command=_parse_required_string(
                    mapping.get("command"), field_name=f"{context}.command"
                ),
                value_type=cast(ScalarValueType, value_type_text),
                unit=str(mapping.get("unit", "")).strip(),
                label=(None if mapping.get("label") is None else str(mapping.get("label")).strip()),
                payload_index=payload_index,
                args=dict(args),
                snapshot_value=_parse_bool(
                    mapping.get("snapshot_value", True),
                    field_name=f"{context}.snapshot_value",
                ),
            )
        )

    return tuple(specs)
