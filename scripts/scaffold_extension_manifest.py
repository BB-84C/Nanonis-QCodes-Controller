from __future__ import annotations

import argparse
import importlib.metadata
import inspect
import re
from pathlib import Path
from typing import Any

import yaml


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover nanonis_spm commands and scaffold parameter-file YAML.",
    )
    parser.add_argument("--match", type=str, default="", help="Regex filter (example: LockIn).")
    parser.add_argument(
        "--mode",
        choices=("list", "parameters"),
        default="list",
        help="Operation mode.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("config/extra_parameters.yaml"),
        help="Output path for --mode parameters.",
    )
    parser.add_argument(
        "--include-non-get",
        action="store_true",
        help="Include commands that do not end with Get.",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="Maximum number of commands (0 means all)."
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    command_specs = discover_command_specs(match_pattern=args.match)
    if args.limit > 0:
        command_specs = command_specs[: args.limit]

    if args.mode == "list":
        for spec in command_specs:
            print(f"{spec['command']}({', '.join(spec['arguments'])})")
        print(f"\nTotal commands: {len(command_specs)}")
        return 0

    selected_specs = command_specs
    if not args.include_non_get:
        selected_specs = [spec for spec in selected_specs if spec["command"].endswith("Get")]

    parameters: dict[str, Any] = {}
    for spec in selected_specs:
        command = str(spec["command"])
        parameter_name = to_parameter_name(command)
        parameters[parameter_name] = {
            "label": parameter_name,
            "unit": "",
            "type": guess_value_type(command),
            "get_cmd": {
                "command": command,
                "payload_index": 0,
                "args": {str(arg): None for arg in spec["arguments"]},
            },
            "set_cmd": False,
        }

    parameter_file = {
        "version": 1,
        "defaults": {
            "snapshot_value": True,
            "ramp_default_interval_s": 0.05,
        },
        "meta": {
            "generated_by": "scripts/scaffold_extension_manifest.py",
            "source_package": f"nanonis-spm/{installed_nanonis_spm_version()}",
            "match": args.match,
            "include_non_get": bool(args.include_non_get),
        },
        "parameters": parameters,
    }

    output_path = args.output.expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(parameter_file, handle, sort_keys=False)

    print(f"Wrote parameter file template: {output_path}")
    print(f"Parameter entries: {len(parameters)}")
    return 0


def discover_command_specs(*, match_pattern: str) -> list[dict[str, Any]]:
    try:
        import nanonis_spm
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "nanonis_spm is not installed. Install with: python -m pip install nanonis-spm"
        ) from exc

    command_pattern = re.compile(match_pattern, re.IGNORECASE) if match_pattern else None

    command_specs: list[dict[str, Any]] = []
    for name, member in inspect.getmembers(nanonis_spm.Nanonis, predicate=callable):
        if name.startswith("_"):
            continue
        if command_pattern is not None and command_pattern.search(name) is None:
            continue

        signature = inspect.signature(member)
        arguments = [
            parameter.name
            for parameter in signature.parameters.values()
            if parameter.name != "self"
            and parameter.kind
            in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
        ]
        command_specs.append({"command": name, "arguments": arguments})

    command_specs.sort(key=lambda item: item["command"])
    return command_specs


def to_parameter_name(command: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", command).strip("_").lower()
    if text.endswith("_get"):
        text = text[:-4]
    elif text.endswith("get"):
        text = text[:-3]
    if not text:
        raise ValueError(f"Cannot derive parameter name from command '{command}'.")
    return text


def guess_value_type(command: str) -> str:
    lowered = command.lower()
    if "onoffget" in lowered:
        return "bool"
    if "statusget" in lowered:
        return "int"
    return "float"


def installed_nanonis_spm_version() -> str:
    try:
        return importlib.metadata.version("nanonis-spm")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
