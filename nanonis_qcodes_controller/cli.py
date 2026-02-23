from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import inspect
import json
import math
import re
import sqlite3
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from nanonis_qcodes_controller.client import create_client, probe_host_ports, report_to_dict
from nanonis_qcodes_controller.client.errors import (
    NanonisCommandUnavailableError,
    NanonisConnectionError,
    NanonisInvalidArgumentError,
    NanonisProtocolError,
    NanonisTimeoutError,
)
from nanonis_qcodes_controller.config import load_settings
from nanonis_qcodes_controller.qcodes_driver.extensions import (
    DEFAULT_EXTRA_PARAMETERS_FILE,
    DEFAULT_PARAMETERS_FILE,
    load_parameter_spec_bundle,
    load_parameter_specs,
)
from nanonis_qcodes_controller.safety import PolicyViolation
from nanonis_qcodes_controller.trajectory import (
    TrajectoryJournal,
    clear_staged_run_name,
    default_monitor_config,
    default_staged_config_path,
    follow_events,
    load_staged_monitor_config,
    read_events,
    save_staged_monitor_config,
)
from nanonis_qcodes_controller.trajectory.monitor import TrajectoryMonitorRunner
from nanonis_qcodes_controller.trajectory.sqlite_store import TrajectorySQLiteStore
from nanonis_qcodes_controller.version import __version__

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_POLICY_BLOCKED = 2
EXIT_INVALID_INPUT = 3
EXIT_COMMAND_UNAVAILABLE = 4
EXIT_CONNECTION_FAILED = 5


@dataclass(frozen=True)
class ActionDescriptor:
    name: str
    safety: str
    description: str
    command_template: str
    arguments: tuple[str, ...]


@dataclass(frozen=True)
class DiscoveredCommand:
    command: str
    arguments: tuple[str, ...]


_ACTION_DESCRIPTORS: tuple[ActionDescriptor, ...] = (
    ActionDescriptor(
        name="get",
        safety="readonly",
        description="Read a single parameter value.",
        command_template="nqctl get <parameter>",
        arguments=("parameter",),
    ),
    ActionDescriptor(
        name="set",
        safety="guarded",
        description="Apply guarded strict single-step write.",
        command_template="nqctl set <parameter> <value>",
        arguments=("parameter", "value"),
    ),
    ActionDescriptor(
        name="ramp",
        safety="guarded",
        description="Apply explicit ramp using start/end/step/interval.",
        command_template="nqctl ramp <parameter> <start> <end> <step> --interval-s 0.1",
        arguments=("parameter", "start", "end", "step", "interval_s"),
    ),
    ActionDescriptor(
        name="parameters_discover",
        safety="readonly",
        description="Discover backend commands for parameter authoring.",
        command_template="nqctl parameters discover --match LockIn",
        arguments=("match",),
    ),
    ActionDescriptor(
        name="parameters_scaffold",
        safety="readonly",
        description="Scaffold extra parameter file from command list.",
        command_template="nqctl parameters scaffold --match LockIn --output config/extra_parameters.yaml",
        arguments=("match", "output"),
    ),
    ActionDescriptor(
        name="trajectory_tail",
        safety="readonly",
        description="Tail events from non-blocking trajectory logs.",
        command_template="nqctl trajectory tail --directory artifacts/trajectory --limit 20",
        arguments=("directory", "limit"),
    ),
)


def _normalize_help_args(argv: Sequence[str]) -> list[str]:
    tokens = ["--help" if token == "-help" else str(token) for token in argv]
    if not tokens:
        return tokens
    if tokens[0] in {"-h", "--help"}:
        if len(tokens) == 1:
            return ["--help"]
        if tokens[1].startswith("-"):
            return ["--help", *tokens[1:]]
        return [*tokens[1:], "--help"]
    return tokens


def main(argv: Sequence[str] | None = None) -> int:
    normalized_argv = _normalize_help_args(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    args = parser.parse_args(normalized_argv)

    try:
        return int(args.handler(args))
    except PolicyViolation as exc:
        return _emit_error(
            args,
            exit_code=EXIT_POLICY_BLOCKED,
            message=f"Policy blocked operation: {exc}",
            error_type=type(exc).__name__,
        )
    except (NanonisConnectionError, NanonisTimeoutError) as exc:
        return _emit_error(
            args,
            exit_code=EXIT_CONNECTION_FAILED,
            message=f"Connection error: {exc}",
            error_type=type(exc).__name__,
        )
    except NanonisCommandUnavailableError as exc:
        return _emit_error(
            args,
            exit_code=EXIT_COMMAND_UNAVAILABLE,
            message=str(exc),
            error_type=type(exc).__name__,
        )
    except (NanonisInvalidArgumentError, NanonisProtocolError, ValueError) as exc:
        return _emit_error(
            args,
            exit_code=EXIT_INVALID_INPUT,
            message=str(exc),
            error_type=type(exc).__name__,
        )
    except KeyboardInterrupt:
        return _emit_error(
            args,
            exit_code=EXIT_FAILED,
            message="Interrupted by user.",
            error_type="KeyboardInterrupt",
        )
    except Exception as exc:  # pragma: no cover
        return _emit_error(
            args,
            exit_code=EXIT_FAILED,
            message=f"Unexpected error: {exc}",
            error_type=type(exc).__name__,
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nqctl",
        description=(
            "Nanonis-QCodes bridge CLI for agent orchestration.\n"
            "Use atomic commands (capabilities/get/set/ramp/parameters/trajectory)."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Quick start:\n"
            "  nqctl capabilities\n"
            "  nqctl get bias_v\n"
            "  nqctl set bias_v 0.12\n"
            "  nqctl ramp bias_v 0.1 0.3 0.01 --interval-s 0.1\n"
            "\n"
            "Help shortcuts:\n"
            "  nqctl -help\n"
            "  nqctl -help parameters\n"
            "  nqctl -help ramp"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    parser_capabilities = subparsers.add_parser(
        "capabilities",
        help="Show available parameters/actions and policy summary.",
    )
    _add_runtime_args(parser_capabilities, include_trajectory=False)
    parser_capabilities.add_argument(
        "--include-backend-commands",
        action="store_true",
        help="Connect and include backend command names.",
    )
    parser_capabilities.add_argument("--backend-match", help="Optional filter token.")
    parser_capabilities.set_defaults(handler=_cmd_capabilities)

    parser_get = subparsers.add_parser("get", help="Read a single parameter value.")
    _add_runtime_args(parser_get, include_trajectory=True)
    parser_get.add_argument("parameter", help="Parameter name from parameter files.")
    parser_get.set_defaults(handler=_cmd_get)

    parser_set = subparsers.add_parser(
        "set",
        help="Apply guarded strict single-step write.",
        description=(
            "Apply guarded strict single-step write for writable numeric parameters.\n"
            "Use `nqctl ramp` for stepped trajectories."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n  nqctl set bias_v 0.15\n  nqctl set zctrl_setpoint_a 8e-11 --plan-only"
        ),
    )
    _add_runtime_args(parser_set, include_trajectory=True)
    parser_set.add_argument("parameter", help="Writable parameter name.")
    parser_set.add_argument("value", help="Target numeric value.")
    parser_set.add_argument("--interval-s", type=float, help="Optional interval for slew checks.")
    parser_set.add_argument("--plan-only", action="store_true", help="Show plan only.")
    parser_set.set_defaults(handler=_cmd_set)

    parser_ramp = subparsers.add_parser(
        "ramp",
        help="Apply explicit guarded ramp.",
        description="Apply explicit ramp with start/end/step/interval.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  nqctl ramp bias_v 0.1 0.25 0.01 --interval-s 0.1\n"
            "  nqctl ramp zctrl_setpoint_a 5e-11 1e-10 5e-12 --interval-s 0.05 --plan-only"
        ),
    )
    _add_runtime_args(parser_ramp, include_trajectory=True)
    parser_ramp.add_argument("parameter", help="Writable parameter name.")
    parser_ramp.add_argument("start", help="Ramp start value.")
    parser_ramp.add_argument("end", help="Ramp end value.")
    parser_ramp.add_argument("step", help="Positive ramp step value.")
    parser_ramp.add_argument(
        "--interval-s", type=float, required=True, help="Step interval in seconds."
    )
    parser_ramp.add_argument("--plan-only", action="store_true", help="Show ramp plan only.")
    parser_ramp.set_defaults(handler=_cmd_ramp)

    parser_observables = subparsers.add_parser("observables", help="Observable metadata commands.")
    observables_subparsers = parser_observables.add_subparsers(
        dest="observables_command", required=True
    )
    parser_observables_list = observables_subparsers.add_parser(
        "list", help="List observable parameters."
    )
    _add_runtime_args(parser_observables_list, include_trajectory=False)
    parser_observables_list.set_defaults(handler=_cmd_observables_list)

    parser_actions = subparsers.add_parser("actions", help="Action metadata commands.")
    actions_subparsers = parser_actions.add_subparsers(dest="actions_command", required=True)
    parser_actions_list = actions_subparsers.add_parser("list", help="List actions.")
    _add_json_arg(parser_actions_list)
    parser_actions_list.set_defaults(handler=_cmd_actions_list)

    parser_parameters = subparsers.add_parser("parameters", help="Parameter-file helper commands.")
    parameters_subparsers = parser_parameters.add_subparsers(
        dest="parameters_command", required=True
    )

    parser_parameters_discover = parameters_subparsers.add_parser(
        "discover",
        help="Discover nanonis_spm command names/signatures.",
    )
    _add_json_arg(parser_parameters_discover)
    parser_parameters_discover.add_argument(
        "--match", default="", help="Case-insensitive regex token."
    )
    parser_parameters_discover.add_argument(
        "--limit", type=int, default=0, help="Max results (0 means all)."
    )
    parser_parameters_discover.set_defaults(handler=_cmd_parameters_discover)

    parser_parameters_scaffold = parameters_subparsers.add_parser(
        "scaffold",
        help="Scaffold extra parameter file YAML.",
    )
    _add_json_arg(parser_parameters_scaffold)
    parser_parameters_scaffold.add_argument(
        "--match", default="", help="Case-insensitive regex token."
    )
    parser_parameters_scaffold.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_EXTRA_PARAMETERS_FILE,
        help="Output path for generated extra parameters file.",
    )
    parser_parameters_scaffold.add_argument(
        "--include-non-get",
        action="store_true",
        help="Include non-Get commands (usually not recommended).",
    )
    parser_parameters_scaffold.add_argument(
        "--limit", type=int, default=0, help="Max commands (0 means all)."
    )
    parser_parameters_scaffold.set_defaults(handler=_cmd_parameters_scaffold)

    parser_parameters_validate = parameters_subparsers.add_parser(
        "validate",
        help="Validate parameter-file schema.",
    )
    _add_json_arg(parser_parameters_validate)
    parser_parameters_validate.add_argument(
        "--file", type=Path, required=True, help="YAML file to validate."
    )
    parser_parameters_validate.set_defaults(handler=_cmd_parameters_validate)

    parser_policy = subparsers.add_parser(
        "policy", help="Policy metadata and write-enable guidance."
    )
    policy_subparsers = parser_policy.add_subparsers(dest="policy_command", required=True)
    parser_policy_show = policy_subparsers.add_parser("show", help="Show active policy values.")
    _add_json_arg(parser_policy_show)
    parser_policy_show.add_argument("--config-file")
    parser_policy_show.set_defaults(handler=_cmd_policy_show)

    parser_trajectory = subparsers.add_parser("trajectory", help="Trajectory log utilities.")
    trajectory_subparsers = parser_trajectory.add_subparsers(
        dest="trajectory_command", required=True
    )

    parser_trajectory_tail = trajectory_subparsers.add_parser(
        "tail", help="Read latest trajectory events."
    )
    _add_json_arg(parser_trajectory_tail)
    parser_trajectory_tail.add_argument(
        "--directory",
        type=Path,
        default=Path("artifacts/trajectory"),
        help="Trajectory directory.",
    )
    parser_trajectory_tail.add_argument(
        "--limit", type=int, default=20, help="Number of trailing events."
    )
    parser_trajectory_tail.set_defaults(handler=_cmd_trajectory_tail)

    parser_trajectory_follow = trajectory_subparsers.add_parser(
        "follow", help="Follow appended trajectory events."
    )
    _add_json_arg(parser_trajectory_follow)
    parser_trajectory_follow.add_argument(
        "--directory",
        type=Path,
        default=Path("artifacts/trajectory"),
        help="Trajectory directory.",
    )
    parser_trajectory_follow.add_argument(
        "--interval-s", type=float, default=0.5, help="Polling interval."
    )
    parser_trajectory_follow.add_argument("--start-at-end", action="store_true")
    parser_trajectory_follow.set_defaults(handler=_cmd_trajectory_follow)

    parser_trajectory_action = trajectory_subparsers.add_parser(
        "action", help="Query trajectory action events from SQLite store."
    )
    trajectory_action_subparsers = parser_trajectory_action.add_subparsers(
        dest="trajectory_action_command", required=True
    )

    parser_trajectory_action_list = trajectory_action_subparsers.add_parser(
        "list", help="List action events."
    )
    _add_json_arg(parser_trajectory_action_list)
    parser_trajectory_action_list.add_argument(
        "--db-path", type=Path, required=True, help="SQLite store path."
    )
    parser_trajectory_action_list.add_argument("--run-name", help="Optional run name filter.")
    parser_trajectory_action_list.set_defaults(handler=_cmd_trajectory_action_list)

    parser_trajectory_action_show = trajectory_action_subparsers.add_parser(
        "show", help="Show one action event by index."
    )
    _add_json_arg(parser_trajectory_action_show)
    parser_trajectory_action_show.add_argument(
        "--db-path", type=Path, required=True, help="SQLite store path."
    )
    parser_trajectory_action_show.add_argument(
        "--action-idx", type=int, required=True, help="Zero-based action index."
    )
    parser_trajectory_action_show.add_argument("--run-name", help="Optional run name filter.")
    parser_trajectory_action_show.add_argument(
        "--with-signal-window",
        action="store_true",
        help="Include signal samples in the action window.",
    )
    parser_trajectory_action_show.set_defaults(handler=_cmd_trajectory_action_show)

    parser_trajectory_monitor = trajectory_subparsers.add_parser(
        "monitor", help="SQLite trajectory monitor commands."
    )
    trajectory_monitor_subparsers = parser_trajectory_monitor.add_subparsers(
        dest="trajectory_monitor_command", required=True
    )

    parser_trajectory_monitor_config = trajectory_monitor_subparsers.add_parser(
        "config", help="Manage staged trajectory monitor config."
    )
    trajectory_monitor_config_subparsers = parser_trajectory_monitor_config.add_subparsers(
        dest="trajectory_monitor_config_command", required=True
    )

    parser_trajectory_monitor_config_show = trajectory_monitor_config_subparsers.add_parser(
        "show", help="Show staged monitor configuration."
    )
    _add_json_arg(parser_trajectory_monitor_config_show)
    parser_trajectory_monitor_config_show.set_defaults(handler=_cmd_trajectory_monitor_config_show)

    parser_trajectory_monitor_config_set = trajectory_monitor_config_subparsers.add_parser(
        "set", help="Set staged monitor configuration values."
    )
    _add_json_arg(parser_trajectory_monitor_config_set)
    parser_trajectory_monitor_config_set.add_argument("--run-name", help="Run name.")
    parser_trajectory_monitor_config_set.add_argument(
        "--signals", help="Comma-separated signal labels."
    )
    parser_trajectory_monitor_config_set.add_argument(
        "--specs", help="Comma-separated spec labels."
    )
    parser_trajectory_monitor_config_set.add_argument(
        "--interval-s", type=float, help="Sample interval."
    )
    parser_trajectory_monitor_config_set.add_argument(
        "--rotate-entries", type=int, help="Samples per segment."
    )
    parser_trajectory_monitor_config_set.add_argument(
        "--action-window-s",
        type=float,
        default=None,
        help="Action window in seconds (default 2.5).",
    )
    parser_trajectory_monitor_config_set.add_argument("--directory", help="Database directory.")
    parser_trajectory_monitor_config_set.add_argument("--db-name", help="Database file name.")
    parser_trajectory_monitor_config_set.set_defaults(handler=_cmd_trajectory_monitor_config_set)

    parser_trajectory_monitor_config_clear = trajectory_monitor_config_subparsers.add_parser(
        "clear", help="Reset staged monitor config to defaults."
    )
    _add_json_arg(parser_trajectory_monitor_config_clear)
    parser_trajectory_monitor_config_clear.set_defaults(
        handler=_cmd_trajectory_monitor_config_clear
    )

    parser_trajectory_monitor_list_signals = trajectory_monitor_subparsers.add_parser(
        "list-signals", help="List available signal labels from parameter files."
    )
    _add_runtime_args(parser_trajectory_monitor_list_signals, include_trajectory=False)
    parser_trajectory_monitor_list_signals.set_defaults(
        handler=_cmd_trajectory_monitor_list_signals
    )

    parser_trajectory_monitor_list_specs = trajectory_monitor_subparsers.add_parser(
        "list-specs", help="List available spec labels from parameter files."
    )
    _add_runtime_args(parser_trajectory_monitor_list_specs, include_trajectory=False)
    parser_trajectory_monitor_list_specs.set_defaults(handler=_cmd_trajectory_monitor_list_specs)

    parser_trajectory_monitor_run = trajectory_monitor_subparsers.add_parser(
        "run", help="Run trajectory monitor into SQLite store."
    )
    _add_runtime_args(parser_trajectory_monitor_run, include_trajectory=False)
    parser_trajectory_monitor_run.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="Optional fixed iteration count for tests/dev usage.",
    )
    parser_trajectory_monitor_run.set_defaults(handler=_cmd_trajectory_monitor_run)

    parser_backend = subparsers.add_parser("backend", help="Backend command utilities.")
    backend_subparsers = parser_backend.add_subparsers(dest="backend_command", required=True)

    parser_backend_commands = backend_subparsers.add_parser(
        "commands", help="List backend commands."
    )
    _add_runtime_args(parser_backend_commands, include_trajectory=False)
    parser_backend_commands.add_argument("--match", help="Optional filter token.")
    parser_backend_commands.set_defaults(handler=_cmd_backend_commands)

    parser_doctor = subparsers.add_parser(
        "doctor", help="Connectivity and trajectory preflight checks."
    )
    _add_json_arg(parser_doctor)
    parser_doctor.add_argument("--config-file")
    parser_doctor.add_argument("--attempts", type=int, default=2)
    parser_doctor.add_argument("--command-probe", action="store_true")
    parser_doctor.set_defaults(handler=_cmd_doctor)

    return parser


def _add_runtime_args(parser: argparse.ArgumentParser, *, include_trajectory: bool) -> None:
    _add_json_arg(parser)
    parser.add_argument("--config-file", help="Runtime config YAML path.")
    parser.add_argument(
        "--parameters-file",
        default=str(DEFAULT_PARAMETERS_FILE),
        help=f"Built-in parameter YAML (default: {DEFAULT_PARAMETERS_FILE}).",
    )
    parser.add_argument(
        "--extra-parameters-file",
        default=None,
        help="Optional extra parameter YAML file.",
    )
    if include_trajectory:
        parser.add_argument("--trajectory-enable", action="store_true")
        parser.add_argument("--trajectory-dir", help="Trajectory directory override.")
        parser.add_argument("--trajectory-queue-size", type=int)
        parser.add_argument("--trajectory-max-events-per-file", type=int)


def _add_json_arg(parser: argparse.ArgumentParser) -> None:
    format_group = parser.add_mutually_exclusive_group()
    format_group.add_argument(
        "--json",
        action="store_true",
        dest="json",
        default=True,
        help="Print JSON output (default).",
    )
    format_group.add_argument(
        "--text",
        action="store_false",
        dest="json",
        help="Print text output.",
    )


def _cmd_capabilities(args: argparse.Namespace) -> int:
    settings = load_settings(config_file=args.config_file)
    resolved_extra_parameters = _resolve_extra_parameters_file(args)
    with _instrument_context(args, auto_connect=False) as instrument_ctx:
        instrument, _ = instrument_ctx
        observables = _collect_observables(instrument)

    payload: dict[str, Any] = {
        "cli": {"name": "nqctl", "version": __version__},
        "observables": observables,
        "actions": [asdict(descriptor) for descriptor in _ACTION_DESCRIPTORS],
        "policy": {
            "allow_writes": settings.safety.allow_writes,
            "dry_run": settings.safety.dry_run,
            "default_ramp_interval_s": settings.safety.default_ramp_interval_s,
        },
        "parameter_files": {
            "default": str(Path(args.parameters_file).expanduser()),
            "extra": (
                None
                if resolved_extra_parameters is None
                else str(Path(resolved_extra_parameters).expanduser())
            ),
        },
    }

    if args.include_backend_commands:
        with _instrument_context(args, auto_connect=True) as instrument_ctx:
            instrument, _ = instrument_ctx
            payload["backend_commands"] = list(
                instrument.available_backend_commands(match=args.backend_match)
            )

    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_get(args: argparse.Namespace) -> int:
    parameter_name = _normalize_parameter_name(args.parameter)
    with _instrument_context(
        args, auto_connect=True, include_parameters=(parameter_name,)
    ) as instrument_ctx:
        instrument, journal = instrument_ctx
        spec = instrument.parameter_spec(parameter_name)
        value = instrument.get_parameter_value(parameter_name)
        payload: dict[str, Any] = {
            "parameter": parameter_name,
            "value": _json_safe(value),
            "unit": spec.unit,
            "timestamp_utc": _now_utc_iso(),
        }
        if journal is not None:
            payload["trajectory"] = asdict(journal.stats())

    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_set(args: argparse.Namespace) -> int:
    parameter_name = _normalize_parameter_name(args.parameter)
    if parameter_name in {"allow_writes", "dry_run"}:
        raise ValueError(
            "'set' controls instrument parameters only, not runtime policy flags. "
            "Use `nqctl policy show` for guidance and update NANONIS_ALLOW_WRITES/NANONIS_DRY_RUN "
            "or edit config/default_runtime.yaml."
        )

    target_value = _parse_float_arg(name="value", raw_value=args.value)
    interval_s = None if args.interval_s is None else float(args.interval_s)
    if interval_s is not None and interval_s < 0:
        raise ValueError("--interval-s must be non-negative.")

    with _instrument_context(
        args, auto_connect=True, include_parameters=(parameter_name,)
    ) as instrument_ctx:
        instrument, journal = instrument_ctx
        plan = instrument.plan_parameter_single_step(
            parameter_name,
            target_value,
            reason=None,
            interval_s=interval_s,
        )
        report = None
        if not args.plan_only:
            report = instrument.set_parameter_single_step(
                parameter_name,
                target_value,
                reason=None,
                interval_s=interval_s,
            )

        payload: dict[str, Any] = {
            "parameter": parameter_name,
            "target_value": target_value,
            "plan": _json_safe(plan),
            "applied": report is not None and not report.dry_run,
            "report": None if report is None else _json_safe(report),
            "timestamp_utc": _now_utc_iso(),
        }
        if journal is not None:
            payload["trajectory"] = asdict(journal.stats())

    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_ramp(args: argparse.Namespace) -> int:
    parameter_name = _normalize_parameter_name(args.parameter)
    if parameter_name in {"allow_writes", "dry_run"}:
        raise ValueError("'ramp' controls instrument parameters only, not runtime policy flags.")

    start_value = _parse_float_arg(name="start", raw_value=args.start)
    end_value = _parse_float_arg(name="end", raw_value=args.end)
    step_value = _parse_positive_float_arg(name="step", raw_value=args.step)
    interval_s = float(args.interval_s)
    if interval_s < 0:
        raise ValueError("--interval-s must be non-negative.")

    with _instrument_context(
        args, auto_connect=True, include_parameters=(parameter_name,)
    ) as instrument_ctx:
        instrument, journal = instrument_ctx
        plan = instrument.plan_parameter_ramp(
            parameter_name,
            start_value=start_value,
            end_value=end_value,
            step_value=step_value,
            interval_s=interval_s,
            reason=None,
        )
        report = None
        if not args.plan_only:
            report = instrument.ramp_parameter(
                parameter_name,
                start_value=start_value,
                end_value=end_value,
                step_value=step_value,
                interval_s=interval_s,
                reason=None,
            )

        payload: dict[str, Any] = {
            "parameter": parameter_name,
            "start_value": start_value,
            "end_value": end_value,
            "step_value": step_value,
            "interval_s": interval_s,
            "plan": _json_safe(plan),
            "applied": report is not None and not report.dry_run,
            "report": None if report is None else _json_safe(report),
            "timestamp_utc": _now_utc_iso(),
        }
        if journal is not None:
            payload["trajectory"] = asdict(journal.stats())

    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_observables_list(args: argparse.Namespace) -> int:
    with _instrument_context(args, auto_connect=False) as instrument_ctx:
        instrument, _ = instrument_ctx
        observables = _collect_observables(instrument)

    payload = {"count": len(observables), "observables": observables}
    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_actions_list(args: argparse.Namespace) -> int:
    payload = {
        "count": len(_ACTION_DESCRIPTORS),
        "actions": [asdict(item) for item in _ACTION_DESCRIPTORS],
    }
    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_policy_show(args: argparse.Namespace) -> int:
    settings = load_settings(config_file=args.config_file)
    payload = {
        "allow_writes": settings.safety.allow_writes,
        "dry_run": settings.safety.dry_run,
        "default_ramp_interval_s": settings.safety.default_ramp_interval_s,
        "how_to_enable_live_writes": [
            "Set NANONIS_ALLOW_WRITES=1",
            "Set NANONIS_DRY_RUN=0",
            "Or edit safety.allow_writes and safety.dry_run in config/default_runtime.yaml",
        ],
    }
    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_parameters_discover(args: argparse.Namespace) -> int:
    commands = list(_discover_nanonis_spm_commands(args.match))
    if args.limit > 0:
        commands = commands[: args.limit]

    payload = {
        "source": {
            "package": "nanonis-spm",
            "version": _installed_package_version("nanonis-spm"),
        },
        "match": args.match,
        "count": len(commands),
        "commands": [asdict(item) for item in commands],
    }
    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_parameters_scaffold(args: argparse.Namespace) -> int:
    commands = list(_discover_nanonis_spm_commands(args.match))
    if args.limit > 0:
        commands = commands[: args.limit]

    payload_mapping = _build_parameter_scaffold(
        commands,
        include_non_get=bool(args.include_non_get),
        match=args.match,
    )

    output_path = Path(args.output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload_mapping, handle, sort_keys=False)

    payload = {
        "output": str(output_path),
        "parameters": len(payload_mapping.get("parameters", {})),
        "match": args.match,
        "include_non_get": bool(args.include_non_get),
    }
    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_parameters_validate(args: argparse.Namespace) -> int:
    file_path = Path(args.file).expanduser()
    specs = load_parameter_specs(file_path)
    payload = {
        "file": str(file_path),
        "valid": True,
        "count": len(specs),
        "parameters": [
            {
                "name": spec.name,
                "readable": spec.readable,
                "writable": spec.writable,
                "value_type": spec.value_type,
            }
            for spec in specs
        ],
    }
    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_trajectory_tail(args: argparse.Namespace) -> int:
    events = read_events(args.directory, limit=int(args.limit))
    payload = {
        "directory": str(args.directory),
        "count": len(events),
        "events": events,
    }
    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_trajectory_follow(args: argparse.Namespace) -> int:
    if not args.json:
        print(f"Following events from {args.directory}...")
    try:
        for event in follow_events(
            args.directory,
            poll_interval_s=float(args.interval_s),
            start_at_end=bool(args.start_at_end),
        ):
            print(json.dumps(_json_safe(event), ensure_ascii=True, sort_keys=True))
            sys.stdout.flush()
    except KeyboardInterrupt:
        return EXIT_OK

    return EXIT_OK


def _cmd_trajectory_action_list(args: argparse.Namespace) -> int:
    store = _open_trajectory_store_for_query(args.db_path)
    try:
        run_name = None if args.run_name is None else str(args.run_name).strip()
        if run_name:
            run_id = store.get_run_id_by_name(run_name)
            if run_id is None:
                raise ValueError(f"No run found for run_name '{run_name}'.")
        else:
            run_id = store.get_latest_run_id()
            if run_id is None:
                raise ValueError("No runs found in store.")

        events = store.list_action_events(run_id=run_id)
        payload_events = [
            _normalize_action_event(row, action_idx=index) for index, row in enumerate(events)
        ]
        payload = {
            "db_path": str(Path(args.db_path)),
            "run_name": run_name,
            "count": len(payload_events),
            "actions": payload_events,
        }
    finally:
        store.close()

    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_trajectory_action_show(args: argparse.Namespace) -> int:
    action_idx = int(args.action_idx)
    if action_idx < 0:
        raise ValueError("--action-idx must be non-negative.")

    store = _open_trajectory_store_for_query(args.db_path)
    try:
        run_name = None if args.run_name is None else str(args.run_name).strip()
        if run_name:
            run_id = store.get_run_id_by_name(run_name)
            if run_id is None:
                raise ValueError(f"No run found for run_name '{run_name}'.")
        else:
            run_id = store.get_latest_run_id()
            if run_id is None:
                raise ValueError("No runs found in store.")

        event = store.get_action_event_by_idx(run_id=run_id, action_idx=action_idx)
        if event is None:
            raise ValueError(
                f"No action event found for action_idx={action_idx} in run_id={run_id}."
            )

        payload: dict[str, Any] = {
            "db_path": str(Path(args.db_path)),
            "run_name": run_name,
            "action": _normalize_action_event(event, action_idx=action_idx),
        }

        if args.with_signal_window:
            signal_rows = store.list_signal_samples_in_window(
                run_id=run_id,
                dt_min_s=float(event["signal_window_start_dt_s"]),
                dt_max_s=float(event["signal_window_end_dt_s"]),
            )
            normalized_rows = [_normalize_signal_sample_row(row) for row in signal_rows]
            payload["signal_window"] = {
                "dt_min_s": float(event["signal_window_start_dt_s"]),
                "dt_max_s": float(event["signal_window_end_dt_s"]),
                "count": len(normalized_rows),
                "rows": normalized_rows,
            }
    finally:
        store.close()

    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _open_trajectory_store_for_query(db_path: str | Path) -> TrajectorySQLiteStore:
    path = Path(db_path)
    if not path.is_file():
        raise ValueError(f"Trajectory DB path does not exist: {path}")

    try:
        store = TrajectorySQLiteStore(path)
    except sqlite3.Error as exc:
        raise ValueError(f"Invalid trajectory DB path '{path}': {exc}") from exc

    try:
        required_tables = {"runs", "action_events"}
        missing_tables = sorted(required_tables - store.table_names())
        if missing_tables:
            missing_csv = ", ".join(missing_tables)
            raise ValueError(f"Trajectory DB schema missing required tables: {missing_csv}")
    except Exception:
        store.close()
        raise

    return store


def _cmd_trajectory_monitor_config_show(args: argparse.Namespace) -> int:
    staged_path = default_staged_config_path()
    config = load_staged_monitor_config(path=staged_path)
    payload = {"config_path": str(staged_path), "config": asdict(config)}
    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_trajectory_monitor_config_set(args: argparse.Namespace) -> int:
    staged_path = default_staged_config_path()
    config = load_staged_monitor_config(path=staged_path)
    updated = config

    if args.run_name is not None:
        updated = replace(updated, run_name=str(args.run_name).strip())
    if args.signals is not None:
        updated = replace(updated, signal_labels=_parse_label_csv(args.signals))
    if args.specs is not None:
        updated = replace(updated, spec_labels=_parse_label_csv(args.specs))
    if args.interval_s is not None:
        updated = replace(updated, interval_s=float(args.interval_s))
    if args.rotate_entries is not None:
        updated = replace(updated, rotate_entries=int(args.rotate_entries))
    if args.action_window_s is not None:
        updated = replace(updated, action_window_s=float(args.action_window_s))
    if args.directory is not None:
        updated = replace(updated, db_directory=str(args.directory).strip())
    if args.db_name is not None:
        updated = replace(updated, db_name=str(args.db_name).strip())

    updated.validate()
    save_staged_monitor_config(updated, path=staged_path)
    payload = {"config_path": str(staged_path), "config": asdict(updated)}
    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_trajectory_monitor_config_clear(args: argparse.Namespace) -> int:
    staged_path = default_staged_config_path()
    config = default_monitor_config(run_name="")
    save_staged_monitor_config(config, path=staged_path)
    payload = {"config_path": str(staged_path), "config": asdict(config)}
    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_trajectory_monitor_list_signals(args: argparse.Namespace) -> int:
    specs = _load_monitor_parameter_specs(args)
    payload_signals = [
        {
            "name": spec.name,
            "label": spec.label,
            "unit": spec.unit,
            "value_type": spec.value_type,
        }
        for spec in specs
        if spec.readable
    ]
    payload_signals.sort(key=lambda item: (str(item["label"]).lower(), str(item["name"])))
    payload = {"count": len(payload_signals), "signals": payload_signals}
    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_trajectory_monitor_list_specs(args: argparse.Namespace) -> int:
    specs = _load_monitor_parameter_specs(args)
    payload_specs = [
        {
            "name": spec.name,
            "label": spec.label,
            "unit": spec.unit,
            "value_type": spec.value_type,
            "vals": None if spec.vals is None else asdict(spec.vals),
        }
        for spec in specs
        if spec.readable
    ]
    payload_specs.sort(key=lambda item: (str(item["label"]).lower(), str(item["name"])))
    payload = {"count": len(payload_specs), "specs": payload_specs}
    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_trajectory_monitor_run(args: argparse.Namespace) -> int:
    staged_path = default_staged_config_path()
    config = load_staged_monitor_config(path=staged_path)
    config.validate()
    config.require_runnable()

    if args.iterations is not None and int(args.iterations) < 0:
        raise ValueError("--iterations must be non-negative.")

    db_path = Path(config.db_directory) / config.db_name
    run_start_utc = _now_utc_iso()
    store: TrajectorySQLiteStore | None = None
    run_id = None
    completed_iterations = 0
    interrupted = False

    try:
        store = TrajectorySQLiteStore(db_path)
        store.initialize_schema()

        with _instrument_context(args, auto_connect=True) as instrument_ctx:
            instrument, _ = instrument_ctx
            available_specs = tuple(spec for spec in instrument.parameter_specs() if spec.readable)
            by_label = {spec.label: spec for spec in available_specs}
            signal_specs = [
                _require_monitor_label(by_label, label, field_name="signals")
                for label in config.signal_labels
            ]
            spec_specs = [
                _require_monitor_label(by_label, label, field_name="specs")
                for label in config.spec_labels
            ]

            def poll_signals() -> dict[str, object]:
                return {
                    spec.label: instrument.get_parameter_value(spec.name) for spec in signal_specs
                }

            def poll_specs() -> dict[str, object]:
                return {
                    spec.label: instrument.get_parameter_value(spec.name) for spec in spec_specs
                }

            run_id = store.create_run(run_name=config.run_name, started_at_utc=run_start_utc)
            runner = TrajectoryMonitorRunner(
                store=store,
                run_id=run_id,
                run_start_utc=run_start_utc,
                interval_s=config.interval_s,
                rotate_entries=config.rotate_entries,
                poll_signals=poll_signals,
                poll_specs=poll_specs,
                action_window_s=config.action_window_s,
            )

            try:
                if args.iterations is not None:
                    completed_iterations = runner.run_iterations(int(args.iterations))
                else:
                    print(
                        (
                            "Trajectory monitor running "
                            f"(run_name={config.run_name}, db_path={db_path}). "
                            "Press Ctrl+C to stop."
                        ),
                        file=sys.stderr,
                        flush=True,
                    )
                    while True:
                        completed_iterations += runner.run_iterations(1)
            except KeyboardInterrupt:
                interrupted = True
                completed_iterations = max(completed_iterations, runner.sample_idx)
    finally:
        try:
            clear_staged_run_name(path=staged_path)
        finally:
            if store is not None:
                store.close()

    payload = {
        "run_id": run_id,
        "run_name": config.run_name,
        "db_path": str(db_path),
        "iterations": completed_iterations,
        "interrupted": interrupted,
    }
    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_backend_commands(args: argparse.Namespace) -> int:
    client = create_client(config_file=args.config_file)
    try:
        names = list(client.available_commands())
    finally:
        client.close()

    if args.match:
        token = str(args.match).strip().lower()
        names = [name for name in names if token in name.lower()]

    payload = {"count": len(names), "commands": sorted(names)}
    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_doctor(args: argparse.Namespace) -> int:
    settings = load_settings(config_file=args.config_file)
    report = probe_host_ports(
        host=settings.nanonis.host,
        ports=settings.nanonis.ports,
        timeout_s=settings.nanonis.timeout_s,
        attempts=int(args.attempts),
        backend=settings.nanonis.backend,
        command_probe=bool(args.command_probe),
    )

    payload: dict[str, Any] = {
        "config": {
            "host": settings.nanonis.host,
            "ports": list(settings.nanonis.ports),
            "timeout_s": settings.nanonis.timeout_s,
            "backend": settings.nanonis.backend,
            "allow_writes": settings.safety.allow_writes,
            "dry_run": settings.safety.dry_run,
            "trajectory_enabled": settings.trajectory.enabled,
            "trajectory_directory": settings.trajectory.directory,
        },
        "probe": report_to_dict(report),
        "trajectory_directory_check": _trajectory_directory_check(settings.trajectory.directory),
    }

    _print_payload(payload, as_json=args.json)
    return EXIT_OK if report.candidate_ports else EXIT_FAILED


@contextmanager
def _instrument_context(
    args: argparse.Namespace,
    *,
    auto_connect: bool,
    include_parameters: Sequence[str] | None = None,
) -> Iterator[tuple[Any, TrajectoryJournal | None]]:
    instrument_cls = _load_instrument_class()
    settings = load_settings(config_file=args.config_file)

    trajectory_journal: TrajectoryJournal | None = None
    if bool(getattr(args, "trajectory_enable", False)):
        trajectory_directory = (
            str(args.trajectory_dir).strip()
            if getattr(args, "trajectory_dir", None)
            else settings.trajectory.directory
        )
        queue_size = (
            int(args.trajectory_queue_size)
            if getattr(args, "trajectory_queue_size", None) is not None
            else settings.trajectory.queue_size
        )
        max_events_per_file = (
            int(args.trajectory_max_events_per_file)
            if getattr(args, "trajectory_max_events_per_file", None) is not None
            else settings.trajectory.max_events_per_file
        )
        trajectory_journal = TrajectoryJournal(
            directory=trajectory_directory,
            queue_size=queue_size,
            max_events_per_file=max_events_per_file,
        )
        trajectory_journal.start()

    instrument = None
    try:
        instrument = instrument_cls(
            name=f"nqctl_{int(time.time() * 1000)}",
            config_file=args.config_file,
            parameters_file=args.parameters_file,
            extra_parameters_file=_resolve_extra_parameters_file(args),
            include_parameters=include_parameters,
            trajectory_journal=trajectory_journal,
            auto_connect=auto_connect,
        )
        yield instrument, trajectory_journal
    finally:
        if instrument is not None:
            instrument.close()
        if trajectory_journal is not None:
            trajectory_journal.close()


def _load_instrument_class() -> Any:
    try:
        from nanonis_qcodes_controller.qcodes_driver import QcodesNanonisSTM
    except ModuleNotFoundError as exc:
        if exc.name is not None and exc.name.startswith("qcodes"):
            raise ValueError(
                "qcodes is not installed. Install optional extra with: "
                "python -m pip install -e .[qcodes]"
            ) from exc
        raise

    return QcodesNanonisSTM


def _collect_observables(instrument: Any) -> list[dict[str, Any]]:
    observables: list[dict[str, Any]] = []
    for spec in instrument.parameter_specs():
        observables.append(
            {
                "name": spec.name,
                "label": spec.label,
                "unit": spec.unit,
                "readable": spec.readable,
                "writable": spec.writable,
                "value_type": spec.value_type,
                "has_ramp": bool(spec.safety is not None and spec.safety.ramp_enabled),
            }
        )
    return observables


def _parse_label_csv(raw_labels: str) -> tuple[str, ...]:
    labels = [token.strip() for token in str(raw_labels).split(",")]
    return tuple(label for label in labels if label)


def _load_monitor_parameter_specs(args: argparse.Namespace) -> tuple[Any, ...]:
    merged_specs = load_parameter_spec_bundle(
        default_parameters_file=args.parameters_file,
        extra_parameters_file=_resolve_extra_parameters_file(args),
    )
    return tuple(merged_specs[name] for name in sorted(merged_specs))


def _resolve_extra_parameters_file(args: argparse.Namespace) -> str | None:
    explicit_extra = getattr(args, "extra_parameters_file", None)
    if explicit_extra is not None:
        text = str(explicit_extra).strip()
        if text:
            return text

    parameters_file = Path(
        str(getattr(args, "parameters_file", DEFAULT_PARAMETERS_FILE))
    ).expanduser()
    default_parameters_file = DEFAULT_PARAMETERS_FILE.expanduser()
    default_extra_file = DEFAULT_EXTRA_PARAMETERS_FILE.expanduser()
    if parameters_file == default_parameters_file and default_extra_file.is_file():
        return str(default_extra_file)
    return None


def _require_monitor_label(by_label: Mapping[str, Any], label: str, *, field_name: str) -> Any:
    key = str(label)
    spec = by_label.get(key)
    if spec is None:
        raise ValueError(f"Unknown monitor {field_name} label: {key}")
    return spec


def _normalize_parameter_name(raw_name: str) -> str:
    name = str(raw_name).strip()
    if not name:
        raise ValueError("Parameter name cannot be empty.")
    return name


def _parse_float_arg(*, name: str, raw_value: str) -> float:
    try:
        return float(str(raw_value).strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric.") from exc


def _parse_positive_float_arg(*, name: str, raw_value: str) -> float:
    value = _parse_float_arg(name=name, raw_value=raw_value)
    if value <= 0:
        raise ValueError(f"{name} must be positive.")
    return value


def _build_ramp_targets(*, start: float, end: float, step: float) -> tuple[float, ...]:
    start_value = float(start)
    end_value = float(end)
    step_value = float(step)
    if step_value <= 0:
        raise ValueError("step must be positive.")

    if math.isclose(start_value, end_value, rel_tol=0.0, abs_tol=1e-15):
        return (end_value,)

    direction = 1.0 if end_value > start_value else -1.0
    signed_step = step_value * direction
    points: list[float] = []
    current = start_value
    for _ in range(1_000_000):
        if (direction > 0 and current >= end_value) or (direction < 0 and current <= end_value):
            points.append(end_value)
            break
        points.append(current)
        current += signed_step
    else:
        raise ValueError("Ramp target generation exceeded safe iteration limit.")

    deduped: list[float] = []
    for value in points:
        if not deduped or not math.isclose(value, deduped[-1], rel_tol=0.0, abs_tol=1e-15):
            deduped.append(value)
    return tuple(deduped)


def _discover_nanonis_spm_commands(match_pattern: str) -> tuple[DiscoveredCommand, ...]:
    try:
        nanonis_spm = importlib.import_module("nanonis_spm")
    except ModuleNotFoundError as exc:
        raise ValueError(
            "nanonis_spm is not installed. Install with: python -m pip install nanonis-spm"
        ) from exc

    compiled_pattern = re.compile(match_pattern, re.IGNORECASE) if match_pattern else None

    discovered: list[DiscoveredCommand] = []
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
        discovered.append(DiscoveredCommand(command=name, arguments=arguments))

    discovered.sort(key=lambda entry: entry.command)
    return tuple(discovered)


def _build_parameter_scaffold(
    command_specs: Sequence[DiscoveredCommand],
    *,
    include_non_get: bool,
    match: str,
) -> dict[str, Any]:
    selected = (
        list(command_specs)
        if include_non_get
        else [spec for spec in command_specs if spec.command.endswith("Get")]
    )

    parameters: dict[str, Any] = {}
    for spec in selected:
        parameter_name = _derive_parameter_name(spec.command)
        parameter_entry: dict[str, Any] = {
            "label": parameter_name,
            "unit": "",
            "type": _guess_value_type(spec.command),
            "get_cmd": {
                "command": spec.command,
                "payload_index": 0,
                "args": {argument_name: None for argument_name in spec.arguments},
            },
            "set_cmd": False,
        }
        parameters[parameter_name] = parameter_entry

    return {
        "version": 1,
        "defaults": {
            "snapshot_value": True,
            "ramp_default_interval_s": 0.05,
        },
        "meta": {
            "generated_by": "nqctl parameters scaffold",
            "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source_package": f"nanonis-spm/{_installed_package_version('nanonis-spm')}",
            "match": match,
            "include_non_get": include_non_get,
        },
        "parameters": parameters,
    }


def _derive_parameter_name(command_name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9]+", "_", command_name).strip("_").lower()
    if name.endswith("_get"):
        name = name[:-4]
    elif name.endswith("get"):
        name = name[:-3]
    if not name:
        raise ValueError(f"Cannot derive parameter name from command '{command_name}'.")
    return name


def _guess_value_type(command_name: str) -> str:
    lowered = command_name.lower()
    if "onoffget" in lowered:
        return "bool"
    if "statusget" in lowered:
        return "int"
    return "float"


def _installed_package_version(package_name: str) -> str:
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _trajectory_directory_check(directory: str) -> dict[str, Any]:
    root = Path(directory)
    result: dict[str, Any] = {
        "directory": str(root),
        "exists": False,
        "writable": False,
        "error": None,
    }

    try:
        root.mkdir(parents=True, exist_ok=True)
        result["exists"] = root.exists()

        marker = root / ".doctor_write_test"
        marker.write_text("ok", encoding="utf-8")
        marker.unlink(missing_ok=True)
        result["writable"] = True
    except Exception as exc:  # pragma: no cover
        result["error"] = f"{type(exc).__name__}: {exc}"

    return result


def _emit_error(
    args: argparse.Namespace,
    *,
    exit_code: int,
    message: str,
    error_type: str,
) -> int:
    payload = {
        "ok": False,
        "error": {"type": error_type, "message": message},
        "exit_code": exit_code,
    }
    if bool(getattr(args, "json", False)):
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(message, file=sys.stderr)
    return exit_code


def _print_payload(payload: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(_json_safe(dict(payload)), indent=2, sort_keys=True))
        return

    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            print(f"{key}: {json.dumps(_json_safe(value), ensure_ascii=True, sort_keys=True)}")
        else:
            print(f"{key}: {value}")


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]

    item_method = getattr(value, "item", None)
    if callable(item_method):
        try:
            return _json_safe(item_method())
        except Exception:
            return str(value)

    return str(value)


def _normalize_action_event(row: Mapping[str, Any], *, action_idx: int) -> dict[str, Any]:
    payload = dict(row)
    payload["action_idx"] = int(action_idx)
    payload["old_value_json"] = _try_parse_json_text(payload.get("old_value_json"))
    payload["new_value_json"] = _try_parse_json_text(payload.get("new_value_json"))
    return payload


def _normalize_signal_sample_row(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload["values_json"] = _try_parse_json_text(payload.get("values_json"))
    return payload


def _try_parse_json_text(value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
