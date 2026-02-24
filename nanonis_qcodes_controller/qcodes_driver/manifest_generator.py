from __future__ import annotations

import html
import importlib
import importlib.metadata
import inspect
import re
from dataclasses import dataclass
from typing import Any

ScalarValueType = str

_SCALAR_TYPES: tuple[str, ...] = ("float", "int", "bool", "str")
_SELECTOR_TOKENS: tuple[str, ...] = (
    "number",
    "index",
    "idx",
    "channel",
    "line",
    "module",
    "demodulator",
    "modulator",
    "segment",
    "generator",
    "slot",
    "axis",
    "direction",
    "source",
    "signal",
    "id",
    "nr",
)
_VALUE_TOKENS: tuple[str, ...] = (
    "value",
    "amplitude",
    "gain",
    "setpoint",
    "frequency",
    "phase",
    "offset",
    "current",
    "bias",
    "voltage",
    "power",
    "time",
    "width",
    "height",
    "angle",
)


@dataclass(frozen=True)
class CommandInfo:
    command: str
    arguments: tuple[str, ...]
    signature: inspect.Signature
    doc: str


@dataclass(frozen=True)
class InferredSetMapping:
    value_arg: str
    fixed_args: dict[str, Any]


def discover_nanonis_commands(*, match_pattern: str = "") -> tuple[CommandInfo, ...]:
    try:
        nanonis_spm = importlib.import_module("nanonis_spm")
    except ModuleNotFoundError as exc:
        raise ValueError(
            "nanonis_spm is not installed. Install with: python -m pip install nanonis-spm"
        ) from exc

    compiled_pattern = re.compile(match_pattern, re.IGNORECASE) if match_pattern else None
    discovered: list[CommandInfo] = []

    for name, member in inspect.getmembers(nanonis_spm.Nanonis, predicate=callable):
        if name.startswith("_"):
            continue
        if compiled_pattern is not None and compiled_pattern.search(name) is None:
            continue

        signature = inspect.signature(member)
        arguments = tuple(
            parameter.name
            for parameter in signature.parameters.values()
            if parameter.name != "self"
            and parameter.kind
            in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
        )
        discovered.append(
            CommandInfo(
                command=name,
                arguments=arguments,
                signature=signature,
                doc=inspect.getdoc(member) or "",
            )
        )

    discovered.sort(key=lambda item: item.command)
    return tuple(discovered)


def build_unified_manifest(
    *,
    curated_defaults: dict[str, Any],
    curated_parameters: dict[str, Any],
    curated_actions: dict[str, Any],
    commands: tuple[CommandInfo, ...],
) -> dict[str, Any]:
    command_docs = {item.command: item.doc for item in commands}

    grouped: dict[str, dict[str, CommandInfo]] = {}
    action_commands: list[CommandInfo] = []
    for info in commands:
        if info.command.endswith("Get"):
            stem = info.command[:-3]
            grouped.setdefault(stem, {})["get"] = info
        elif info.command.endswith("Set"):
            stem = info.command[:-3]
            grouped.setdefault(stem, {})["set"] = info
        else:
            action_commands.append(info)

    generated_parameters: dict[str, dict[str, Any]] = {}
    get_set_commands_imported = 0
    for stem in sorted(grouped):
        get_info = grouped[stem].get("get")
        set_info = grouped[stem].get("set")
        if get_info is None and set_info is None:
            continue

        get_set_commands_imported += int(get_info is not None) + int(set_info is not None)
        if get_info is not None:
            reference_command = get_info.command
        elif set_info is not None:
            reference_command = set_info.command
        else:
            continue
        parameter_name = derive_parameter_name(reference_command)
        if parameter_name in generated_parameters:
            parameter_name = derive_parameter_name(f"{reference_command}_{stem}")

        generated_parameters[parameter_name] = _build_generated_parameter_entry(
            parameter_name=parameter_name,
            get_info=get_info,
            set_info=set_info,
        )

    merged_parameters: dict[str, Any] = {}
    all_names = sorted(set(generated_parameters).union(curated_parameters))
    for name in all_names:
        generated_entry = generated_parameters.get(name)
        curated_entry = curated_parameters.get(name)
        if generated_entry is not None and curated_entry is not None:
            merged_parameters[name] = _deep_merge(generated_entry, curated_entry)
        elif curated_entry is not None:
            merged_parameters[name] = curated_entry
        elif generated_entry is not None:
            merged_parameters[name] = generated_entry

    generated_actions: dict[str, dict[str, Any]] = {}
    for info in sorted(action_commands, key=lambda item: item.command):
        generated_actions[info.command] = _build_generated_action_entry(info=info)

    merged_actions: dict[str, Any] = {}
    all_action_names = sorted(set(generated_actions).union(curated_actions))
    for name in all_action_names:
        generated_entry = generated_actions.get(name)
        curated_entry = curated_actions.get(name)
        if generated_entry is not None and curated_entry is not None:
            merged_actions[name] = _deep_merge(generated_entry, curated_entry)
        elif curated_entry is not None:
            merged_actions[name] = curated_entry
        elif generated_entry is not None:
            merged_actions[name] = generated_entry

    for _name, parameter in merged_parameters.items():
        if not isinstance(parameter, dict):
            continue

        _ = parameter.pop("description", None)

        get_cmd = parameter.get("get_cmd")
        if isinstance(get_cmd, dict):
            get_description = str(get_cmd.get("description", "")).strip()
            if not get_description:
                get_command = str(get_cmd.get("command", "")).strip()
                if get_command:
                    get_cmd["description"] = extract_description(command_docs.get(get_command))

        set_cmd = parameter.get("set_cmd")
        if isinstance(set_cmd, dict):
            set_description = str(set_cmd.get("description", "")).strip()
            if not set_description:
                set_command = str(set_cmd.get("command", "")).strip()
                if set_command:
                    set_cmd["description"] = extract_description(command_docs.get(set_command))

    for _name, action in merged_actions.items():
        if not isinstance(action, dict):
            continue

        _ = action.pop("description", None)

        action_cmd = action.get("action_cmd")
        if isinstance(action_cmd, dict):
            command_name = str(action_cmd.get("command", "")).strip()
            command_description = str(action_cmd.get("description", "")).strip()
            if not command_description and command_name:
                action_cmd["description"] = extract_description(command_docs.get(command_name))

            arg_types = action_cmd.get("arg_types")
            args = action_cmd.get("args")
            normalized_arg_types: dict[str, ScalarValueType]
            if isinstance(arg_types, dict):
                normalized_arg_types = {
                    str(key): _normalize_scalar_type(value) for key, value in arg_types.items()
                }
            else:
                normalized_arg_types = {}

            if isinstance(args, dict):
                for arg_name, arg_value in args.items():
                    normalized_key = str(arg_name)
                    normalized_arg_types.setdefault(
                        normalized_key,
                        _infer_scalar_type_from_value(arg_value),
                    )
            action_cmd["arg_types"] = normalized_arg_types

        safety = action.get("safety")
        if isinstance(safety, dict):
            mode = _normalize_action_safety_mode(safety.get("mode", "guarded"))
            safety["mode"] = mode
            action["safety"] = safety
        else:
            action["safety"] = {"mode": "guarded"}

    defaults = {
        "snapshot_value": bool(curated_defaults.get("snapshot_value", True)),
        "ramp_default_interval_s": float(curated_defaults.get("ramp_default_interval_s", 0.05)),
    }

    parameter_description_count = 0
    action_description_count = 0
    writable_count = 0
    for parameter in merged_parameters.values():
        get_cmd = parameter.get("get_cmd")
        set_cmd = parameter.get("set_cmd")
        if (
            isinstance(get_cmd, dict)
            and str(get_cmd.get("description", "")).strip()
            or isinstance(set_cmd, dict)
            and str(set_cmd.get("description", "")).strip()
        ):
            parameter_description_count += 1
        if set_cmd is not None and set_cmd is not False:
            writable_count += 1

    for action in merged_actions.values():
        action_cmd = action.get("action_cmd") if isinstance(action, dict) else None
        if isinstance(action_cmd, dict) and str(action_cmd.get("description", "")).strip():
            action_description_count += 1

    return {
        "version": 1,
        "defaults": defaults,
        "meta": {
            "generated_by": "scripts/generate_parameters_manifest.py",
            "generated_at_utc": _utc_now_string(),
            "source_package": f"nanonis-spm/{installed_nanonis_spm_version()}",
            "methods_seen": len(commands),
            "get_set_commands_imported": get_set_commands_imported,
            "action_commands_imported": len(action_commands),
            "parameters_emitted": len(merged_parameters),
            "actions_emitted": len(merged_actions),
            "with_description_count": parameter_description_count,
            "actions_with_description_count": action_description_count,
            "writable_count": writable_count,
        },
        "parameters": merged_parameters,
        "actions": merged_actions,
    }


def extract_description(doc: str | None) -> str:
    if doc is None:
        return ""
    text = html.unescape(str(doc))
    lines = [line.strip() for line in text.splitlines()]

    cleaned: list[str] = []
    for line in lines:
        if not line:
            continue
        lowered = line.lower()
        if lowered.startswith("arguments:") or lowered.startswith("return arguments"):
            break
        if line.startswith("--"):
            continue
        if _looks_like_method_heading(line):
            continue
        cleaned.append(line)

    description = " ".join(cleaned)
    description = re.sub(r"\s+", " ", description).strip()
    return description


def infer_set_mapping(
    *,
    signature: inspect.Signature,
    args: tuple[str, ...],
    arg_docs: dict[str, str],
) -> InferredSetMapping:
    if not args:
        raise ValueError("Setter must define at least one non-self argument.")

    ranked: list[tuple[float, str]] = []
    selectors: dict[str, bool] = {}
    arg_types: dict[str, ScalarValueType] = {}
    for index, arg_name in enumerate(args):
        parameter = signature.parameters[arg_name]
        doc_line = _match_doc_for_arg(arg_name, arg_docs)
        inferred_type = _infer_type_for_arg(parameter=parameter, doc_line=doc_line, command_name="")
        arg_types[arg_name] = inferred_type
        selector = _is_selector_arg(
            arg_name=arg_name, doc_line=doc_line, inferred_type=inferred_type
        )
        selectors[arg_name] = selector

        score = float(index) / 1000.0
        if not selector:
            score += 1.0
        if inferred_type == "float":
            score += 3.0
        elif inferred_type == "int":
            score += 2.0
        elif inferred_type == "bool":
            score += 1.5
        if any(token in _normalize_key(arg_name) for token in _VALUE_TOKENS):
            score += 1.0
        ranked.append((score, arg_name))

    ranked.sort(key=lambda item: item[0])
    value_arg = ranked[-1][1]

    fixed_args: dict[str, Any] = {}
    for arg_name in args:
        if arg_name == value_arg:
            continue
        parameter = signature.parameters[arg_name]
        if parameter.default is not inspect.Parameter.empty:
            fixed_args[arg_name] = parameter.default
            continue

        inferred_type = arg_types[arg_name]
        if selectors[arg_name] and inferred_type == "int":
            fixed_args[arg_name] = 1
        elif inferred_type == "bool":
            fixed_args[arg_name] = 0
        elif inferred_type == "int":
            fixed_args[arg_name] = 0
        elif inferred_type == "float":
            fixed_args[arg_name] = 0.0
        elif inferred_type == "str":
            fixed_args[arg_name] = ""
        else:
            fixed_args[arg_name] = None

    return InferredSetMapping(value_arg=value_arg, fixed_args=fixed_args)


def derive_parameter_name(command_name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9]+", "_", str(command_name)).strip("_").lower()
    for suffix in ("_get", "get", "_set", "set"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    if not name:
        raise ValueError(f"Cannot derive parameter name from command '{command_name}'.")
    return name


def installed_nanonis_spm_version() -> str:
    try:
        return importlib.metadata.version("nanonis-spm")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _build_generated_parameter_entry(
    *,
    parameter_name: str,
    get_info: CommandInfo | None,
    set_info: CommandInfo | None,
) -> dict[str, Any]:
    get_arg_docs = _parse_argument_docs(get_info.doc) if get_info is not None else {}
    set_arg_docs = _parse_argument_docs(set_info.doc) if set_info is not None else {}

    inferred_type = _infer_parameter_type(
        get_info=get_info, set_info=set_info, set_arg_docs=set_arg_docs
    )
    label = parameter_name.replace("_", " ").strip().title()
    unit = _infer_parameter_unit(get_info=get_info, set_info=set_info, set_arg_docs=set_arg_docs)

    get_cmd: dict[str, Any] | bool
    if get_info is None:
        get_cmd = False
    else:
        read_args = _infer_read_args(
            signature=get_info.signature, args=get_info.arguments, arg_docs=get_arg_docs
        )
        get_cmd = {
            "command": get_info.command,
            "payload_index": 0,
            "args": read_args,
            "description": extract_description(get_info.doc),
        }

    set_cmd: dict[str, Any] | bool
    if set_info is None:
        set_cmd = False
    else:
        set_mapping = infer_set_mapping(
            signature=set_info.signature,
            args=set_info.arguments,
            arg_docs=set_arg_docs,
        )
        set_cmd = {
            "command": set_info.command,
            "value_arg": set_mapping.value_arg,
            "args": set_mapping.fixed_args,
            "description": extract_description(set_info.doc),
        }

    entry: dict[str, Any] = {
        "label": label,
        "unit": unit,
        "type": inferred_type,
        "get_cmd": get_cmd,
        "set_cmd": set_cmd,
        "vals": _default_vals_for_type(inferred_type),
    }

    if set_cmd is not False:
        entry["safety"] = {
            "min": None,
            "max": None,
            "max_step": None,
            "max_slew_per_s": None,
            "cooldown_s": None,
            "ramp_enabled": True,
            "ramp_interval_s": None,
        }
    return entry


def _build_generated_action_entry(*, info: CommandInfo) -> dict[str, Any]:
    arg_docs = _parse_argument_docs(info.doc)
    args: dict[str, Any] = {}
    arg_types: dict[str, ScalarValueType] = {}
    for arg_name in info.arguments:
        parameter = info.signature.parameters[arg_name]
        doc_line = _match_doc_for_arg(arg_name, arg_docs)
        inferred_type = _infer_action_arg_type(
            parameter=parameter,
            doc_line=doc_line,
            arg_name=arg_name,
        )
        arg_types[arg_name] = inferred_type

        if parameter.default is not inspect.Parameter.empty:
            args[arg_name] = parameter.default
            continue
        if _is_selector_arg(arg_name=arg_name, doc_line=doc_line, inferred_type=inferred_type):
            args[arg_name] = 1
            continue
        if inferred_type == "bool":
            args[arg_name] = 0
        elif inferred_type == "int":
            args[arg_name] = 0
        elif inferred_type == "float":
            args[arg_name] = 0.0
        elif inferred_type == "str":
            args[arg_name] = ""
        else:
            args[arg_name] = None

    return {
        "action_cmd": {
            "command": info.command,
            "args": args,
            "arg_types": arg_types,
            "description": extract_description(info.doc),
        },
        "safety": {"mode": "guarded"},
    }


def _parse_argument_docs(doc: str) -> dict[str, str]:
    if not doc:
        return {}

    lines = [html.unescape(line).rstrip() for line in doc.splitlines()]
    in_arguments = False
    current: str | None = None
    raw_items: list[str] = []
    for line in lines:
        stripped = line.strip()
        lowered = stripped.lower()
        if lowered.startswith("arguments:"):
            in_arguments = True
            continue
        if in_arguments and lowered.startswith("return arguments"):
            break
        if not in_arguments:
            continue

        if stripped.startswith("--"):
            if current is not None:
                raw_items.append(current)
            current = stripped[2:].strip()
            continue

        if current is not None and stripped:
            current = f"{current} {stripped}"

    if current is not None:
        raw_items.append(current)

    mapped: dict[str, str] = {}
    for item in raw_items:
        head = item
        if " is " in head:
            head = head.split(" is ", 1)[0]
        head = re.split(r"\(", head, maxsplit=1)[0].strip()
        normalized = _normalize_key(head)
        if normalized:
            mapped[normalized] = item
    return mapped


def _infer_parameter_type(
    *,
    get_info: CommandInfo | None,
    set_info: CommandInfo | None,
    set_arg_docs: dict[str, str],
) -> ScalarValueType:
    if get_info is not None:
        return_line = _extract_first_return_line(get_info.doc)
        inferred = _infer_scalar_type_from_text(return_line)
        if inferred is not None:
            return inferred
        inferred = _infer_scalar_type_from_text(get_info.command)
        if inferred is not None:
            return inferred

    if set_info is not None:
        set_mapping = infer_set_mapping(
            signature=set_info.signature,
            args=set_info.arguments,
            arg_docs={arg: _match_doc_for_arg(arg, set_arg_docs) for arg in set_info.arguments},
        )
        value_parameter = set_info.signature.parameters[set_mapping.value_arg]
        arg_doc = _match_doc_for_arg(set_mapping.value_arg, set_arg_docs)
        inferred = _infer_type_for_arg(
            parameter=value_parameter,
            doc_line=arg_doc,
            command_name=set_info.command,
        )
        if inferred in _SCALAR_TYPES:
            return inferred

    if get_info is not None:
        return _guess_value_type(get_info.command)
    if set_info is not None:
        return _guess_value_type(set_info.command)
    return "float"


def _infer_parameter_unit(
    *,
    get_info: CommandInfo | None,
    set_info: CommandInfo | None,
    set_arg_docs: dict[str, str],
) -> str:
    if get_info is not None:
        line = _extract_first_return_line(get_info.doc)
        unit = _extract_unit_from_doc_line(line)
        if unit:
            return unit

    if set_info is not None:
        set_mapping = infer_set_mapping(
            signature=set_info.signature,
            args=set_info.arguments,
            arg_docs={arg: _match_doc_for_arg(arg, set_arg_docs) for arg in set_info.arguments},
        )
        unit = _extract_unit_from_doc_line(_match_doc_for_arg(set_mapping.value_arg, set_arg_docs))
        if unit:
            return unit
    return ""


def _infer_read_args(
    *,
    signature: inspect.Signature,
    args: tuple[str, ...],
    arg_docs: dict[str, str],
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for arg_name in args:
        parameter = signature.parameters[arg_name]
        doc_line = _match_doc_for_arg(arg_name, arg_docs)
        arg_type = _infer_type_for_arg(parameter=parameter, doc_line=doc_line, command_name="")
        if parameter.default is not inspect.Parameter.empty:
            output[arg_name] = parameter.default
        elif _is_selector_arg(arg_name=arg_name, doc_line=doc_line, inferred_type=arg_type):
            output[arg_name] = 1
        elif arg_type == "bool":
            output[arg_name] = 0
        elif arg_type == "int":
            output[arg_name] = 0
        elif arg_type == "float":
            output[arg_name] = 0.0
        elif arg_type == "str":
            output[arg_name] = ""
        else:
            output[arg_name] = None
    return output


def _match_doc_for_arg(arg_name: str, arg_docs: dict[str, str]) -> str:
    normalized = _normalize_key(arg_name)
    if normalized in arg_docs:
        return arg_docs[normalized]

    best: tuple[int, str] | None = None
    for doc_name, line in arg_docs.items():
        if normalized in doc_name or doc_name in normalized:
            score = abs(len(normalized) - len(doc_name))
            if best is None or score < best[0]:
                best = (score, line)
    return "" if best is None else best[1]


def _extract_first_return_line(doc: str) -> str:
    if not doc:
        return ""
    lines = [html.unescape(line).strip() for line in doc.splitlines()]
    in_returns = False
    for line in lines:
        lowered = line.lower()
        if lowered.startswith("return arguments"):
            in_returns = True
            continue
        if not in_returns:
            continue
        if line.startswith("--"):
            return line[2:].strip()
    return ""


def _extract_unit_from_doc_line(line: str) -> str:
    if not line:
        return ""
    groups = re.findall(r"\(([^)]*)\)", line)
    if len(groups) < 2:
        return ""
    candidate = str(groups[-2]).strip()
    if _infer_scalar_type_from_text(candidate) is not None:
        return ""
    return candidate


def _infer_type_for_arg(
    *,
    parameter: inspect.Parameter,
    doc_line: str,
    command_name: str,
) -> ScalarValueType:
    from_doc = _infer_scalar_type_from_text(doc_line)
    if from_doc is not None:
        return from_doc

    annotation = parameter.annotation
    if annotation is not inspect.Parameter.empty:
        from_annotation = _infer_scalar_type_from_text(str(annotation))
        if from_annotation is not None:
            return from_annotation

    from_name = _infer_scalar_type_from_text(parameter.name)
    if from_name is not None:
        return from_name

    if command_name:
        return _guess_value_type(command_name)
    return "float"


def _infer_action_arg_type(
    *,
    parameter: inspect.Parameter,
    doc_line: str,
    arg_name: str,
) -> ScalarValueType:
    from_doc = _infer_scalar_type_from_text(doc_line)
    if from_doc is not None:
        return from_doc

    annotation = parameter.annotation
    if annotation is not inspect.Parameter.empty:
        from_annotation = _infer_scalar_type_from_text(str(annotation))
        if from_annotation is not None:
            return from_annotation

    from_name = _infer_scalar_type_from_text(arg_name)
    if from_name is not None:
        return from_name

    normalized = _normalize_key(arg_name)
    if any(token in normalized for token in _SELECTOR_TOKENS):
        return "int"
    if any(token in normalized for token in _VALUE_TOKENS):
        return "float"
    if any(
        token in normalized for token in ("path", "file", "folder", "directory", "name", "basename")
    ):
        return "str"
    return "str"


def _is_selector_arg(*, arg_name: str, doc_line: str, inferred_type: ScalarValueType) -> bool:
    if inferred_type != "int":
        return False

    normalized_name = _normalize_key(arg_name)
    if any(token in normalized_name for token in _SELECTOR_TOKENS):
        return True

    lowered_doc = doc_line.lower()
    return "specifies which" in lowered_doc or "starts from number" in lowered_doc


def _infer_scalar_type_from_text(text: str) -> ScalarValueType | None:
    lowered = str(text).lower()
    if not lowered:
        return None
    if any(token in lowered for token in ("bool", "boolean", "on/off", "onoff")):
        return "bool"
    if re.search(r"\b(unsigned\s+int\d*|uint\d*|int\d*|integer|int)\b", lowered):
        return "int"
    if re.search(r"\b(float\d*|double)\b", lowered):
        return "float"
    if re.search(r"\b(string|str|char|text)\b", lowered):
        return "str"
    return None


def _normalize_scalar_type(value: Any) -> ScalarValueType:
    normalized = str(value).strip().lower()
    if normalized in _SCALAR_TYPES:
        return normalized
    return "str"


def _infer_scalar_type_from_value(value: Any) -> ScalarValueType:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "str"


def _normalize_action_safety_mode(value: Any) -> str:
    normalized = str(value).strip().lower()
    if normalized in {"readonly", "alwaysallowed"}:
        return "alwaysAllowed"
    if normalized == "blocked":
        return "blocked"
    return "guarded"


def _default_vals_for_type(value_type: ScalarValueType) -> dict[str, Any]:
    if value_type == "bool":
        return {"kind": "bool"}
    if value_type == "int":
        return {"kind": "ints"}
    if value_type == "str":
        return {"kind": "none"}
    return {"kind": "numbers"}


def _guess_value_type(command_name: str) -> ScalarValueType:
    lowered = command_name.lower()
    if "onoff" in lowered:
        return "bool"
    if "status" in lowered:
        return "int"
    if "count" in lowered:
        return "int"
    return "float"


def _normalize_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text).lower())


def _looks_like_method_heading(line: str) -> bool:
    if " " in line:
        return False
    return bool(re.match(r"^[A-Za-z0-9_.]+(Get|Set)$", line))


def _utc_now_string() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        output = dict(base)
        for key, value in override.items():
            if key in output:
                output[key] = _deep_merge(output[key], value)
            else:
                output[key] = value
        return output
    return override
