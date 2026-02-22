from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import yaml

ScalarValueType = Literal["float", "int", "bool", "str"]
_ALLOWED_VALUE_TYPES: frozenset[str] = frozenset({"float", "int", "bool", "str"})


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


def load_scalar_parameter_specs(manifest_file: str | Path) -> tuple[ScalarParameterSpec, ...]:
    manifest_path = Path(manifest_file).expanduser()
    if not manifest_path.exists():
        raise ValueError(f"Manifest file does not exist: {manifest_path}")

    with manifest_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)

    if loaded is None:
        return ()

    manifest = _as_mapping(loaded, context="manifest")
    parameters_raw = manifest.get("parameters", [])
    if not isinstance(parameters_raw, list):
        raise ValueError("Manifest field 'parameters' must be a list.")

    specs: list[ScalarParameterSpec] = []
    for index, entry in enumerate(parameters_raw):
        context = f"parameters[{index}]"
        spec_mapping = _as_mapping(entry, context=context)
        specs.append(_parse_scalar_parameter_spec(spec_mapping, context=context))

    return tuple(specs)


def _parse_scalar_parameter_spec(
    mapping: Mapping[str, Any],
    *,
    context: str,
) -> ScalarParameterSpec:
    name = _parse_required_string(mapping.get("name"), field_name=f"{context}.name")
    command = _parse_required_string(mapping.get("command"), field_name=f"{context}.command")

    raw_value_type = mapping.get("value_type", mapping.get("type", "float"))
    value_type_text = str(raw_value_type).strip().lower()
    if value_type_text not in _ALLOWED_VALUE_TYPES:
        allowed = ", ".join(sorted(_ALLOWED_VALUE_TYPES))
        raise ValueError(
            f"{context}.value_type must be one of: {allowed}. Received: {raw_value_type}"
        )
    value_type = cast(ScalarValueType, value_type_text)

    unit = str(mapping.get("unit", "")).strip()

    raw_label = mapping.get("label")
    if raw_label is None:
        label = None
    else:
        label = str(raw_label).strip()
        if not label:
            label = None

    payload_index = int(mapping.get("payload_index", 0))
    if payload_index < 0:
        raise ValueError(f"{context}.payload_index must be non-negative.")

    args_raw = mapping.get("args", {})
    args = _as_mapping(args_raw, context=f"{context}.args")

    snapshot_value = _parse_bool(mapping.get("snapshot_value", True), field_name="snapshot_value")

    return ScalarParameterSpec(
        name=name,
        command=command,
        value_type=value_type,
        unit=unit,
        label=label,
        payload_index=payload_index,
        args=dict(args),
        snapshot_value=snapshot_value,
    )


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
