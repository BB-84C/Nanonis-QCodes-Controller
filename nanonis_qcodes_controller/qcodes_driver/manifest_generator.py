from __future__ import annotations

import ast
import html
import importlib
import importlib.metadata
import inspect
import re
import textwrap
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
    body_arg_names: tuple[str, ...]
    body_wire_types: tuple[str, ...]
    response_wire_types: tuple[str, ...]


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
    ordered_callables: list[tuple[str, Any]] = []
    for name, member in nanonis_spm.Nanonis.__dict__.items():
        if name.startswith("_"):
            continue
        callable_member: Any = member
        if isinstance(member, (staticmethod, classmethod)):
            callable_member = member.__func__
        if not callable(callable_member):
            continue
        ordered_callables.append((name, callable_member))

    anchor_index: int | None = None
    for index, (name, _member) in enumerate(ordered_callables):
        if name == "Bias_Set":
            anchor_index = index
            break
    if anchor_index is None:
        raise ValueError("Could not find Bias_Set anchor in nanonis_spm.Nanonis callables.")

    for name, member in ordered_callables[anchor_index:]:
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
        body_arg_names, body_wire_types, response_wire_types = _extract_quicksend_signature(member)
        discovered.append(
            CommandInfo(
                command=name,
                arguments=arguments,
                signature=signature,
                doc=inspect.getdoc(member) or "",
                body_arg_names=body_arg_names,
                body_wire_types=body_wire_types,
                response_wire_types=response_wire_types,
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
    command_infos = {item.command: item for item in commands}

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
    for name in sorted(generated_actions):
        generated_entry = generated_actions[name]
        curated_entry = curated_actions.get(name)
        if curated_entry is not None:
            merged_actions[name] = _deep_merge(generated_entry, curated_entry)
        else:
            merged_actions[name] = generated_entry

    for _name, parameter in merged_parameters.items():
        if not isinstance(parameter, dict):
            continue

        _ = parameter.pop("description", None)

        get_cmd = parameter.get("get_cmd")
        if isinstance(get_cmd, dict):
            get_description = str(get_cmd.get("description", "")).strip()
            get_command = str(get_cmd.get("command", "")).strip()
            if not get_description:
                if get_command:
                    get_cmd["description"] = extract_description(command_docs.get(get_command))
            args_mapping = get_cmd.pop("args", None)
            _ = get_cmd.pop("docstring_full", None)
            command_info = command_infos.get(get_command)
            if command_info is not None:
                argument_items, return_items = _parse_doc_sections(command_info.doc)
                get_arg_docs = _parse_argument_docs(command_info.doc)
                read_args = _infer_read_args(
                    signature=command_info.signature,
                    args=command_info.arguments,
                    arg_docs=get_arg_docs,
                )
                get_cmd["arg_fields"] = _build_arg_fields_from_wire(
                    signature=command_info.signature,
                    ordered_arg_names=command_info.body_arg_names or command_info.arguments,
                    wire_types=command_info.body_wire_types,
                    arg_docs=get_arg_docs,
                    argument_items=argument_items,
                    default_values=read_args,
                    required_arg_names=(),
                )
                get_cmd["response_fields"] = _build_response_fields_from_wire(
                    return_items=return_items,
                    wire_types=command_info.response_wire_types,
                )
            else:
                if not isinstance(get_cmd.get("response_fields"), list):
                    get_cmd["response_fields"] = []
                if not isinstance(get_cmd.get("arg_fields"), list):
                    get_cmd["arg_fields"] = _arg_fields_from_legacy_args(args_mapping)

        set_cmd = parameter.get("set_cmd")
        if isinstance(set_cmd, dict):
            set_description = str(set_cmd.get("description", "")).strip()
            set_command = str(set_cmd.get("command", "")).strip()
            value_arg = str(set_cmd.get("value_arg", "")).strip()
            if not set_description:
                if set_command:
                    set_cmd["description"] = extract_description(command_docs.get(set_command))
            args_mapping = set_cmd.pop("args", None)
            _ = set_cmd.pop("docstring_full", None)
            _ = set_cmd.pop("value_arg", None)
            command_info = command_infos.get(set_command)
            if command_info is not None:
                arg_items, _return_items = _parse_doc_sections(command_info.doc)
                set_arg_docs = _parse_argument_docs(command_info.doc)
                set_mapping = infer_set_mapping(
                    signature=command_info.signature,
                    args=command_info.arguments,
                    arg_docs=set_arg_docs,
                )
                set_defaults = {**set_mapping.fixed_args, set_mapping.value_arg: None}
                set_cmd["arg_fields"] = _build_arg_fields_from_wire(
                    signature=command_info.signature,
                    ordered_arg_names=command_info.body_arg_names or command_info.arguments,
                    wire_types=command_info.body_wire_types,
                    arg_docs=set_arg_docs,
                    argument_items=arg_items,
                    default_values=set_defaults,
                    required_arg_names=(set_mapping.value_arg,),
                )
            elif not isinstance(set_cmd.get("arg_fields"), list):
                defaults = dict(args_mapping) if isinstance(args_mapping, dict) else {}
                if value_arg and value_arg not in defaults:
                    defaults[value_arg] = None
                set_cmd["arg_fields"] = _arg_fields_from_legacy_args(defaults, value_arg=value_arg)

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
            args_mapping = action_cmd.pop("args", None)
            _ = action_cmd.pop("arg_types", None)
            _ = action_cmd.pop("docstring_full", None)
            command_info = command_infos.get(command_name)
            if command_info is not None:
                arg_items, _return_items = _parse_doc_sections(command_info.doc)
                action_arg_docs = _parse_argument_docs(command_info.doc)
                action_defaults: dict[str, Any] = {}
                for arg_name in command_info.arguments:
                    parameter = command_info.signature.parameters[arg_name]
                    doc_line = _match_doc_for_arg(arg_name, action_arg_docs)
                    inferred_type = _infer_action_arg_type(
                        parameter=parameter,
                        doc_line=doc_line,
                        arg_name=arg_name,
                    )
                    if parameter.default is not inspect.Parameter.empty:
                        action_defaults[arg_name] = parameter.default
                    elif _is_selector_arg(
                        arg_name=arg_name,
                        doc_line=doc_line,
                        inferred_type=inferred_type,
                    ):
                        action_defaults[arg_name] = 1
                    elif inferred_type == "bool":
                        action_defaults[arg_name] = 0
                    elif inferred_type == "int":
                        action_defaults[arg_name] = 0
                    elif inferred_type == "float":
                        action_defaults[arg_name] = 0.0
                    elif inferred_type == "str":
                        action_defaults[arg_name] = ""
                    else:
                        action_defaults[arg_name] = None

                action_cmd["arg_fields"] = _build_arg_fields_from_wire(
                    signature=command_info.signature,
                    ordered_arg_names=command_info.body_arg_names or command_info.arguments,
                    wire_types=command_info.body_wire_types,
                    arg_docs=action_arg_docs,
                    argument_items=arg_items,
                    default_values=action_defaults,
                    required_arg_names=(),
                )
            elif not isinstance(action_cmd.get("arg_fields"), list):
                action_cmd["arg_fields"] = _arg_fields_from_legacy_args(args_mapping)

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
        argument_items, return_items = _parse_doc_sections(get_info.doc)
        read_args = _infer_read_args(
            signature=get_info.signature,
            args=get_info.arguments,
            arg_docs=get_arg_docs,
        )
        ordered_get_args = get_info.body_arg_names or get_info.arguments
        get_cmd = {
            "command": get_info.command,
            "payload_index": 0,
            "description": extract_description(get_info.doc),
            "arg_fields": _build_arg_fields_from_wire(
                signature=get_info.signature,
                ordered_arg_names=ordered_get_args,
                wire_types=get_info.body_wire_types,
                arg_docs=get_arg_docs,
                argument_items=argument_items,
                default_values=read_args,
                required_arg_names=(),
            ),
            "response_fields": _build_response_fields_from_wire(
                return_items=return_items,
                wire_types=get_info.response_wire_types,
            ),
        }

    set_cmd: dict[str, Any] | bool
    if set_info is None:
        set_cmd = False
    else:
        argument_items, _return_items = _parse_doc_sections(set_info.doc)
        set_mapping = infer_set_mapping(
            signature=set_info.signature,
            args=set_info.arguments,
            arg_docs=set_arg_docs,
        )
        ordered_set_args = set_info.body_arg_names or set_info.arguments
        set_defaults = {**set_mapping.fixed_args, set_mapping.value_arg: None}
        set_cmd = {
            "command": set_info.command,
            "description": extract_description(set_info.doc),
            "arg_fields": _build_arg_fields_from_wire(
                signature=set_info.signature,
                ordered_arg_names=ordered_set_args,
                wire_types=set_info.body_wire_types,
                arg_docs=set_arg_docs,
                argument_items=argument_items,
                default_values=set_defaults,
                required_arg_names=(set_mapping.value_arg,),
            ),
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
    argument_items, _return_items = _parse_doc_sections(info.doc)
    action_defaults: dict[str, Any] = {}
    for arg_name in info.arguments:
        parameter = info.signature.parameters[arg_name]
        doc_line = _match_doc_for_arg(arg_name, arg_docs)
        inferred_type = _infer_action_arg_type(
            parameter=parameter,
            doc_line=doc_line,
            arg_name=arg_name,
        )
        if parameter.default is not inspect.Parameter.empty:
            action_defaults[arg_name] = parameter.default
        elif _is_selector_arg(arg_name=arg_name, doc_line=doc_line, inferred_type=inferred_type):
            action_defaults[arg_name] = 1
        elif inferred_type == "bool":
            action_defaults[arg_name] = 0
        elif inferred_type == "int":
            action_defaults[arg_name] = 0
        elif inferred_type == "float":
            action_defaults[arg_name] = 0.0
        elif inferred_type == "str":
            action_defaults[arg_name] = ""
        else:
            action_defaults[arg_name] = None

    return {
        "action_cmd": {
            "command": info.command,
            "description": extract_description(info.doc),
            "arg_fields": _build_arg_fields_from_wire(
                signature=info.signature,
                ordered_arg_names=info.body_arg_names or info.arguments,
                wire_types=info.body_wire_types,
                arg_docs=arg_docs,
                argument_items=argument_items,
                default_values=action_defaults,
                required_arg_names=(),
            ),
        },
        "safety": {"mode": "guarded"},
    }


def _extract_quicksend_signature(
    member: Any,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    try:
        source = inspect.getsource(member)
    except (OSError, TypeError):
        return ((), (), ())

    try:
        module = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return ((), (), ())

    call: ast.Call | None = None
    for node in ast.walk(module):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "quickSend":
            call = node
            break
    if call is None or len(call.args) < 4:
        return ((), (), ())

    body_arg_names = _extract_body_arg_names(call.args[1])
    body_wire_types = _extract_literal_string_list(call.args[2])
    response_wire_types = _extract_literal_string_list(call.args[3])
    return (body_arg_names, body_wire_types, response_wire_types)


def _extract_body_arg_names(node: ast.AST) -> tuple[str, ...]:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return ()
    names: list[str] = []
    for index, item in enumerate(node.elts):
        if isinstance(item, ast.Name):
            names.append(str(item.id))
            continue
        if isinstance(item, ast.Attribute):
            names.append(str(item.attr))
            continue
        try:
            unparsed = ast.unparse(item)
        except Exception:
            unparsed = f"arg_{index}"
        names.append(re.sub(r"\W+", "_", unparsed).strip("_") or f"arg_{index}")
    return tuple(names)


def _extract_literal_string_list(node: ast.AST) -> tuple[str, ...]:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return ()
    output: list[str] = []
    for item in node.elts:
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            output.append(str(item.value))
    return tuple(output)


def _build_response_fields_from_wire(
    *,
    return_items: list[str],
    wire_types: tuple[str, ...],
) -> list[dict[str, Any]]:
    if not wire_types:
        return _build_response_fields(return_items)

    fields: list[dict[str, Any]] = []
    for index, wire_type in enumerate(wire_types):
        item = return_items[index] if index < len(return_items) else ""
        name = _extract_doc_item_name(item) if item else f"response_{index}"
        value_type, unit = _extract_doc_type_and_unit(item)
        if value_type == "unknown":
            value_type = _normalize_wire_type(wire_type)
        fields.append(
            {
                "index": index,
                "name": name,
                "type": value_type,
                "unit": unit,
                "wire_type": wire_type,
                "description": item.strip(),
            }
        )
    return fields


def _build_arg_fields_from_wire(
    *,
    signature: inspect.Signature,
    ordered_arg_names: tuple[str, ...],
    wire_types: tuple[str, ...],
    arg_docs: dict[str, str],
    argument_items: list[str],
    default_values: dict[str, Any],
    required_arg_names: tuple[str, ...],
) -> list[dict[str, Any]]:
    arg_item_map: dict[str, str] = {}
    for item in argument_items:
        key = _normalize_key(_extract_doc_item_name(item))
        if key:
            arg_item_map[key] = item

    if not ordered_arg_names:
        ordered_arg_names = tuple(
            name
            for name in signature.parameters
            if name != "self"
            and signature.parameters[name].kind
            in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
        )

    required_names = set(required_arg_names)
    fields: list[dict[str, Any]] = []
    for index, arg_name in enumerate(ordered_arg_names):
        parameter = signature.parameters.get(arg_name)
        doc_line = _match_doc_for_arg(arg_name, arg_docs)
        if not doc_line and index < len(argument_items):
            doc_line = argument_items[index]

        value_type, unit = _extract_doc_type_and_unit(doc_line)
        wire_type = wire_types[index] if index < len(wire_types) else ""
        if value_type == "unknown":
            value_type = _normalize_wire_type(wire_type)
        if value_type == "unknown" and parameter is not None:
            value_type = _infer_type_for_arg(
                parameter=parameter, doc_line=doc_line, command_name=""
            )

        default_value = default_values.get(arg_name)
        required = arg_name in required_names
        if not required and parameter is not None and parameter.default is inspect.Parameter.empty:
            required = default_value is None

        fields.append(
            {
                "name": arg_name,
                "type": value_type,
                "unit": unit,
                "wire_type": wire_type,
                "required": required,
                "default": default_value,
                "description": doc_line.strip(),
            }
        )
    return fields


def _arg_fields_from_legacy_args(
    args_mapping: Any,
    *,
    value_arg: str | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(args_mapping, dict):
        return []
    fields: list[dict[str, Any]] = []
    for key, value in args_mapping.items():
        name = str(key)
        value_type = _infer_scalar_type_from_value(value)
        required = bool(value_arg and name == value_arg and value is None)
        fields.append(
            {
                "name": name,
                "type": value_type,
                "unit": "",
                "wire_type": "",
                "required": required,
                "default": value,
                "description": "",
            }
        )
    return fields


def _normalize_wire_type(wire_type: str) -> str:
    token = str(wire_type).strip()
    if not token:
        return "unknown"
    is_array = "*" in token
    normalized = token.replace("+", "").replace("*", "")
    base = "unknown"
    if normalized in {"f", "d"}:
        base = "float"
    elif normalized in {"?"}:
        base = "bool"
    elif normalized in {"s", "c"}:
        base = "str"
    elif normalized in {"b", "B", "h", "H", "i", "I", "l", "L", "q", "Q"}:
        base = "int"
    if is_array and base != "unknown":
        return f"array[{base}]"
    if is_array:
        return "array"
    return base


def _parse_doc_sections(doc: str) -> tuple[list[str], list[str]]:
    if not doc:
        return ([], [])

    lines = [html.unescape(line).rstrip() for line in doc.splitlines()]
    mode: str | None = None
    arg_items: list[str] = []
    return_items: list[str] = []
    current: str | None = None

    def _flush_current(target: list[str]) -> None:
        nonlocal current
        if current is not None:
            target.append(current.strip())
            current = None

    for raw in lines:
        stripped = raw.strip()
        lowered = stripped.lower()
        if lowered.startswith("arguments:"):
            if mode == "returns":
                _flush_current(return_items)
            elif mode == "args":
                _flush_current(arg_items)
            mode = "args"
            continue
        if lowered.startswith("return arguments"):
            if mode == "args":
                _flush_current(arg_items)
            elif mode == "returns":
                _flush_current(return_items)
            mode = "returns"
            continue
        if mode not in {"args", "returns"}:
            continue

        target = arg_items if mode == "args" else return_items
        if stripped.startswith("--"):
            _flush_current(target)
            current = stripped[2:].strip()
            continue

        if current is not None and stripped:
            current = f"{current} {stripped}"

    if mode == "args":
        _flush_current(arg_items)
    elif mode == "returns":
        _flush_current(return_items)
    return (arg_items, return_items)


def _build_response_fields(return_items: list[str]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for index, item in enumerate(return_items):
        name = _extract_doc_item_name(item)
        value_type, unit = _extract_doc_type_and_unit(item)
        fields.append(
            {
                "index": index,
                "name": name,
                "type": value_type,
                "unit": unit,
                "wire_type": "",
                "description": item.strip(),
            }
        )
    return fields


def _build_arg_fields(
    *,
    signature: inspect.Signature,
    args: tuple[str, ...],
    arg_docs: dict[str, str],
    argument_items: list[str],
) -> list[dict[str, Any]]:
    arg_item_map: dict[str, str] = {}
    for item in argument_items:
        key = _normalize_key(_extract_doc_item_name(item))
        if key:
            arg_item_map[key] = item

    fields: list[dict[str, Any]] = []
    for arg_name in args:
        parameter = signature.parameters[arg_name]
        doc_line = _match_doc_for_arg(arg_name, arg_docs)
        item = arg_item_map.get(_normalize_key(arg_name), doc_line)
        value_type, _unit = _extract_doc_type_and_unit(item)
        if value_type == "unknown":
            value_type = _infer_type_for_arg(
                parameter=parameter, doc_line=doc_line, command_name=""
            )
        required = parameter.default is inspect.Parameter.empty
        fields.append(
            {
                "name": arg_name,
                "type": value_type,
                "required": required,
                "description": item.strip(),
            }
        )
    return fields


def _extract_doc_item_name(item: str) -> str:
    base = str(item)
    if " is " in base:
        base = base.split(" is ", 1)[0]
    base = re.split(r"\(", base, maxsplit=1)[0].strip()
    return base


def _extract_doc_type_and_unit(item: str) -> tuple[str, str]:
    groups = [str(group).strip() for group in re.findall(r"\(([^)]*)\)", item)]
    if not groups:
        return ("unknown", "")

    type_index: int | None = None
    value_type = "unknown"
    for index in range(len(groups) - 1, -1, -1):
        normalized = _normalize_doc_type(groups[index])
        if normalized != "unknown":
            type_index = index
            value_type = normalized
            break

    unit = ""
    if type_index is not None:
        for index in range(type_index - 1, -1, -1):
            if _normalize_doc_type(groups[index]) == "unknown":
                unit = groups[index]
                break
    elif len(groups) == 1:
        unit = groups[0]
    return (value_type, unit)


def _normalize_doc_type(token: str) -> str:
    lowered = str(token).strip().lower()
    if not lowered:
        return "unknown"
    if "array" in lowered:
        if "float" in lowered:
            return "array[float]"
        if "int" in lowered:
            return "array[int]"
        if "bool" in lowered:
            return "array[bool]"
        if "str" in lowered or "string" in lowered or "char" in lowered:
            return "array[str]"
        return "array"
    if "float" in lowered or "double" in lowered:
        return "float"
    if "int" in lowered or "unsigned" in lowered or "signed" in lowered:
        return "int"
    if "bool" in lowered:
        return "bool"
    if "str" in lowered or "string" in lowered or "char" in lowered:
        return "str"
    return "unknown"


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
