from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate unified config/parameters.yaml from nanonis_spm NanonisClass.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("config/parameters.yaml"),
        help="Output manifest path.",
    )
    parser.add_argument(
        "--curated-file",
        dest="curated_files",
        action="append",
        type=Path,
        default=None,
        help="Optional curated manifest input (repeatable).",
    )
    parser.add_argument(
        "--match",
        default="",
        help="Optional regex filter for method names.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional command limit (0 means all).",
    )
    return parser.parse_args()


def main() -> int:
    from nanonis_qcodes_controller.qcodes_driver.manifest_generator import (
        build_unified_manifest,
        discover_nanonis_commands,
    )

    args = parse_args()
    output_path = args.output.expanduser()
    curated_paths = _resolve_curated_paths(
        output_path=output_path,
        explicit_curated=args.curated_files,
    )

    curated_defaults, curated_parameters, curated_actions = _load_curated_inputs(curated_paths)
    commands = list(discover_nanonis_commands(match_pattern=str(args.match)))
    if args.limit > 0:
        commands = commands[: args.limit]

    manifest = build_unified_manifest(
        curated_defaults=curated_defaults,
        curated_parameters=curated_parameters,
        curated_actions=curated_actions,
        commands=tuple(commands),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(manifest, handle, sort_keys=False)

    meta = manifest.get("meta", {})
    print(f"Wrote unified parameters manifest: {output_path}")
    print(f"Methods seen: {meta.get('methods_seen')}")
    print(f"Get/Set commands imported: {meta.get('get_set_commands_imported')}")
    print(f"Action commands imported: {meta.get('action_commands_imported')}")
    print(f"Parameters emitted: {meta.get('parameters_emitted')}")
    print(f"Actions emitted: {meta.get('actions_emitted')}")
    print(f"With descriptions: {meta.get('with_description_count')}")
    print(f"Actions with descriptions: {meta.get('actions_with_description_count')}")
    print(f"Writable count: {meta.get('writable_count')}")
    return 0


def _resolve_curated_paths(
    *,
    output_path: Path,
    explicit_curated: list[Path] | None,
) -> list[Path]:
    if explicit_curated:
        return [path.expanduser() for path in explicit_curated]

    candidates = [
        Path("config/default_parameters.yaml"),
        Path("config/extra_parameters.yaml"),
    ]
    existing_candidates = [path.expanduser() for path in candidates if path.expanduser().is_file()]
    if existing_candidates:
        return existing_candidates

    if output_path.is_file():
        return [output_path]
    return []


def _load_curated_inputs(
    paths: list[Path],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    defaults: dict[str, Any] = {}
    parameters: dict[str, Any] = {}
    actions: dict[str, Any] = {}
    for path in paths:
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
        if loaded is None:
            continue
        if not isinstance(loaded, dict):
            continue

        loaded_defaults = loaded.get("defaults")
        if isinstance(loaded_defaults, dict):
            defaults.update(loaded_defaults)

        loaded_parameters = loaded.get("parameters")
        if isinstance(loaded_parameters, dict):
            for name, mapping in loaded_parameters.items():
                if not isinstance(mapping, dict):
                    continue
                parameters[str(name)] = mapping

        loaded_actions = loaded.get("actions")
        if isinstance(loaded_actions, dict):
            for name, mapping in loaded_actions.items():
                if not isinstance(mapping, dict):
                    continue
                actions[str(name)] = mapping

    return defaults, parameters, actions


if __name__ == "__main__":
    raise SystemExit(main())
