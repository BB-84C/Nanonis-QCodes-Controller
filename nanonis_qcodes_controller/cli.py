from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import inspect
import json
import math
import re
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
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
from nanonis_qcodes_controller.qcodes_driver.extensions import load_scalar_parameter_specs
from nanonis_qcodes_controller.safety import PolicyViolation
from nanonis_qcodes_controller.trajectory import TrajectoryJournal, follow_events, read_events
from nanonis_qcodes_controller.version import __version__

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_POLICY_BLOCKED = 2
EXIT_INVALID_INPUT = 3
EXIT_COMMAND_UNAVAILABLE = 4
EXIT_CONNECTION_FAILED = 5

_OBSERVABLE_ALIASES = {
    "bias": "bias_v",
    "bias_v": "bias_v",
    "current": "current_a",
    "current_a": "current_a",
    "setpoint": "zctrl_setpoint_a",
    "setpoint_a": "zctrl_setpoint_a",
    "zctrl_setpoint_a": "zctrl_setpoint_a",
    "z": "zctrl_z_m",
    "zctrl_z_m": "zctrl_z_m",
}

_SET_CHANNEL_ALIASES = {
    "bias": "bias_v",
    "bias_v": "bias_v",
    "setpoint": "setpoint_a",
    "setpoint_a": "setpoint_a",
    "zctrl_setpoint_a": "setpoint_a",
}


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
        description="Read a single observable.",
        command_template="nqctl get <observable> [--json]",
        arguments=("observable",),
    ),
    ActionDescriptor(
        name="set",
        safety="guarded",
        description="Apply guarded single-step write for a supported scalar channel.",
        command_template="nqctl set <channel> <value> [--confirmed] [--reason TEXT] [--json]",
        arguments=("channel", "value"),
    ),
    ActionDescriptor(
        name="ramp",
        safety="guarded",
        description="Apply explicit ramp with start/end/step/interval.",
        command_template=(
            "nqctl ramp <channel> <start> <end> <step> --interval-s 0.05 "
            "[--confirmed] [--reason TEXT] [--json]"
        ),
        arguments=("channel", "start", "end", "step", "interval_s"),
    ),
    ActionDescriptor(
        name="start_scan",
        safety="raw",
        description="Start scan action via QCodes driver helper.",
        command_template='nqctl backend call Scan_Action --args-json \'{"Scan_action":0,"Scan_direction":0}\' --unsafe-raw-call --json',
        arguments=("direction",),
    ),
    ActionDescriptor(
        name="stop_scan",
        safety="raw",
        description="Stop scan action via QCodes driver helper.",
        command_template='nqctl backend call Scan_Action --args-json \'{"Scan_action":1,"Scan_direction":0}\' --unsafe-raw-call --json',
        arguments=("direction",),
    ),
    ActionDescriptor(
        name="wait_end_of_scan",
        safety="readonly",
        description="Wait until scan ends and return status/path.",
        command_template="nqctl backend call Scan_WaitEndOfScan --args-json '{\"Timeout_ms\":-1}' --unsafe-raw-call --json",
        arguments=("timeout_ms",),
    ),
    ActionDescriptor(
        name="extensions_discover",
        safety="readonly",
        description="Discover backend commands for extension-file authoring.",
        command_template="nqctl extensions discover --match LockIn --json",
        arguments=("match",),
    ),
    ActionDescriptor(
        name="extensions_scaffold",
        safety="readonly",
        description="Scaffold extension file from discovered commands.",
        command_template="nqctl extensions scaffold --match LockIn --output config/lockin_parameters.yaml --json",
        arguments=("match", "output"),
    ),
    ActionDescriptor(
        name="trajectory_tail",
        safety="readonly",
        description="Tail events from non-blocking trajectory logs.",
        command_template="nqctl trajectory tail --directory artifacts/trajectory --limit 20 --json",
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
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return EXIT_INVALID_INPUT

    try:
        return int(handler(args))
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
    except Exception as exc:  # pragma: no cover - defensive fallback
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
            "Use atomic commands (capabilities/get/set/ramp/extensions/trajectory) and keep\n"
            "multi-step sequencing in your orchestration agent."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Quick start:\n"
            "  nqctl capabilities --json\n"
            "  nqctl get bias_v --json\n"
            '  nqctl set bias_v 0.12 --confirmed --reason "test" --json\n'
            "  nqctl ramp bias_v 0.05 0.2 0.01 --interval-s 0.2 --confirmed --json\n"
            "\n"
            "Help shortcuts:\n"
            "  nqctl -help\n"
            "  nqctl -help observables\n"
            "  nqctl -help extensions\n"
            "  nqctl -help extensions discover\n"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    parser_capabilities = subparsers.add_parser(
        "capabilities",
        help="Show available observables/actions and policy summary.",
        description="Discover orchestration surface: observables, actions, and write policy.",
    )
    _add_runtime_args(parser_capabilities, include_trajectory=False)
    parser_capabilities.add_argument(
        "--include-backend-commands",
        action="store_true",
        help="Connect and include backend command names.",
    )
    parser_capabilities.add_argument(
        "--backend-match",
        help="Optional filter token for backend commands.",
    )
    parser_capabilities.set_defaults(handler=_cmd_capabilities)

    parser_get = subparsers.add_parser(
        "get",
        help="Read a single observable value.",
        description=(
            "Read one observable (built-in or extension-file derived).\n"
            "Examples: bias_v, current_a, zctrl_setpoint_a"
        ),
    )
    _add_runtime_args(parser_get, include_trajectory=True)
    parser_get.add_argument("observable", help="Observable name (example: bias_v, current_a).")
    parser_get.set_defaults(handler=_cmd_get)

    parser_set = subparsers.add_parser(
        "set",
        help="Apply guarded scalar write.",
        description=(
            "Apply guarded single-step write for allowed channels only.\n"
            "Supported channels: bias_v, setpoint_a\n"
            "This command does not ramp automatically. Use `nqctl ramp` for multi-step moves.\n"
            "This command does not change policy flags like allow_writes/dry_run."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            '  nqctl set bias_v 0.15 --confirmed --reason "single step" --json\n'
            "  nqctl set setpoint_a 8e-11 --plan-only --json\n"
            "\n"
            "For multi-step moves use ramp:\n"
            "  nqctl ramp bias_v 0.10 0.30 0.01 --interval-s 0.10 --confirmed --json\n"
            "\n"
            "Enable live writes (outside CLI):\n"
            "  1) Set NANONIS_ALLOW_WRITES=1\n"
            "  2) Set NANONIS_DRY_RUN=0\n"
            "  3) Or edit safety.allow_writes / safety.dry_run in config/default.yaml"
        ),
    )
    _add_runtime_args(parser_set, include_trajectory=True)
    parser_set.add_argument("channel", help="Channel name (bias_v or setpoint_a).")
    parser_set.add_argument("value", help="Target numeric value.")
    parser_set.add_argument(
        "--confirmed",
        action="store_true",
        help="Mark this write request as confirmed.",
    )
    parser_set.add_argument("--reason", help="Optional reason for audit trail.")
    parser_set.add_argument(
        "--plan-only",
        action="store_true",
        help="Show write plan only, do not execute set command.",
    )
    parser_set.set_defaults(handler=_cmd_set)

    parser_ramp = subparsers.add_parser(
        "ramp",
        help="Apply explicit guarded ramp.",
        description=(
            "Apply explicit guarded ramp with user-defined start/end/step/interval.\n"
            "The CLI does not auto-ramp in `set`; use this command for stepped moves."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            '  nqctl ramp bias_v 0.10 0.25 0.01 --interval-s 0.10 --confirmed --reason "spectroscopy" --json\n'
            "  nqctl ramp setpoint_a 5e-11 1e-10 5e-12 --interval-s 0.05 --plan-only --json"
        ),
    )
    _add_runtime_args(parser_ramp, include_trajectory=True)
    parser_ramp.add_argument("channel", help="Channel name (bias_v or setpoint_a).")
    parser_ramp.add_argument("start", help="Ramp start value.")
    parser_ramp.add_argument("end", help="Ramp end value.")
    parser_ramp.add_argument("step", help="Positive ramp step size.")
    parser_ramp.add_argument(
        "--interval-s",
        type=float,
        required=True,
        help="Delay between applied ramp points in seconds (>= 0).",
    )
    parser_ramp.add_argument(
        "--confirmed",
        action="store_true",
        help="Mark this write request as confirmed.",
    )
    parser_ramp.add_argument("--reason", help="Optional reason for audit trail.")
    parser_ramp.add_argument(
        "--plan-only",
        action="store_true",
        help="Show ramp plan only, do not execute set commands.",
    )
    parser_ramp.set_defaults(handler=_cmd_ramp)

    parser_observables = subparsers.add_parser(
        "observables",
        help="Observable metadata commands.",
        description="Commands for listing observable channels and metadata.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  nqctl observables list --json\n"
            "  nqctl observables list --extensions-file config/lockin_parameters.yaml --json"
        ),
    )
    observables_subparsers = parser_observables.add_subparsers(
        dest="observables_command", required=True
    )
    parser_observables_list = observables_subparsers.add_parser("list", help="List observables.")
    _add_runtime_args(parser_observables_list, include_trajectory=False)
    parser_observables_list.set_defaults(handler=_cmd_observables_list)

    parser_actions = subparsers.add_parser("actions", help="Action metadata commands.")
    actions_subparsers = parser_actions.add_subparsers(dest="actions_command", required=True)
    parser_actions_list = actions_subparsers.add_parser("list", help="List actions.")
    _add_json_arg(parser_actions_list)
    parser_actions_list.set_defaults(handler=_cmd_actions_list)

    parser_extensions = subparsers.add_parser(
        "extensions",
        aliases=("manifest",),
        help="Extension-file helper commands (alias: manifest).",
        description=(
            "Extension files map backend read commands to dynamic read-only observables.\n"
            "Recommended flow: discover -> scaffold -> validate"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  nqctl extensions discover --match LockIn --json\n"
            "  nqctl extensions scaffold --match LockIn --output config/lockin_parameters.yaml --json\n"
            "  nqctl extensions validate --file config/lockin_parameters.yaml --json"
        ),
    )
    extensions_subparsers = parser_extensions.add_subparsers(
        dest="extensions_command", required=True
    )

    parser_manifest_discover = extensions_subparsers.add_parser(
        "discover",
        help="Discover backend command names/signatures.",
        description="Discover command names and argument signatures from installed nanonis_spm.",
    )
    _add_json_arg(parser_manifest_discover)
    parser_manifest_discover.add_argument(
        "--match",
        default="",
        help="Case-insensitive regex filter token (example: LockIn).",
    )
    parser_manifest_discover.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum commands to return (0 means all).",
    )
    parser_manifest_discover.set_defaults(handler=_cmd_manifest_discover)

    parser_manifest_scaffold = extensions_subparsers.add_parser(
        "scaffold",
        help="Scaffold extension-file YAML.",
        description="Generate an extension file template from discovered commands.",
    )
    _add_json_arg(parser_manifest_scaffold)
    parser_manifest_scaffold.add_argument(
        "--match",
        default="",
        help="Case-insensitive regex filter token.",
    )
    parser_manifest_scaffold.add_argument(
        "--output",
        type=Path,
        default=Path("config/extra_parameters.generated.yaml"),
        help="Output path for generated extension-file YAML.",
    )
    parser_manifest_scaffold.add_argument(
        "--include-non-get",
        action="store_true",
        help="Include non-Get commands in template.",
    )
    parser_manifest_scaffold.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum discovered commands to use (0 means all).",
    )
    parser_manifest_scaffold.set_defaults(handler=_cmd_manifest_scaffold)

    parser_manifest_validate = extensions_subparsers.add_parser(
        "validate",
        help="Validate extension-file schema.",
        description="Validate extension-file YAML against dynamic parameter schema.",
    )
    _add_json_arg(parser_manifest_validate)
    parser_manifest_validate.add_argument(
        "--file",
        type=Path,
        required=True,
        help="Extension-file YAML path.",
    )
    parser_manifest_validate.set_defaults(handler=_cmd_manifest_validate)

    parser_policy = subparsers.add_parser(
        "policy",
        help="Policy metadata and write-enable guidance.",
        description="Show effective safety policy and how to enable guarded writes.",
    )
    policy_subparsers = parser_policy.add_subparsers(dest="policy_command", required=True)
    parser_policy_show = policy_subparsers.add_parser(
        "show",
        help="Show active policy values and guidance.",
    )
    _add_json_arg(parser_policy_show)
    parser_policy_show.add_argument("--config-file")
    parser_policy_show.set_defaults(handler=_cmd_policy_show)

    parser_trajectory = subparsers.add_parser("trajectory", help="Trajectory log utilities.")
    trajectory_subparsers = parser_trajectory.add_subparsers(
        dest="trajectory_command", required=True
    )

    parser_trajectory_tail = trajectory_subparsers.add_parser(
        "tail",
        help="Read latest trajectory events.",
    )
    _add_json_arg(parser_trajectory_tail)
    parser_trajectory_tail.add_argument(
        "--directory",
        type=Path,
        default=Path("artifacts/trajectory"),
        help="Trajectory directory.",
    )
    parser_trajectory_tail.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Number of trailing events.",
    )
    parser_trajectory_tail.set_defaults(handler=_cmd_trajectory_tail)

    parser_trajectory_follow = trajectory_subparsers.add_parser(
        "follow",
        help="Follow appended trajectory events.",
    )
    _add_json_arg(parser_trajectory_follow)
    parser_trajectory_follow.add_argument(
        "--directory",
        type=Path,
        default=Path("artifacts/trajectory"),
        help="Trajectory directory.",
    )
    parser_trajectory_follow.add_argument(
        "--interval-s",
        type=float,
        default=0.5,
        help="Polling interval in seconds.",
    )
    parser_trajectory_follow.add_argument(
        "--start-at-end",
        action="store_true",
        help="Ignore historical events and stream only new ones.",
    )
    parser_trajectory_follow.set_defaults(handler=_cmd_trajectory_follow)

    parser_backend = subparsers.add_parser("backend", help="Backend command utilities.")
    backend_subparsers = parser_backend.add_subparsers(dest="backend_command", required=True)

    parser_backend_commands = backend_subparsers.add_parser(
        "commands",
        help="List commands available from active backend session.",
    )
    _add_runtime_args(parser_backend_commands, include_trajectory=False)
    parser_backend_commands.add_argument(
        "--match",
        help="Filter command names containing token (case-insensitive).",
    )
    parser_backend_commands.set_defaults(handler=_cmd_backend_commands)

    parser_backend_call = backend_subparsers.add_parser(
        "call",
        help="Call backend command directly (unsafe path).",
    )
    _add_runtime_args(parser_backend_call, include_trajectory=False)
    parser_backend_call.add_argument("command_name", help="Backend command to execute.")
    parser_backend_call.add_argument(
        "--args-json",
        default="{}",
        help="JSON object for command arguments.",
    )
    parser_backend_call.add_argument(
        "--unsafe-raw-call",
        action="store_true",
        help="Required acknowledgement for raw backend call.",
    )
    parser_backend_call.set_defaults(handler=_cmd_backend_call)

    parser_doctor = subparsers.add_parser(
        "doctor", help="Connectivity and trajectory preflight checks."
    )
    _add_json_arg(parser_doctor)
    parser_doctor.add_argument("--config-file")
    parser_doctor.add_argument(
        "--attempts",
        type=int,
        default=2,
        help="TCP attempts per port.",
    )
    parser_doctor.add_argument(
        "--command-probe",
        action="store_true",
        help="Enable backend command probe during doctor check.",
    )
    parser_doctor.set_defaults(handler=_cmd_doctor)

    return parser


def _add_runtime_args(parser: argparse.ArgumentParser, *, include_trajectory: bool) -> None:
    _add_json_arg(parser)
    parser.add_argument(
        "--config-file",
        help="Optional YAML config path (defaults to env/config defaults).",
    )
    parser.add_argument(
        "--extensions-file",
        dest="extensions_file",
        help="Optional extension-file YAML for dynamic observables.",
    )
    parser.add_argument(
        "--manifest",
        dest="extensions_file",
        help=argparse.SUPPRESS,
    )
    if include_trajectory:
        parser.add_argument(
            "--trajectory-enable",
            action="store_true",
            help="Enable non-blocking trajectory logging for this command.",
        )
        parser.add_argument(
            "--trajectory-dir",
            help="Trajectory directory override.",
        )
        parser.add_argument(
            "--trajectory-queue-size",
            type=int,
            help="Trajectory queue size override.",
        )
        parser.add_argument(
            "--trajectory-max-events-per-file",
            type=int,
            help="Trajectory segment rotation threshold.",
        )


def _add_json_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Print JSON output.")


def _cmd_capabilities(args: argparse.Namespace) -> int:
    settings = load_settings(config_file=args.config_file)
    with _instrument_context(args, auto_connect=False) as instrument_ctx:
        instrument, _ = instrument_ctx
        observables = _collect_observables(instrument)

    payload: dict[str, Any] = {
        "cli": {
            "name": "nqctl",
            "version": __version__,
        },
        "observables": observables,
        "actions": [asdict(descriptor) for descriptor in _ACTION_DESCRIPTORS],
        "policy": {
            "allow_writes": settings.safety.allow_writes,
            "dry_run": settings.safety.dry_run,
            "limits": {
                channel: {
                    "min": limit.min,
                    "max": limit.max,
                    "max_step": limit.max_step,
                    "max_slew_per_s": limit.max_slew_per_s,
                    "cooldown_s": limit.cooldown_s,
                    "require_confirmation": limit.require_confirmation,
                    "ramp_interval_s": limit.ramp_interval_s,
                }
                for channel, limit in sorted(settings.safety.limits.items())
            },
        },
        "extensions": {
            "path": args.extensions_file,
            "loaded": bool(args.extensions_file),
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
    observable_name = _resolve_observable_name(args.observable)
    with _instrument_context(args, auto_connect=True) as instrument_ctx:
        instrument, journal = instrument_ctx
        parameter = instrument.parameters.get(observable_name)
        if parameter is None:
            raise ValueError(f"Unknown observable: {args.observable}")

        value = parameter.get()
        payload: dict[str, Any] = {
            "observable": observable_name,
            "value": _json_safe(value),
            "unit": parameter.unit,
            "timestamp_s": time.time(),
        }
        if journal is not None:
            payload["trajectory"] = asdict(journal.stats())

    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_set(args: argparse.Namespace) -> int:
    channel = _resolve_set_channel(args.channel)
    target_value = _parse_set_target_value(channel=channel, raw_value=args.value)

    with _instrument_context(args, auto_connect=True) as instrument_ctx:
        instrument, journal = instrument_ctx

        if channel == "bias_v":
            plan = instrument.plan_bias_v_set_single_step(
                target_value,
                confirmed=bool(args.confirmed),
                reason=args.reason,
            )
            report = None
            if not args.plan_only:
                report = instrument.set_bias_v_single_step_guarded(
                    target_value,
                    confirmed=bool(args.confirmed),
                    reason=args.reason,
                )
        else:
            plan = instrument.plan_zctrl_setpoint_a_set_single_step(
                target_value,
                confirmed=bool(args.confirmed),
                reason=args.reason,
            )
            report = None
            if not args.plan_only:
                report = instrument.set_zctrl_setpoint_a_single_step_guarded(
                    target_value,
                    confirmed=bool(args.confirmed),
                    reason=args.reason,
                )

        payload: dict[str, Any] = {
            "channel": channel,
            "target_value": target_value,
            "plan": _json_safe(plan),
            "applied": report is not None,
            "report": None if report is None else _json_safe(report),
            "timestamp_s": time.time(),
        }
        if journal is not None:
            payload["trajectory"] = asdict(journal.stats())

    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_ramp(args: argparse.Namespace) -> int:
    channel = _resolve_set_channel(args.channel)
    start_value = _parse_set_target_value(channel=channel, raw_value=args.start)
    end_value = _parse_set_target_value(channel=channel, raw_value=args.end)
    step_value = _parse_positive_value(name="step", raw_value=args.step)
    interval_s = float(args.interval_s)
    if interval_s < 0:
        raise ValueError("--interval-s must be non-negative.")

    targets = _build_ramp_targets(start=start_value, end=end_value, step=step_value)

    with _instrument_context(args, auto_connect=True) as instrument_ctx:
        instrument, journal = instrument_ctx

        plans = []
        reports = []
        for target in targets:
            if channel == "bias_v":
                plan = instrument.plan_bias_v_set_single_step(
                    target,
                    confirmed=bool(args.confirmed),
                    reason=args.reason,
                    interval_s=interval_s,
                )
            else:
                plan = instrument.plan_zctrl_setpoint_a_set_single_step(
                    target,
                    confirmed=bool(args.confirmed),
                    reason=args.reason,
                    interval_s=interval_s,
                )
            plans.append(plan)

        if not args.plan_only:
            for step_index, target in enumerate(targets):
                if channel == "bias_v":
                    report = instrument.set_bias_v_single_step_guarded(
                        target,
                        confirmed=bool(args.confirmed),
                        reason=args.reason,
                        interval_s=interval_s,
                    )
                else:
                    report = instrument.set_zctrl_setpoint_a_single_step_guarded(
                        target,
                        confirmed=bool(args.confirmed),
                        reason=args.reason,
                        interval_s=interval_s,
                    )
                reports.append(report)

                if step_index < len(targets) - 1 and interval_s > 0 and not report.dry_run:
                    time.sleep(interval_s)

        payload: dict[str, Any] = {
            "channel": channel,
            "start_value": start_value,
            "end_value": end_value,
            "step_value": step_value,
            "interval_s": interval_s,
            "targets": list(targets),
            "plan_count": len(plans),
            "plans": [_json_safe(plan) for plan in plans],
            "applied": any(not report.dry_run for report in reports),
            "reports": [_json_safe(report) for report in reports],
            "timestamp_s": time.time(),
        }
        if journal is not None:
            payload["trajectory"] = asdict(journal.stats())

    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_observables_list(args: argparse.Namespace) -> int:
    with _instrument_context(args, auto_connect=False) as instrument_ctx:
        instrument, _ = instrument_ctx
        observables = _collect_observables(instrument)

    payload = {
        "count": len(observables),
        "observables": observables,
    }
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
        "limits": {
            channel: {
                "min": limit.min,
                "max": limit.max,
                "max_step": limit.max_step,
                "max_slew_per_s": limit.max_slew_per_s,
                "cooldown_s": limit.cooldown_s,
                "require_confirmation": limit.require_confirmation,
                "ramp_interval_s": limit.ramp_interval_s,
            }
            for channel, limit in sorted(settings.safety.limits.items())
        },
        "how_to_enable_live_writes": [
            "Set environment variable NANONIS_ALLOW_WRITES=1",
            "Set environment variable NANONIS_DRY_RUN=0",
            "Or edit safety.allow_writes and safety.dry_run in config/default.yaml",
        ],
    }
    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_manifest_discover(args: argparse.Namespace) -> int:
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


def _cmd_manifest_scaffold(args: argparse.Namespace) -> int:
    commands = list(_discover_nanonis_spm_commands(args.match))
    if args.limit > 0:
        commands = commands[: args.limit]

    manifest = _build_manifest_from_commands(
        commands,
        include_non_get=bool(args.include_non_get),
        match=args.match,
    )

    output_path = Path(args.output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(manifest, handle, sort_keys=False)

    payload = {
        "output": str(output_path),
        "parameters": len(manifest.get("parameters", [])),
        "match": args.match,
        "include_non_get": bool(args.include_non_get),
    }
    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_manifest_validate(args: argparse.Namespace) -> int:
    file_path = Path(args.file).expanduser()
    specs = load_scalar_parameter_specs(file_path)

    payload = {
        "file": str(file_path),
        "valid": True,
        "count": len(specs),
        "parameters": [
            {
                "name": spec.name,
                "command": spec.command,
                "value_type": spec.value_type,
                "payload_index": spec.payload_index,
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


def _cmd_backend_commands(args: argparse.Namespace) -> int:
    client = create_client(config_file=args.config_file)
    try:
        names = list(client.available_commands())
    finally:
        client.close()

    if args.match:
        token = str(args.match).strip().lower()
        names = [name for name in names if token in name.lower()]

    payload = {
        "count": len(names),
        "commands": sorted(names),
    }
    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_backend_call(args: argparse.Namespace) -> int:
    if not args.unsafe_raw_call:
        raise PolicyViolation("Raw backend call requires --unsafe-raw-call acknowledgement.")

    args_mapping = _parse_args_json(args.args_json)
    client = create_client(config_file=args.config_file)
    try:
        response = client.call(args.command_name, args=args_mapping)
    finally:
        client.close()

    payload = {
        "command": args.command_name,
        "args": args_mapping,
        "response": response,
    }
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

    instrument_name = f"nqctl_{int(time.time() * 1000)}"
    instrument = None
    try:
        instrument = instrument_cls(
            name=instrument_name,
            config_file=args.config_file,
            extra_parameters_manifest=getattr(args, "extensions_file", None),
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
    dynamic_names = {spec.name for spec in instrument.dynamic_parameter_specs()}
    observables: list[dict[str, Any]] = []
    for name, parameter in sorted(instrument.parameters.items()):
        if name.upper() == "IDN":
            continue
        observables.append(
            {
                "name": name,
                "label": str(parameter.label),
                "unit": str(parameter.unit or ""),
                "gettable": bool(parameter.gettable),
                "settable": bool(parameter.settable),
                "source": "extension_file" if name in dynamic_names else "built_in",
            }
        )
    return observables


def _resolve_observable_name(raw_name: str) -> str:
    normalized = str(raw_name).strip().lower()
    if not normalized:
        raise ValueError("Observable name cannot be empty.")
    return _OBSERVABLE_ALIASES.get(normalized, normalized)


def _resolve_set_channel(raw_name: str) -> str:
    normalized = str(raw_name).strip().lower()
    if not normalized:
        raise ValueError("Set channel cannot be empty.")

    if normalized in {"allow_writes", "dry_run"}:
        raise ValueError(
            "'set' controls instrument channels only, not policy flags. "
            "Use `nqctl policy show` for guidance and set NANONIS_ALLOW_WRITES/NANONIS_DRY_RUN "
            "or edit config/default.yaml."
        )

    resolved = _SET_CHANNEL_ALIASES.get(normalized)
    if resolved is None:
        allowed = ", ".join(sorted(_SET_CHANNEL_ALIASES))
        raise ValueError(
            f"Unsupported set channel '{raw_name}'. Allowed: {allowed}. "
            "Use `nqctl observables list --json` to inspect readable channels."
        )
    return resolved


def _parse_set_target_value(*, channel: str, raw_value: str) -> float:
    try:
        return float(str(raw_value).strip())
    except ValueError as exc:
        raise ValueError(
            f"Target value for channel '{channel}' must be numeric. "
            "Example: `nqctl set bias_v 0.12 --confirmed`."
        ) from exc


def _parse_positive_value(*, name: str, raw_value: str) -> float:
    try:
        value = float(str(raw_value).strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric.") from exc

    if value <= 0:
        raise ValueError(f"{name} must be positive.")
    return value


def _build_ramp_targets(*, start: float, end: float, step: float) -> tuple[float, ...]:
    if step <= 0:
        raise ValueError("Ramp step must be positive.")

    start_value = float(start)
    end_value = float(end)
    if start_value == end_value:
        return (end_value,)

    direction = 1.0 if end_value > start_value else -1.0
    step_signed = abs(step) * direction

    targets: list[float] = [start_value]
    current = start_value
    max_iterations = 1_000_000
    for _ in range(max_iterations):
        next_value = current + step_signed
        if (direction > 0 and next_value >= end_value) or (
            direction < 0 and next_value <= end_value
        ):
            targets.append(end_value)
            break

        targets.append(next_value)
        current = next_value
    else:
        raise ValueError("Ramp target generation exceeded safe iteration limit.")

    deduped_targets: list[float] = []
    for value in targets:
        if not deduped_targets or not math.isclose(
            value,
            deduped_targets[-1],
            rel_tol=0.0,
            abs_tol=1.0e-15,
        ):
            deduped_targets.append(value)
    return tuple(deduped_targets)


def _parse_args_json(raw_json: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid --args-json payload: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("--args-json must decode to a JSON object.")
    return {str(key): value for key, value in parsed.items()}


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


def _build_manifest_from_commands(
    command_specs: Sequence[DiscoveredCommand],
    *,
    include_non_get: bool,
    match: str,
) -> dict[str, Any]:
    if include_non_get:
        selected = list(command_specs)
    else:
        selected = [spec for spec in command_specs if spec.command.endswith("Get")]

    parameters: list[dict[str, Any]] = []
    for spec in selected:
        parameter_name = _derive_parameter_name(spec.command)
        entry: dict[str, Any] = {
            "name": parameter_name,
            "command": spec.command,
            "type": _guess_manifest_value_type(spec.command),
        }
        if spec.arguments:
            entry["args"] = {argument_name: None for argument_name in spec.arguments}
        parameters.append(entry)

    return {
        "meta": {
            "generated_by": "nqctl extensions scaffold",
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


def _guess_manifest_value_type(command_name: str) -> str:
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
    except Exception as exc:  # pragma: no cover - filesystem dependent
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
        "error": {
            "type": error_type,
            "message": message,
        },
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


if __name__ == "__main__":
    raise SystemExit(main())
