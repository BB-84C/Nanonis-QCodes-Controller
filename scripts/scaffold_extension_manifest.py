from __future__ import annotations

import argparse
import importlib.metadata
import inspect
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List nanonis_spm commands and scaffold dynamic QCodes parameter manifests.",
    )
    parser.add_argument(
        "--match",
        type=str,
        default="",
        help="Case-insensitive regex filter for command names (example: LockIn).",
    )
    parser.add_argument(
        "--mode",
        choices=("list", "manifest"),
        default="list",
        help="Operation mode: list matching commands or write a manifest template.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("config/extra_parameters.generated.yaml"),
        help="Output path for --mode manifest.",
    )
    parser.add_argument(
        "--include-non-get",
        action="store_true",
        help="In manifest mode, include commands that do not end with '_Get'.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum number of commands to include (0 means all).",
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

    manifest_specs = command_specs
    if not args.include_non_get:
        manifest_specs = [spec for spec in manifest_specs if spec["command"].endswith("Get")]

    manifest = {
        "meta": {
            "generated_by": "scripts/scaffold_extension_manifest.py",
            "source_package": f"nanonis-spm/{installed_nanonis_spm_version()}",
            "match": args.match,
            "include_non_get": bool(args.include_non_get),
        },
        "parameters": [build_parameter_entry(spec) for spec in manifest_specs],
    }

    output_path = args.output.expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(manifest, handle, sort_keys=False)

    print(f"Wrote manifest template: {output_path}")
    print(f"Parameter entries: {len(manifest['parameters'])}")
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
        command_specs.append(
            {
                "command": name,
                "arguments": arguments,
            }
        )

    command_specs.sort(key=lambda item: item["command"])
    return command_specs


def build_parameter_entry(spec: dict[str, Any]) -> dict[str, Any]:
    command = str(spec["command"])
    argument_names = tuple(str(name) for name in cast_sequence(spec.get("arguments")))

    parameter_name = to_parameter_name(command)
    value_type = guess_value_type(command)
    args = {name: None for name in argument_names}

    entry: dict[str, Any] = {
        "name": parameter_name,
        "command": command,
        "type": value_type,
    }
    if args:
        entry["args"] = args
    return entry


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


def cast_sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


if __name__ == "__main__":
    raise SystemExit(main())
