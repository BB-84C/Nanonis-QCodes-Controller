from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import inspect
import json
import math
import os
import re
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from nspmctl.client import create_client, probe_host_ports, report_to_dict
from nspmctl.client.errors import (
    NanonisCommandUnavailableError,
    NanonisConnectionError,
    NanonisInvalidArgumentError,
    NanonisProtocolError,
    NanonisTimeoutError,
)
from nspmctl.config import load_settings
from nspmctl.controller.extensions import (
    DEFAULT_PARAMETERS_FILE,
    load_parameter_specs,
)
from nspmctl.safety import PolicyViolation
from nspmctl.version import __version__

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_POLICY_BLOCKED = 2
EXIT_INVALID_INPUT = 3
EXIT_COMMAND_UNAVAILABLE = 4
EXIT_CONNECTION_FAILED = 5

_NEGATIVE_NUMERIC_TOKEN_RE = re.compile(
    r"^-((\d+\.?\d*)|(\.\d+))([eE][-+]?\d+)?$|^-inf$|^-nan$",
    re.IGNORECASE,
)

_TRUE_BOOL_TOKENS = frozenset({"1", "true", "yes", "on"})
_FALSE_BOOL_TOKENS = frozenset({"0", "false", "no", "off"})


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
        command_template="nspmctl get <parameter>",
        arguments=("parameter",),
    ),
    ActionDescriptor(
        name="set",
        safety="guarded",
        description="Apply guarded strict single-step write.",
        command_template="nspmctl set <parameter> <value>",
        arguments=("parameter", "value"),
    ),
    ActionDescriptor(
        name="ramp",
        safety="guarded",
        description="Apply explicit ramp using start/end/step/interval.",
        command_template="nspmctl ramp <parameter> <start> <end> <step> --interval-s 0.1",
        arguments=("parameter", "start", "end", "step", "interval_s"),
    ),
    ActionDescriptor(
        name="act",
        safety="policy-controlled",
        description="Invoke a manifest-defined backend action command.",
        command_template="nspmctl act <action_name> --arg key=value",
        arguments=("action_name", "arg"),
    ),
    ActionDescriptor(
        name="parameters_discover",
        safety="readonly",
        description="Discover backend commands for parameter authoring.",
        command_template="nspmctl parameters discover --match LockIn",
        arguments=("match",),
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


_NO_DAEMON_ENV = "NSPMCTL_NO_DAEMON"


def _is_daemon_routable(raw_argv: Sequence[str]) -> bool:
    """Return True if the given argv should be forwarded to a warm daemon.

    The ``daemon`` meta-command group and help flags always run inline.
    Everything else benefits from a warm controller.
    """
    if not raw_argv:
        return False
    if raw_argv[0] in {"-h", "--help", "-help"}:
        return False
    if raw_argv[0] == "daemon":
        return False
    return True


def _strip_no_daemon(raw_argv: list[str]) -> tuple[list[str], bool]:
    """Strip a ``--no-daemon`` flag from argv. Returns (cleaned, flag_was_present)."""
    if "--no-daemon" not in raw_argv:
        return raw_argv, False
    return [a for a in raw_argv if a != "--no-daemon"], True


def _try_route_through_daemon(raw_argv: list[str]) -> int | None:
    """Send the argv to a running daemon. Return its exit code, or None on miss."""
    try:
        from nspmctl import daemon as _daemon  # local import keeps cold path light
    except ImportError:
        return None

    status = _daemon.daemon_status()
    if not status.get("running") or not status.get("reachable"):
        return None

    try:
        response = _daemon.send_request_to_daemon(list(raw_argv))
    except (ConnectionRefusedError, ConnectionResetError, OSError, ValueError):
        return None

    stdout_text = str(response.get("stdout", ""))
    stderr_text = str(response.get("stderr", ""))
    if stdout_text:
        sys.stdout.write(stdout_text)
    if stderr_text:
        sys.stderr.write(stderr_text)
    rc = response.get("rc", 0)
    try:
        return int(rc)
    except (TypeError, ValueError):
        return 1


def _trigger_background_daemon_spawn(parameters_file: str | None = None) -> None:
    """Best-effort: spawn a daemon so the NEXT call hits a warm one."""
    try:
        from nspmctl import daemon as _daemon  # noqa: PLC0415

        _daemon.spawn_daemon_background(parameters_file=parameters_file)
    except Exception:  # noqa: BLE001 - best-effort, never block the user's call
        pass


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)

    # Daemon routing: inside the daemon process, _DAEMON_SHARED_INSTRUMENT is set
    # so we skip the forward-to-daemon shortcut to avoid infinite loops.
    use_daemon = _DAEMON_SHARED_INSTRUMENT is None
    raw_argv, no_daemon_flag = _strip_no_daemon(raw_argv)
    if no_daemon_flag or os.environ.get(_NO_DAEMON_ENV):
        use_daemon = False

    if use_daemon and _is_daemon_routable(raw_argv):
        result = _try_route_through_daemon(raw_argv)
        if result is not None:
            return result
        # Daemon was unreachable. Run inline this time AND spawn a daemon in the
        # background so the next agent tool call benefits from a warm controller.
        _trigger_background_daemon_spawn()

    return _run_inline(raw_argv)


def _run_inline(raw_argv: list[str]) -> int:
    normalized_argv = _normalize_help_args(raw_argv)
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
        prog="nspmctl",
        description=(
            "Nanonis SPM controller CLI for agent orchestration.\n"
            "Use atomic commands (capabilities/get/set/ramp/act/parameters/policy)."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Quick start:\n"
            "  nspmctl capabilities\n"
            "  nspmctl get bias_v\n"
            "  nspmctl set bias_v 0.12\n"
            "  nspmctl ramp bias_v 0.1 0.3 0.01 --interval-s 0.1\n"
            "\n"
            "Help shortcuts:\n"
            "  nspmctl -help\n"
            "  nspmctl -help parameters\n"
            "  nspmctl -help ramp"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    parser_capabilities = subparsers.add_parser(
        "capabilities",
        help="Show available parameters/actions and policy summary.",
    )
    _add_runtime_args(parser_capabilities)
    parser_capabilities.add_argument(
        "--include-backend-commands",
        action="store_true",
        help="Connect and include backend command names.",
    )
    parser_capabilities.add_argument("--backend-match", help="Optional filter token.")
    parser_capabilities.set_defaults(handler=_cmd_capabilities)

    parser_showall = subparsers.add_parser(
        "showall",
        help="Show full legacy capabilities payload.",
    )
    _add_runtime_args(parser_showall)
    parser_showall.add_argument(
        "--include-backend-commands",
        action="store_true",
        help="Connect and include backend command names.",
    )
    parser_showall.add_argument("--backend-match", help="Optional filter token.")
    parser_showall.set_defaults(handler=_cmd_showall)

    parser_get = subparsers.add_parser("get", help="Read a single parameter value.")
    _add_runtime_args(parser_get)
    parser_get.add_argument("parameter", help="Parameter name from parameter files.")
    parser_get.set_defaults(handler=_cmd_get)

    parser_set = subparsers.add_parser(
        "set",
        help="Apply guarded structured write.",
        description=(
            "Apply guarded structured write for writable parameters.\n"
            "Use repeatable --arg key=value for multi-field commands."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  nspmctl set bias_v 0.15\n"
            "  nspmctl set scan_buffer --arg Pixels=512\n"
            "  nspmctl set zctrl_setpoint_a --arg Z_Controller_setpoint=8e-11 --plan-only"
        ),
    )
    _add_runtime_args(parser_set)
    parser_set.add_argument("parameter", help="Writable parameter name.")
    parser_set.add_argument("value", nargs="?", help="Optional shorthand scalar value.")
    parser_set.add_argument(
        "--arg",
        action="append",
        default=[],
        help="Parameter argument override (repeatable): key=value",
    )
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
            "  nspmctl ramp bias_v 0.1 0.25 0.01 --interval-s 0.1\n"
            "  nspmctl ramp zctrl_setpoint_a 5e-11 1e-10 5e-12 --interval-s 0.05 --plan-only"
        ),
    )
    _add_runtime_args(parser_ramp)
    parser_ramp.add_argument("parameter", help="Writable parameter name.")
    parser_ramp.add_argument("start", help="Ramp start value.")
    parser_ramp.add_argument("end", help="Ramp end value.")
    parser_ramp.add_argument("step", help="Positive ramp step value.")
    parser_ramp.add_argument(
        "--interval-s", type=float, required=True, help="Step interval in seconds."
    )
    parser_ramp.add_argument("--plan-only", action="store_true", help="Show ramp plan only.")
    parser_ramp.set_defaults(handler=_cmd_ramp)

    parser_act = subparsers.add_parser(
        "act",
        help="Invoke one manifest action command.",
        description=(
            "Invoke one action command defined in parameters.yaml actions section.\n"
            "Use repeatable --arg key=value entries to override default arguments."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  nspmctl act Scan_Action --arg Scan_action=0 --arg Scan_direction=1\n"
            "  nspmctl act Scan_WaitEndOfScan --arg Timeout_ms=5000"
        ),
    )
    _add_runtime_args(parser_act)
    parser_act.add_argument("action_name", help="Action name from actions manifest section.")
    parser_act.add_argument(
        "--arg",
        action="append",
        default=[],
        help="Action argument override (repeatable): key=value",
    )
    parser_act.add_argument("--plan-only", action="store_true", help="Show action plan only.")
    parser_act.set_defaults(handler=_cmd_act)

    parser_observables = subparsers.add_parser("observables", help="Observable metadata commands.")
    observables_subparsers = parser_observables.add_subparsers(
        dest="observables_command", required=True
    )
    parser_observables_list = observables_subparsers.add_parser(
        "list", help="List observable parameters."
    )
    _add_runtime_args(parser_observables_list)
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

    parser_policy_set = policy_subparsers.add_parser(
        "set", help="Set runtime policy write/dry-run flags."
    )
    _add_json_arg(parser_policy_set)
    parser_policy_set.add_argument(
        "--allow-writes",
        type=_parse_cli_bool_arg,
        help="Override safety.allow_writes (1/0, true/false, yes/no, on/off).",
    )
    parser_policy_set.add_argument(
        "--dry-run",
        type=_parse_cli_bool_arg,
        help="Override safety.dry_run (1/0, true/false, yes/no, on/off).",
    )
    parser_policy_set.add_argument("--config-file")
    parser_policy_set.set_defaults(handler=_cmd_policy_set)

    parser_backend = subparsers.add_parser("backend", help="Backend command utilities.")
    backend_subparsers = parser_backend.add_subparsers(dest="backend_command", required=True)

    parser_backend_commands = backend_subparsers.add_parser(
        "commands", help="List backend commands."
    )
    _add_runtime_args(parser_backend_commands)
    parser_backend_commands.add_argument("--match", help="Optional filter token.")
    parser_backend_commands.set_defaults(handler=_cmd_backend_commands)

    parser_doctor = subparsers.add_parser("doctor", help="Connectivity preflight checks.")
    _add_json_arg(parser_doctor)
    parser_doctor.add_argument("--config-file")
    parser_doctor.add_argument("--attempts", type=int, default=2)
    parser_doctor.add_argument("--command-probe", action="store_true")
    parser_doctor.set_defaults(handler=_cmd_doctor)

    parser_daemon = subparsers.add_parser(
        "daemon",
        help="Manage the persistent nspmctl daemon (warm controller, fast tool calls).",
    )
    daemon_subparsers = parser_daemon.add_subparsers(dest="daemon_command", required=True)

    parser_daemon_status = daemon_subparsers.add_parser(
        "status", help="Show daemon liveness, port, pid, version."
    )
    _add_json_arg(parser_daemon_status)
    parser_daemon_status.set_defaults(handler=_cmd_daemon_status)

    parser_daemon_start = daemon_subparsers.add_parser(
        "start", help="Spawn the daemon in the background; wait until ready."
    )
    _add_json_arg(parser_daemon_start)
    parser_daemon_start.add_argument(
        "--parameters-file",
        default=None,
        help="Optional parameter manifest override for the warm controller.",
    )
    parser_daemon_start.add_argument(
        "--wait-timeout-s",
        type=float,
        default=8.0,
        help="How long to wait for the daemon to become reachable (default 8s).",
    )
    parser_daemon_start.set_defaults(handler=_cmd_daemon_start)

    parser_daemon_stop = daemon_subparsers.add_parser(
        "stop", help="Terminate the running daemon (if any)."
    )
    _add_json_arg(parser_daemon_stop)
    parser_daemon_stop.set_defaults(handler=_cmd_daemon_stop)

    parser_daemon_restart = daemon_subparsers.add_parser(
        "restart", help="Stop the daemon if running, then start a fresh one."
    )
    _add_json_arg(parser_daemon_restart)
    parser_daemon_restart.add_argument(
        "--parameters-file",
        default=None,
        help="Optional parameter manifest override for the warm controller.",
    )
    parser_daemon_restart.add_argument(
        "--wait-timeout-s",
        type=float,
        default=8.0,
        help="How long to wait for the new daemon to become reachable (default 8s).",
    )
    parser_daemon_restart.set_defaults(handler=_cmd_daemon_restart)

    parser_daemon_logs = daemon_subparsers.add_parser(
        "logs", help="Print the tail of the daemon log file."
    )
    parser_daemon_logs.add_argument(
        "--tail",
        type=int,
        default=80,
        help="Number of trailing log lines to print (default 80).",
    )
    parser_daemon_logs.set_defaults(handler=_cmd_daemon_logs)

    _configure_negative_number_parsing(parser)
    return parser


def _configure_negative_number_parsing(parser: argparse.ArgumentParser) -> None:
    parser._negative_number_matcher = _NEGATIVE_NUMERIC_TOKEN_RE
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for subparser in action.choices.values():
                _configure_negative_number_parsing(subparser)


def _add_runtime_args(parser: argparse.ArgumentParser) -> None:
    _add_json_arg(parser)
    parser.add_argument("--config-file", help="Runtime config YAML path.")
    parser.add_argument(
        "--parameters-file",
        default=str(DEFAULT_PARAMETERS_FILE),
        help=f"Built-in parameter YAML (default: {DEFAULT_PARAMETERS_FILE}).",
    )


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
    with _instrument_context(args, auto_connect=False) as instrument_ctx:
        instrument, _ = instrument_ctx
        parameters = _collect_parameter_capabilities(instrument)
        action_commands = _collect_action_command_capabilities(instrument)

    payload: dict[str, Any] = {
        "parameters": {"count": len(parameters), "items": parameters},
        "action_commands": {"count": len(action_commands), "items": action_commands},
    }
    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_showall(args: argparse.Namespace) -> int:
    settings = load_settings(config_file=args.config_file)
    with _instrument_context(args, auto_connect=False) as instrument_ctx:
        instrument, _ = instrument_ctx
        observables = _collect_observables(instrument)
        parameters = _collect_parameter_capabilities(instrument)
        action_commands = _collect_action_command_capabilities(instrument)

    payload: dict[str, Any] = {
        "cli": {"name": "nspmctl", "version": __version__},
        "observables": observables,
        "parameters": {"count": len(parameters), "items": parameters},
        "action_commands": {"count": len(action_commands), "items": action_commands},
        "actions": [asdict(descriptor) for descriptor in _ACTION_DESCRIPTORS],
        "policy": {
            "allow_writes": settings.safety.allow_writes,
            "dry_run": settings.safety.dry_run,
            "default_ramp_interval_s": settings.safety.default_ramp_interval_s,
        },
        "parameter_files": {"parameters": str(Path(args.parameters_file).expanduser())},
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
        snapshot = instrument.get_parameter_snapshot(parameter_name)
        values = snapshot.get("values", {})
        if len(values) == 1:
            value = next(iter(values.values()))
        else:
            value = values
        payload: dict[str, Any] = {
            "parameter": parameter_name,
            "value": _json_safe(value),
            "fields": _json_safe(values),
            "timestamp_utc": _now_utc_iso(),
        }

    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_set(args: argparse.Namespace) -> int:
    parameter_name = _normalize_parameter_name(args.parameter)
    if parameter_name in {"allow_writes", "dry_run"}:
        raise ValueError(
            "'set' controls instrument parameters only, not runtime policy flags. "
            "Use `nspmctl policy show` for guidance and update NANONIS_ALLOW_WRITES/NANONIS_DRY_RUN "
            "or edit config/default_runtime.yaml."
        )

    provided_args = _parse_action_args(raw_args=tuple(args.arg))
    interval_s = None if args.interval_s is None else float(args.interval_s)
    if interval_s is not None and interval_s < 0:
        raise ValueError("--interval-s must be non-negative.")

    with _instrument_context(
        args, auto_connect=True, include_parameters=(parameter_name,)
    ) as instrument_ctx:
        instrument, journal = instrument_ctx
        spec = instrument.parameter_spec(parameter_name)

        if args.value is not None:
            if provided_args:
                raise ValueError("Use either positional <value> or --arg entries, not both.")
            if spec.set_cmd is None:
                raise ValueError(f"Parameter '{parameter_name}' is not writable.")
            if not instrument.is_scalar_rampable(parameter_name):
                raise ValueError(
                    f"Positional set shorthand is not supported for multi-field parameter "
                    f"'{parameter_name}' because its scalar field cannot be inferred safely. "
                    f"Provide every required field explicitly with --arg key=value."
                )
            writable_targets = [field.name for field in spec.set_cmd.arg_fields if field.required]
            if len(writable_targets) != 1:
                raise ValueError(
                    "Positional set shorthand is only supported when exactly one required set field exists. "
                    "Use --arg key=value for this parameter."
                )
            provided_args = {writable_targets[0]: args.value}

        if not provided_args:
            raise ValueError(
                "No set arguments provided. Use --arg key=value or positional <value>."
            )

        result = instrument.set_parameter_fields(
            parameter_name,
            args=provided_args,
            plan_only=bool(args.plan_only),
            interval_s=interval_s,
        )

        payload: dict[str, Any] = {
            "parameter": parameter_name,
            "plan_only": bool(args.plan_only),
            "result": _json_safe(result),
            "timestamp_utc": _now_utc_iso(),
        }

    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_ramp(args: argparse.Namespace) -> int:
    parameter_name = _normalize_parameter_name(args.parameter)
    if parameter_name in {"allow_writes", "dry_run"}:
        raise ValueError("'ramp' controls instrument parameters only, not runtime policy flags.")

    start_value = _parse_float_arg(name="start", raw_value=args.start)
    end_value = _parse_float_arg(name="end", raw_value=args.end)
    step_value = abs(_parse_float_arg(name="step", raw_value=args.step))
    if step_value <= 0:
        raise ValueError("step magnitude must be positive.")
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

    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_act(args: argparse.Namespace) -> int:
    action_name = str(args.action_name).strip()
    if not action_name:
        raise ValueError("Action name cannot be empty.")

    action_args = _parse_action_args(raw_args=tuple(args.arg))
    with _instrument_context(args, auto_connect=True) as instrument_ctx:
        instrument, journal = instrument_ctx
        result = instrument.execute_action(
            action_name,
            args=action_args,
            plan_only=bool(args.plan_only),
        )
        payload: dict[str, Any] = {
            "action": action_name,
            "plan_only": bool(args.plan_only),
            "result": _json_safe(result),
            "timestamp_utc": _now_utc_iso(),
        }

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


def _cmd_policy_set(args: argparse.Namespace) -> int:
    allow_writes = args.allow_writes
    dry_run = args.dry_run
    if allow_writes is None and dry_run is None:
        raise ValueError("Provide at least one policy flag: --allow-writes and/or --dry-run.")

    config_path = _resolve_policy_config_path(args.config_file)
    config_values = _load_runtime_config_yaml(config_path)
    safety_section = config_values.get("safety")
    if safety_section is None:
        updated_safety: dict[str, Any] = {}
    elif isinstance(safety_section, Mapping):
        updated_safety = dict(safety_section)
    else:
        raise ValueError("Runtime config section 'safety' must be a mapping.")

    if allow_writes is not None:
        updated_safety["allow_writes"] = bool(allow_writes)
    if dry_run is not None:
        updated_safety["dry_run"] = bool(dry_run)
    config_values["safety"] = updated_safety

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config_values, handle, sort_keys=False)

    settings = load_settings(config_file=config_path)
    payload = {
        "allow_writes": settings.safety.allow_writes,
        "dry_run": settings.safety.dry_run,
        "default_ramp_interval_s": settings.safety.default_ramp_interval_s,
        "config_file": str(config_path),
        "timestamp_utc": _now_utc_iso(),
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
                "has_get_cmd": spec.get_cmd is not None,
                "has_set_cmd": spec.set_cmd is not None,
            }
            for spec in specs
        ],
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
        },
        "probe": report_to_dict(report),
    }

    _print_payload(payload, as_json=args.json)
    return EXIT_OK if report.candidate_ports else EXIT_FAILED


# Module-level slot set by the nspmctl daemon process before each request,
# so handlers reuse the warm controller instead of opening a fresh one.
_DAEMON_SHARED_INSTRUMENT: Any = None


def _cmd_daemon_status(args: argparse.Namespace) -> int:
    from nspmctl import daemon as _daemon

    payload = _daemon.daemon_status()
    _print_payload(payload, as_json=args.json)
    return EXIT_OK if payload.get("running") and payload.get("reachable") else EXIT_FAILED


def _cmd_daemon_start(args: argparse.Namespace) -> int:
    from nspmctl import daemon as _daemon

    existing = _daemon.daemon_status()
    if existing.get("running") and existing.get("reachable"):
        payload = {"started": False, "reason": "already_running", "status": existing}
        _print_payload(payload, as_json=args.json)
        return EXIT_OK

    pid = _daemon.spawn_daemon_background(parameters_file=args.parameters_file)
    ready = _daemon.wait_for_daemon_ready(timeout_s=float(args.wait_timeout_s))
    status = _daemon.daemon_status()
    payload = {
        "started": ready and bool(status.get("running")),
        "spawned_pid": pid,
        "status": status,
    }
    _print_payload(payload, as_json=args.json)
    return EXIT_OK if payload["started"] else EXIT_FAILED


def _cmd_daemon_stop(args: argparse.Namespace) -> int:
    from nspmctl import daemon as _daemon

    info = _daemon._read_pid_file()  # noqa: SLF001 - intra-package access
    if info is None:
        payload = {"stopped": False, "reason": "no_pid_file"}
        _print_payload(payload, as_json=args.json)
        return EXIT_OK

    pid = int(info.get("pid", 0))
    if pid > 0 and _daemon._is_pid_alive(pid):  # noqa: SLF001
        _daemon._terminate_pid(pid)  # noqa: SLF001
    _daemon._remove_pid_file()  # noqa: SLF001

    payload = {"stopped": True, "pid": pid}
    _print_payload(payload, as_json=args.json)
    return EXIT_OK


def _cmd_daemon_restart(args: argparse.Namespace) -> int:
    from nspmctl import daemon as _daemon

    info = _daemon._read_pid_file()  # noqa: SLF001
    if info is not None:
        pid = int(info.get("pid", 0))
        if pid > 0 and _daemon._is_pid_alive(pid):  # noqa: SLF001
            _daemon._terminate_pid(pid)  # noqa: SLF001
        _daemon._remove_pid_file()  # noqa: SLF001

    pid_new = _daemon.spawn_daemon_background(parameters_file=args.parameters_file)
    ready = _daemon.wait_for_daemon_ready(timeout_s=float(args.wait_timeout_s))
    status = _daemon.daemon_status()
    payload = {
        "restarted": ready and bool(status.get("running")),
        "spawned_pid": pid_new,
        "status": status,
    }
    _print_payload(payload, as_json=args.json)
    return EXIT_OK if payload["restarted"] else EXIT_FAILED


def _cmd_daemon_logs(args: argparse.Namespace) -> int:
    from nspmctl import daemon as _daemon

    log_path = _daemon.log_file_path()
    if not log_path.is_file():
        print(f"(no daemon log at {log_path})", file=sys.stderr)
        return EXIT_FAILED

    tail = max(1, int(args.tail))
    with log_path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        # Read at most ~256 KiB from the end to find `tail` lines.
        chunk_size = min(size, 256 * 1024)
        handle.seek(size - chunk_size, 0)
        data = handle.read(chunk_size)
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()[-tail:]
    for line in lines:
        print(line)
    return EXIT_OK


@contextmanager
def _instrument_context(
    args: argparse.Namespace,
    *,
    auto_connect: bool,
    include_parameters: Sequence[str] | None = None,
) -> Iterator[tuple[Any, None]]:
    if _DAEMON_SHARED_INSTRUMENT is not None:
        # Daemon owns the lifecycle; never tear it down between requests.
        yield _DAEMON_SHARED_INSTRUMENT, None
        return

    instrument_cls = _load_instrument_class()
    load_settings(config_file=args.config_file)

    instrument = None
    try:
        instrument = instrument_cls(
            name=f"nspmctl_{int(time.time() * 1000)}",
            config_file=args.config_file,
            parameters_file=args.parameters_file,
            include_parameters=include_parameters,
            auto_connect=auto_connect,
        )
        yield instrument, None
    finally:
        if instrument is not None:
            instrument.close()


def _load_instrument_class() -> Any:
    from nspmctl.controller import NanonisController

    return NanonisController


def _spec_has_ramp(spec: Any) -> bool:
    """Whether this parameter can actually be scalar-ramped at runtime.

    A parameter advertises ramp support only when ramping is enabled AND the
    scalar coordinate can be applied safely: single-field parameters, or
    multi-field parameters that declare a dedicated scalar_strategy. Multi-field
    parameters without a strategy are blocked by the runtime guard, so they must
    not advertise has_ramp=true to agents.
    """
    if spec.safety is None or not spec.safety.ramp_enabled:
        return False
    if getattr(spec, "scalar_strategy", None) is not None:
        return True
    return not getattr(spec, "is_multi_field", False)


def _collect_observables(instrument: Any) -> list[dict[str, Any]]:
    observables: list[dict[str, Any]] = []
    for spec in instrument.parameter_specs():
        observables.append(
            {
                "name": spec.name,
                "label": spec.label,
                "readable": spec.readable,
                "writable": spec.writable,
                "has_ramp": _spec_has_ramp(spec),
            }
        )
    return observables


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def _collect_parameter_capabilities(instrument: Any) -> list[dict[str, Any]]:
    capabilities: list[dict[str, Any]] = []
    for spec in instrument.parameter_specs():
        get_cmd = None
        if spec.get_cmd is not None:
            get_description = _optional_text(spec.get_cmd.description)
            get_cmd = {
                "command": spec.get_cmd.command,
                "payload_index": int(spec.get_cmd.payload_index),
                "arg_fields": [asdict(field) for field in spec.get_cmd.arg_fields],
                "response_fields": [asdict(field) for field in spec.get_cmd.response_fields],
            }
            if get_description is not None:
                get_cmd["description"] = get_description

        set_cmd = None
        if spec.set_cmd is not None:
            set_description = _optional_text(spec.set_cmd.description)
            set_cmd = {
                "command": spec.set_cmd.command,
                "arg_fields": [asdict(field) for field in spec.set_cmd.arg_fields],
            }
            if set_description is not None:
                set_cmd["description"] = set_description

        safety = None
        if spec.safety is not None:
            safety = {
                "min_value": spec.safety.min_value,
                "max_value": spec.safety.max_value,
                "max_step": spec.safety.max_step,
                "max_slew_per_s": spec.safety.max_slew_per_s,
                "cooldown_s": spec.safety.cooldown_s,
                "ramp_enabled": spec.safety.ramp_enabled,
                "ramp_interval_s": spec.safety.ramp_interval_s,
            }

        capability: dict[str, Any] = {
            "label": spec.label,
            "name": spec.name,
            "readable": bool(spec.readable),
            "writable": bool(spec.writable),
            "has_ramp": _spec_has_ramp(spec),
            "get_cmd": get_cmd,
            "set_cmd": set_cmd,
            "safety": safety,
        }

        capabilities.append(capability)
    return capabilities


def _collect_action_command_capabilities(instrument: Any) -> list[dict[str, Any]]:
    capabilities: list[dict[str, Any]] = []
    for spec in instrument.action_specs():
        action_cmd: dict[str, Any] = {
            "command": spec.action_cmd.command,
            "arg_fields": [asdict(field) for field in spec.action_cmd.arg_fields],
        }
        description = _optional_text(spec.action_cmd.description)
        if description is not None:
            action_cmd["description"] = description

        capabilities.append(
            {
                "name": spec.name,
                "action_cmd": action_cmd,
                "safety_mode": spec.safety_mode,
            }
        )
    return capabilities


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


def _parse_cli_bool_arg(raw_value: object) -> bool:
    if isinstance(raw_value, bool):
        return raw_value

    normalized = str(raw_value).strip().lower()
    if normalized in _TRUE_BOOL_TOKENS:
        return True
    if normalized in _FALSE_BOOL_TOKENS:
        return False

    raise argparse.ArgumentTypeError("Expected boolean value: 1/0, true/false, yes/no, on/off.")


def _resolve_policy_config_path(config_file: str | None) -> Path:
    if config_file is not None:
        return Path(config_file).expanduser()
    env_override = os.environ.get("NANONIS_CONFIG_FILE")
    if env_override:
        return Path(env_override).expanduser()
    return Path("config/default_runtime.yaml")


def _load_runtime_config_yaml(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}

    with config_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)

    if loaded is None:
        return {}
    if not isinstance(loaded, Mapping):
        raise ValueError("Runtime config file must contain a top-level mapping.")
    return dict(loaded)


def _parse_positive_float_arg(*, name: str, raw_value: str) -> float:
    value = _parse_float_arg(name=name, raw_value=raw_value)
    if value <= 0:
        raise ValueError(f"{name} must be positive.")
    return value


def _parse_action_args(*, raw_args: Sequence[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw_item in raw_args:
        token = str(raw_item)
        if "=" not in token:
            raise ValueError(f"Invalid --arg value '{token}'. Expected key=value format.")
        key, raw_value = token.split("=", 1)
        normalized_key = key.strip()
        if not normalized_key:
            raise ValueError(f"Invalid --arg value '{token}'. Argument key cannot be empty.")
        if normalized_key in parsed:
            raise ValueError(f"Duplicate --arg key: {normalized_key}")
        parsed[normalized_key] = raw_value
    return parsed


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


def _installed_package_version(package_name: str) -> str:
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


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
        print(json.dumps(_json_safe(dict(payload)), indent=2))
        return

    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            print(f"{key}: {json.dumps(_json_safe(value), ensure_ascii=True)}")
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


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
