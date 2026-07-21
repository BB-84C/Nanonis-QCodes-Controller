from __future__ import annotations

import ast
import math
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nspmctl.client import NanonisClient, build_client_from_settings
from nspmctl.client.base import NanonisHealth
from nspmctl.client.errors import NanonisProtocolError
from nspmctl.config import load_settings
from nspmctl.safety import (
    ChannelLimit,
    PolicyViolation,
    WriteExecutionReport,
    WritePlan,
    WritePolicy,
)

from .extensions import (
    DEFAULT_PARAMETERS_FILE,
    ActionSpec,
    ParameterSpec,
    SafetySpec,
    load_action_specs,
    load_parameter_specs,
)


@dataclass(frozen=True)
class GuardedWriteAuditEntry:
    timestamp_utc: str
    operation: str
    status: str
    dry_run: bool
    detail: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RampPlan:
    parameter: str
    start_value: float
    end_value: float
    step_value: float
    interval_s: float
    targets: tuple[float, ...]
    plans: tuple[WritePlan, ...]

    @property
    def step_count(self) -> int:
        return len(self.plans)

    @property
    def dry_run(self) -> bool:
        return any(plan.dry_run for plan in self.plans)


@dataclass(frozen=True)
class RampExecutionReport:
    parameter: str
    dry_run: bool
    attempted_steps: int
    applied_steps: int
    initial_value: float
    target_value: float
    final_value: float
    reports: tuple[WriteExecutionReport, ...]


_TRUE_STRINGS = {"1", "true", "yes", "on"}
_FALSE_STRINGS = {"0", "false", "no", "off"}

# Scalar-write strategies for multi-field parameters whose scalar coordinate
# cannot be inferred from response order + sole-required-arg heuristics.
#
# The Nanonis Z-controller gain has only two degrees of freedom: the proportional
# gain P and the integral action, expressed either as the integral gain I or the
# time constant T, locked by I = P / T. The controller is PT-authoritative (it
# stores P and T and derives I; the wire-level I argument is ignored). Each
# strategy below ramps one coordinate while holding a chosen partner constant and
# always sends a fully self-consistent (P, T, I) tuple.
#
# Mapping: strategy key -> coordinate set-field index (0=P_gain, 1=Time_constant_s,
# 2=I_gain).
_ZCTRL_GAIN_STRATEGIES: dict[str, int] = {
    "zctrl_i_gain": 2,  # ramp I, hold P, send T = P / I
    "zctrl_t_const": 1,  # ramp T, hold P, send I = P / T
    "zctrl_p_gain_hold_t": 0,  # ramp P, hold T, I follows = P / T
    "zctrl_p_gain_hold_i": 0,  # ramp P, hold I, send T = P / I
}
_SUPPORTED_SCALAR_STRATEGIES = frozenset(_ZCTRL_GAIN_STRATEGIES)


def _normalize_field_name(name: str) -> str:
    return "".join(ch.lower() for ch in str(name) if ch.isalnum())


def _coerce_action_value(value: Any, *, value_type: str, field_name: str) -> Any:
    if value_type.startswith("array[") and value_type.endswith("]"):
        element_type = value_type[6:-1].strip() or "str"
        if hasattr(value, "tolist"):
            try:
                value = value.tolist()
            except Exception:
                pass
        if isinstance(value, str):
            text = value.strip()
            if text.startswith(("[", "(")) and text.endswith(("]", ")")):
                try:
                    parsed = ast.literal_eval(text)
                except (ValueError, SyntaxError):
                    parsed = None
                if isinstance(parsed, (list, tuple)):
                    value = parsed
        if isinstance(value, (list, tuple)):
            return [
                _coerce_action_value(item, value_type=element_type, field_name=field_name)
                for item in value
            ]
        text = str(value).strip()
        if not text:
            return []
        parts = [part.strip() for part in text.split(",")]
        return [
            _coerce_action_value(part, value_type=element_type, field_name=field_name)
            for part in parts
        ]
    if value_type == "array":
        if isinstance(value, (list, tuple)):
            return list(value)
        text = str(value).strip()
        if not text:
            return []
        return [part.strip() for part in text.split(",")]
    if value_type == "float":
        try:
            return float(value)
        except (TypeError, ValueError):
            return str(value)
    if value_type == "int":
        if isinstance(value, str):
            text = value.strip()
            if text.startswith(("[", "(")) and text.endswith(("]", ")")):
                try:
                    parsed = ast.literal_eval(text)
                except (ValueError, SyntaxError):
                    parsed = None
                if isinstance(parsed, (list, tuple)) and len(parsed) == 1:
                    value = parsed[0]
        try:
            return int(value)
        except (TypeError, ValueError):
            return str(value)
    if value_type == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value != 0
        normalized = str(value).strip().lower()
        if normalized in _TRUE_STRINGS:
            return True
        if normalized in _FALSE_STRINGS:
            return False
        raise ValueError(f"{field_name} must be a boolean value.")
    if value_type == "str":
        return str(value)
    raise ValueError(f"Unsupported action value type: {value_type}")


class NanonisController:
    def __init__(
        self,
        name: str,
        *,
        client: NanonisClient | None = None,
        config_file: str | Path | None = None,
        parameters_file: str | Path | None = None,
        include_parameters: Sequence[str] | None = None,
        write_policy: WritePolicy | None = None,
        auto_connect: bool = True,
        **kwargs: Any,
    ) -> None:
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s) for NanonisController: {unexpected}")
        self.name = name

        self._owns_client = client is None
        self._client: NanonisClient
        self._write_audit_log: list[GuardedWriteAuditEntry] = []

        settings = load_settings(config_file=config_file)

        if client is None:
            self._client = build_client_from_settings(settings.nanonis)
        else:
            self._client = client

        parameter_manifest = (
            DEFAULT_PARAMETERS_FILE if parameters_file is None else Path(parameters_file)
        )
        all_specs = {spec.name: spec for spec in load_parameter_specs(parameter_manifest)}
        self._action_specs = {spec.name: spec for spec in load_action_specs(parameter_manifest)}
        self._parameter_specs = self._filter_specs(all_specs, include_parameters)

        if write_policy is None:
            limits = _build_channel_limits(
                self._parameter_specs.values(),
                default_ramp_interval_s=settings.safety.default_ramp_interval_s,
            )
            self._write_policy = WritePolicy.from_settings(settings.safety, limits=limits)
        else:
            self._write_policy = write_policy

        if auto_connect:
            self._client.connect()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def client_health(self) -> NanonisHealth:
        return self._client.health()

    def available_backend_commands(self, *, match: str | None = None) -> tuple[str, ...]:
        list_commands = getattr(self._client, "available_commands", None)
        if not callable(list_commands):
            raise NanonisProtocolError("Active client does not expose command discovery.")

        raw_names = list_commands()
        if not isinstance(raw_names, Iterable):
            raise NanonisProtocolError("Backend command discovery must return an iterable.")
        command_names = sorted(str(name) for name in raw_names)
        if match is None:
            return tuple(command_names)

        token = match.strip().lower()
        if not token:
            return tuple(command_names)
        return tuple(command for command in command_names if token in command.lower())

    @property
    def write_policy(self) -> WritePolicy:
        return self._write_policy

    def parameter_specs(self) -> tuple[ParameterSpec, ...]:
        names = sorted(self._parameter_specs)
        return tuple(self._parameter_specs[name] for name in names)

    def parameter_spec(self, name: str) -> ParameterSpec:
        normalized = name.strip()
        if not normalized:
            raise ValueError("Parameter name cannot be empty.")
        spec = self._parameter_specs.get(normalized)
        if spec is None:
            raise ValueError(f"Unknown parameter: {name}")
        return spec

    def action_specs(self) -> tuple[ActionSpec, ...]:
        names = sorted(self._action_specs)
        return tuple(self._action_specs[name] for name in names)

    def action_spec(self, name: str) -> ActionSpec:
        normalized = str(name).strip()
        if not normalized:
            raise ValueError("Action name cannot be empty.")
        spec = self._action_specs.get(normalized)
        if spec is None:
            raise ValueError(f"Unknown action: {name}")
        return spec

    def execute_action(
        self,
        action_name: str,
        *,
        args: Mapping[str, Any] | None = None,
        plan_only: bool = False,
    ) -> Mapping[str, Any]:
        spec = self.action_spec(action_name)
        incoming_args = dict(args) if args is not None else {}

        field_by_name = {arg_field.name: arg_field for arg_field in spec.action_cmd.arg_fields}
        allowed_args = set(field_by_name)
        unknown_args = sorted(key for key in incoming_args if key not in allowed_args)
        if unknown_args:
            formatted = ", ".join(unknown_args)
            raise ValueError(
                f"Action '{spec.name}' received unknown arguments: {formatted}. "
                "Use `nspmctl capabilities` to inspect supported arguments."
            )

        typed_args: dict[str, Any] = {}
        # NOTE: this default-backfill is not tuple-aware. It is safe for the actions
        # registered today, but a coupled multi-field command (like ZCtrl_GainSet)
        # must not be promoted into the `actions:` section without its own guard, or
        # an omitted field could be silently defaulted into an inconsistent tuple.
        for arg_field in spec.action_cmd.arg_fields:
            if arg_field.name in incoming_args:
                raw_value = incoming_args[arg_field.name]
            elif arg_field.default is not None:
                raw_value = arg_field.default
            elif arg_field.required:
                raise ValueError(
                    f"Action '{spec.name}' is missing required argument: {arg_field.name}"
                )
            else:
                continue

            typed_args[arg_field.name] = _coerce_action_value(
                raw_value,
                value_type=arg_field.type,
                field_name=f"{spec.name}.{arg_field.name}",
            )

        dry_run = False
        applied = False
        response: Mapping[str, Any] | None = None

        mode = spec.safety_mode
        if mode == "blocked":
            raise PolicyViolation(f"Action '{spec.name}' is blocked by action policy.")

        if mode == "guarded":
            self._write_policy.ensure_writes_enabled()
            dry_run = bool(plan_only or self._write_policy.dry_run)
            if dry_run:
                self._append_write_audit(
                    operation=f"action:{spec.name}",
                    status="dry_run",
                    dry_run=True,
                    detail="Guarded action dry-run; backend command not executed.",
                    metadata={"command": spec.action_cmd.command, "args": _json_safe(typed_args)},
                )
            else:
                response = self._call(spec.action_cmd.command, args=typed_args)
                applied = True
                self._append_write_audit(
                    operation=f"action:{spec.name}",
                    status="applied",
                    dry_run=False,
                    detail="Guarded action executed.",
                    metadata={"command": spec.action_cmd.command, "args": _json_safe(typed_args)},
                )
        else:
            dry_run = bool(plan_only)
            if not dry_run:
                response = self._call(spec.action_cmd.command, args=typed_args)
                applied = True

        return {
            "name": spec.name,
            "command": spec.action_cmd.command,
            "safety_mode": spec.safety_mode,
            "args": typed_args,
            "dry_run": dry_run,
            "applied": applied,
            "response": response,
        }

    def writable_parameter_names(self) -> tuple[str, ...]:
        names = [spec.name for spec in self.parameter_specs() if spec.writable]
        return tuple(names)

    def readable_parameter_names(self) -> tuple[str, ...]:
        names = [spec.name for spec in self.parameter_specs() if spec.readable]
        return tuple(names)

    def guarded_write_audit_log(self) -> tuple[GuardedWriteAuditEntry, ...]:
        return tuple(self._write_audit_log)

    def get_idn(self) -> dict[str, str | None]:
        health = self.client_health()
        return {
            "vendor": "Nanonis",
            "model": "STM Generic Bridge",
            "serial": health.endpoint,
            "firmware": self._client.version(),
        }

    def get_parameter_value(self, parameter_name: str) -> Any:
        spec = self.parameter_spec(parameter_name)
        if spec.get_cmd is None:
            raise ValueError(f"Parameter '{spec.name}' is not readable.")
        snapshot = self.get_parameter_snapshot(parameter_name)
        values = snapshot["values"]
        if values:
            # Honor the manifest's declared scalar payload_index instead of blindly
            # taking the first response field. For a multi-field parameter such as
            # zctrl_i_gain (payload_index 2), "first field" would return P-gain, not
            # the intended integral gain.
            payload_index = spec.get_cmd.payload_index
            selected_name: str | None = None
            for response_field in spec.get_cmd.response_fields:
                if response_field.index == payload_index and response_field.name in values:
                    selected_name = response_field.name
                    break
            if selected_name is None:
                selected_name = next(iter(values))
            value = values[selected_name]
        else:
            response = self._call(spec.get_cmd.command, args={})
            raw_value = self._extract_payload_value(
                response,
                command=spec.get_cmd.command,
                payload_index=spec.get_cmd.payload_index,
            )
            value = raw_value
        return value

    def get_parameter_snapshot(self, parameter_name: str) -> Mapping[str, Any]:
        spec = self.parameter_spec(parameter_name)
        if spec.get_cmd is None:
            raise ValueError(f"Parameter '{spec.name}' is not readable.")

        get_args = {
            field.name: field.default
            for field in spec.get_cmd.arg_fields
            if field.default is not None
        }
        missing_required = [
            field.name
            for field in spec.get_cmd.arg_fields
            if field.required and field.name not in get_args
        ]
        if missing_required:
            formatted = ", ".join(missing_required)
            raise ValueError(
                f"Parameter '{spec.name}' get_cmd missing required arguments without defaults: {formatted}"
            )

        response = self._call(spec.get_cmd.command, args=get_args)
        payload = response.get("payload")
        if not isinstance(payload, list):
            raise RuntimeError(
                f"Command '{spec.get_cmd.command}' returned payload type {type(payload).__name__}; expected list."
            )

        values: dict[str, Any] = {}
        ordered_values: list[Any] = []
        if spec.get_cmd.response_fields:
            for field in spec.get_cmd.response_fields:
                if field.index < 0 or field.index >= len(payload):
                    continue
                coerced = _coerce_action_value(
                    payload[field.index],
                    value_type=field.type,
                    field_name=f"{spec.name}.{field.name}",
                )
                values[field.name] = coerced
                ordered_values.append(coerced)
        return {
            "command": spec.get_cmd.command,
            "args": get_args,
            "values": values,
            "ordered_values": ordered_values,
            "raw_payload": payload,
        }

    def set_parameter_fields(
        self,
        parameter_name: str,
        *,
        args: Mapping[str, Any],
        plan_only: bool = False,
        interval_s: float | None = None,
    ) -> Mapping[str, Any]:
        del interval_s
        spec = self._require_writable_spec(parameter_name)
        if spec.set_cmd is None:
            raise ValueError(f"Parameter '{spec.name}' is not writable.")

        if self._scalar_strategy_name(spec) is not None:
            target = self._extract_scalar_strategy_target(spec, args)
            return self._apply_scalar_strategy(spec, target, plan_only=plan_only)

        normalized_overrides: dict[str, Any] = {}
        fields_by_normalized: dict[str, Any] = {
            _normalize_field_name(field.name): field for field in spec.set_cmd.arg_fields
        }
        for key, value in args.items():
            normalized = _normalize_field_name(str(key))
            field = fields_by_normalized.get(normalized)
            if field is None:
                raise ValueError(f"Unknown argument for parameter '{spec.name}': {key}")
            normalized_overrides[field.name] = value

        command_args: dict[str, Any] = dict(normalized_overrides)
        autofilled: dict[str, Any] = {}
        if spec.get_cmd is not None:
            snapshot = self.get_parameter_snapshot(spec.name)
            snapshot_values = snapshot.get("values", {})
            if isinstance(snapshot_values, dict):
                by_normalized_snapshot = {
                    _normalize_field_name(str(name)): value
                    for name, value in snapshot_values.items()
                }
                for field in spec.set_cmd.arg_fields:
                    if field.name in command_args:
                        continue
                    value = by_normalized_snapshot.get(_normalize_field_name(field.name))
                    if value is None:
                        continue
                    command_args[field.name] = value
                    autofilled[field.name] = value

        if spec.is_multi_field:
            # For coupled multi-field parameters (e.g. ZCtrl gain P/T/I) a silent
            # manifest-default backfill can corrupt the fields the caller did not
            # touch: the classic failure is Time_constant_s dropping to 0.0 because
            # its snapshot name ("Time constant") does not normalize to the set-field
            # name ("Time_constant_s"), which then drives I = P/T to infinity. Refuse
            # unless every field is either provided explicitly or preserved from the
            # live snapshot.
            unresolved = [
                arg_field.name
                for arg_field in spec.set_cmd.arg_fields
                if arg_field.name not in command_args
            ]
            if unresolved:
                formatted = ", ".join(unresolved)
                raise ValueError(
                    f"Parameter '{spec.name}' is a multi-field parameter whose fields form "
                    f"one consistent tuple; every field must be provided or preserved from "
                    f"the current state. Could not resolve: {formatted}. Provide them "
                    f"explicitly with --arg key=value (silently defaulting one coupled field "
                    f"can corrupt the others)."
                )
        else:
            for arg_field in spec.set_cmd.arg_fields:
                if arg_field.name not in command_args and arg_field.default is not None:
                    command_args[arg_field.name] = arg_field.default

            missing_required = [
                arg_field.name
                for arg_field in spec.set_cmd.arg_fields
                if arg_field.required and arg_field.name not in command_args
            ]

            if missing_required:
                formatted = ", ".join(missing_required)
                raise ValueError(
                    f"Parameter '{spec.name}' is missing required set args: {formatted}. "
                    "Provide them with --arg key=value."
                )

        typed_args: dict[str, Any] = {}
        for field in spec.set_cmd.arg_fields:
            if field.name not in command_args:
                continue
            typed_args[field.name] = _coerce_action_value(
                command_args[field.name],
                value_type=field.type,
                field_name=f"{spec.name}.{field.name}",
            )

        self._write_policy.ensure_writes_enabled()
        dry_run = bool(plan_only or self._write_policy.dry_run)
        response: Mapping[str, Any] | None = None
        if not dry_run:
            response = self._call(spec.set_cmd.command, args=typed_args)

        self._append_write_audit(
            operation=f"set_fields:{spec.name}",
            status="dry_run" if dry_run else "applied",
            dry_run=dry_run,
            detail="Structured parameter set executed.",
            metadata={
                "command": spec.set_cmd.command,
                "args": _json_safe(typed_args),
                "autofilled": _json_safe(autofilled),
            },
        )

        return {
            "name": spec.name,
            "command": spec.set_cmd.command,
            "args": typed_args,
            "autofilled": autofilled,
            "dry_run": dry_run,
            "applied": not dry_run,
            "response": response,
        }

    def plan_parameter_single_step(
        self,
        parameter_name: str,
        target_value: float,
        *,
        reason: str | None = None,
        interval_s: float | None = None,
    ) -> WritePlan:
        spec = self._require_writable_spec(parameter_name)
        self._ensure_scalar_rampable(spec)
        current_value = self._require_current_numeric_value(spec)

        return self._write_policy.plan_scalar_write_single_step(
            channel=spec.name,
            current_value=current_value,
            target_value=float(target_value),
            reason=reason,
            interval_s=interval_s,
        )

    def set_parameter_single_step(
        self,
        parameter_name: str,
        target_value: float,
        *,
        reason: str | None = None,
        interval_s: float | None = None,
    ) -> WriteExecutionReport:
        spec = self._require_writable_spec(parameter_name)
        operation = f"set_single_step:{spec.name}"

        return self._run_guarded_scalar_write(
            operation=operation,
            planner=lambda: self.plan_parameter_single_step(
                spec.name,
                target_value,
                reason=reason,
                interval_s=interval_s,
            ),
            sender=lambda value: self._send_parameter_value(spec, value),
        )

    def plan_parameter_ramp(
        self,
        parameter_name: str,
        *,
        start_value: float,
        end_value: float,
        step_value: float,
        interval_s: float,
        reason: str | None = None,
    ) -> RampPlan:
        spec = self._require_writable_spec(parameter_name)
        if spec.safety is not None and not spec.safety.ramp_enabled:
            raise ValueError(f"Ramp is disabled for parameter '{spec.name}'.")
        self._ensure_scalar_rampable(spec)

        if interval_s < 0:
            raise ValueError("interval_s must be non-negative.")

        current_value = self._require_current_numeric_value(spec)
        plans: list[WritePlan] = []
        targets = _build_ramp_targets(start=start_value, end=end_value, step=step_value)

        target_queue = list(targets)
        if not math.isclose(current_value, float(start_value), rel_tol=0.0, abs_tol=1e-15) and (
            not target_queue
            or not math.isclose(target_queue[0], float(start_value), rel_tol=0.0, abs_tol=1e-15)
        ):
            target_queue.insert(0, float(start_value))

        latest_value = current_value
        for target in target_queue:
            plan = self._write_policy.plan_scalar_write_single_step(
                channel=spec.name,
                current_value=latest_value,
                target_value=float(target),
                reason=reason,
                interval_s=interval_s,
            )
            plans.append(plan)
            latest_value = float(target)

        return RampPlan(
            parameter=spec.name,
            start_value=float(start_value),
            end_value=float(end_value),
            step_value=float(step_value),
            interval_s=float(interval_s),
            targets=tuple(target_queue),
            plans=tuple(plans),
        )

    def ramp_parameter(
        self,
        parameter_name: str,
        *,
        start_value: float,
        end_value: float,
        step_value: float,
        interval_s: float,
        reason: str | None = None,
    ) -> RampExecutionReport:
        spec = self._require_writable_spec(parameter_name)
        operation = f"ramp:{spec.name}"

        try:
            ramp_plan = self.plan_parameter_ramp(
                spec.name,
                start_value=start_value,
                end_value=end_value,
                step_value=step_value,
                interval_s=interval_s,
                reason=reason,
            )
        except Exception as exc:
            self._append_write_audit(
                operation=operation,
                status="blocked",
                dry_run=self._write_policy.dry_run,
                detail=f"{type(exc).__name__}: {exc}",
            )
            raise

        reports: list[WriteExecutionReport] = []
        applied_steps = 0

        def send_step(value: float) -> None:
            self._send_parameter_value(spec, value)

        try:
            for index, plan in enumerate(ramp_plan.plans):
                report = self._write_policy.execute_plan(plan, send_step=send_step)
                reports.append(report)
                applied_steps += report.applied_steps

                if index < len(ramp_plan.plans) - 1 and interval_s > 0 and not report.dry_run:
                    time.sleep(interval_s)
        except Exception as exc:
            self._append_write_audit(
                operation=operation,
                status="failed",
                dry_run=False,
                detail=f"{type(exc).__name__}: {exc}",
                metadata={
                    "attempted_steps": len(ramp_plan.plans),
                    "applied_steps": applied_steps,
                },
            )
            raise

        final_value = (
            reports[-1].final_value if reports else self._require_current_numeric_value(spec)
        )
        ramp_report = RampExecutionReport(
            parameter=spec.name,
            dry_run=(
                all(item.dry_run for item in reports) if reports else self._write_policy.dry_run
            ),
            attempted_steps=len(ramp_plan.plans),
            applied_steps=applied_steps,
            initial_value=ramp_plan.plans[0].current_value if ramp_plan.plans else final_value,
            target_value=ramp_plan.end_value,
            final_value=final_value,
            reports=tuple(reports),
        )

        self._append_write_audit(
            operation=operation,
            status="dry_run" if ramp_report.dry_run else "applied",
            dry_run=ramp_report.dry_run,
            detail="Ramp write completed.",
            metadata={
                "attempted_steps": ramp_report.attempted_steps,
                "applied_steps": ramp_report.applied_steps,
                "target_value": ramp_report.target_value,
                "final_value": ramp_report.final_value,
            },
        )
        return ramp_report

    def start_scan(self, *, direction_up: bool = False) -> None:
        _ = self._call(
            "Scan_Action",
            args={
                "Scan_action": 0,
                "Scan_direction": 1 if direction_up else 0,
            },
        )

    def stop_scan(self, *, direction_up: bool = False) -> None:
        _ = self._call(
            "Scan_Action",
            args={
                "Scan_action": 1,
                "Scan_direction": 1 if direction_up else 0,
            },
        )

    def wait_end_of_scan(self, *, timeout_ms: int = -1) -> tuple[bool, str]:
        response = self._call("Scan_WaitEndOfScan", args={"Timeout_ms": int(timeout_ms)})
        payload = response.get("payload")
        if not isinstance(payload, list) or len(payload) < 3:
            raise NanonisProtocolError(
                "Scan_WaitEndOfScan must return [timeout_status, path_size, path]."
            )

        timed_out = bool(int(payload[0]))
        path_value = payload[2]
        file_path = "" if path_value is None else str(path_value)
        return timed_out, file_path

    def _call(self, command: str, *, args: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        return self._client.call(command, args=args)

    def _send_parameter_value(self, spec: ParameterSpec, value: float) -> None:
        if spec.set_cmd is None:
            raise ValueError(f"Parameter '{spec.name}' is not writable.")
        if self._scalar_strategy_name(spec) is not None:
            self._apply_scalar_strategy(spec, value, plan_only=False)
            return
        self._ensure_scalar_rampable(spec)
        required_fields = [field.name for field in spec.set_cmd.arg_fields if field.required]
        if len(required_fields) != 1:
            raise ValueError(
                f"Parameter '{spec.name}' does not define exactly one required set field for scalar writes."
            )
        _ = self.set_parameter_fields(spec.name, args={required_fields[0]: value}, plan_only=False)

    def _run_guarded_scalar_write(
        self,
        *,
        operation: str,
        planner: Callable[[], WritePlan],
        sender: Callable[[float], None],
    ) -> WriteExecutionReport:
        try:
            plan = planner()
        except Exception as exc:
            self._append_write_audit(
                operation=operation,
                status="blocked",
                dry_run=self._write_policy.dry_run,
                detail=f"{type(exc).__name__}: {exc}",
            )
            raise

        try:
            report = self._write_policy.execute_plan(plan, send_step=sender)
        except Exception as exc:
            self._append_write_audit(
                operation=operation,
                status="failed",
                dry_run=False,
                detail=f"{type(exc).__name__}: {exc}",
                metadata={
                    "attempted_steps": plan.step_count,
                    "target_value": plan.target_value,
                },
            )
            raise

        self._append_write_audit(
            operation=operation,
            status="dry_run" if report.dry_run else "applied",
            dry_run=report.dry_run,
            detail="Scalar write completed.",
            metadata={
                "attempted_steps": report.attempted_steps,
                "applied_steps": report.applied_steps,
                "target_value": report.target_value,
                "final_value": report.final_value,
            },
        )
        return report

    def _append_write_audit(
        self,
        *,
        operation: str,
        status: str,
        dry_run: bool,
        detail: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        entry = GuardedWriteAuditEntry(
            timestamp_utc=_now_utc_iso(),
            operation=operation,
            status=status,
            dry_run=dry_run,
            detail=detail,
            metadata={} if metadata is None else dict(metadata),
        )
        self._write_audit_log.append(entry)

    @staticmethod
    def _extract_payload_value(
        response: Mapping[str, Any],
        *,
        command: str,
        payload_index: int,
    ) -> Any:
        if payload_index == 0 and "value" in response:
            return response["value"]

        payload = response.get("payload")
        if not isinstance(payload, list):
            raise NanonisProtocolError(f"Command '{command}' did not return a list payload.")
        if payload_index >= len(payload):
            raise NanonisProtocolError(
                f"Command '{command}' payload index {payload_index} is out of range."
            )
        return payload[payload_index]

    @staticmethod
    def _filter_specs(
        all_specs: Mapping[str, ParameterSpec],
        include_parameters: Sequence[str] | None,
    ) -> dict[str, ParameterSpec]:
        if include_parameters is None:
            return dict(all_specs)

        names = tuple(name.strip() for name in include_parameters if name.strip())
        if not names:
            raise ValueError("include_parameters must contain at least one non-empty name.")

        missing = [name for name in names if name not in all_specs]
        if missing:
            formatted = ", ".join(sorted(missing))
            raise ValueError(f"Unknown parameters requested in include_parameters: {formatted}")

        return {name: all_specs[name] for name in names}

    def _require_writable_spec(self, parameter_name: str) -> ParameterSpec:
        spec = self.parameter_spec(parameter_name)
        if spec.set_cmd is None:
            raise ValueError(f"Parameter '{spec.name}' is not writable.")
        if spec.safety is None:
            raise ValueError(f"Parameter '{spec.name}' is missing safety settings.")
        return spec

    def _require_current_numeric_value(self, spec: ParameterSpec) -> float:
        if spec.get_cmd is None:
            raise ValueError(
                f"Writable parameter '{spec.name}' must include get_cmd for guarded planning."
            )
        return float(self.get_parameter_value(spec.name))

    # ------------------------------------------------------------------
    # Scalar-safety guard + multi-field write strategies
    # ------------------------------------------------------------------
    def is_scalar_rampable(self, parameter_name: str) -> bool:
        """Whether a parameter's scalar value can be inferred safely.

        True when the parameter exposes at most one response field (unambiguous
        scalar read) or declares an explicit ``scalar_strategy``. Multi-field
        parameters without a strategy return False and are refused by scalar
        ``set``/``ramp`` operations.
        """
        return self._is_scalar_rampable_spec(self.parameter_spec(parameter_name))

    def _is_scalar_rampable_spec(self, spec: ParameterSpec) -> bool:
        if self._scalar_strategy_name(spec) is not None:
            return True
        return not spec.is_multi_field

    def _ensure_scalar_rampable(self, spec: ParameterSpec) -> None:
        if self._is_scalar_rampable_spec(spec):
            return
        raise PolicyViolation(
            f"Scalar set/ramp is blocked for multi-field parameter '{spec.name}': its "
            f"read field and write field are different physical quantities, so a single "
            f"scalar value cannot be applied safely. Use `nspmctl set {spec.name} --arg "
            f"<field>=<value> ...` providing every coupled field explicitly, or use a "
            f"dedicated scalar-coordinate parameter with validated tuple semantics "
            f"(e.g. 'zctrl_i_gain' for the Z-controller integral gain)."
        )

    def _scalar_strategy_name(self, spec: ParameterSpec) -> str | None:
        strategy = spec.scalar_strategy
        if strategy is None:
            return None
        if strategy not in _SUPPORTED_SCALAR_STRATEGIES:
            raise ValueError(
                f"Parameter '{spec.name}' declares unknown scalar_strategy '{strategy}'. "
                f"Supported: {', '.join(sorted(_SUPPORTED_SCALAR_STRATEGIES))}."
            )
        return strategy

    def _apply_scalar_strategy(
        self, spec: ParameterSpec, target_value: float, *, plan_only: bool
    ) -> Mapping[str, Any]:
        strategy = self._scalar_strategy_name(spec)
        if strategy in _ZCTRL_GAIN_STRATEGIES:
            return self._write_zctrl_gain_coordinate(
                spec, float(target_value), strategy=strategy, plan_only=plan_only
            )
        raise ValueError(f"Unsupported scalar strategy for parameter '{spec.name}'.")

    def _extract_scalar_strategy_target(
        self, spec: ParameterSpec, args: Mapping[str, Any]
    ) -> float:
        if spec.set_cmd is None:
            raise ValueError(f"Parameter '{spec.name}' is not writable.")
        strategy = self._scalar_strategy_name(spec)
        assert strategy is not None
        coord_index = _ZCTRL_GAIN_STRATEGIES[strategy]
        coord_name = self._zctrl_gain_arg_names(spec)[coord_index]
        coordinate_normalized = _normalize_field_name(coord_name)
        fields_by_normalized = {
            _normalize_field_name(field.name): field.name for field in spec.set_cmd.arg_fields
        }
        provided: dict[str, Any] = {}
        for key, value in args.items():
            normalized = _normalize_field_name(str(key))
            field_name = fields_by_normalized.get(normalized)
            if field_name is None:
                raise ValueError(f"Unknown argument for parameter '{spec.name}': {key}")
            provided[field_name] = value

        extra = [name for name in provided if _normalize_field_name(name) != coordinate_normalized]
        if extra:
            formatted = ", ".join(sorted(extra))
            raise ValueError(
                f"Parameter '{spec.name}' only accepts its scalar coordinate "
                f"'{coord_name}'; the other Z-controller gain fields are managed "
                f"automatically (I = P / T). Unsupported field(s): {formatted}. "
                f"For explicit (P, T, I) tuple control use `set zctrl_gain`."
            )
        if coord_name not in provided:
            raise ValueError(
                f"Parameter '{spec.name}' requires its coordinate target via "
                f"--arg {coord_name}=<value>."
            )
        return float(provided[coord_name])

    def _zctrl_gain_arg_names(self, spec: ParameterSpec) -> tuple[str, str, str]:
        fields = spec.set_cmd.arg_fields if spec.set_cmd is not None else ()
        if len(fields) != 3:
            raise ValueError(
                f"A ZCtrl gain scalar_strategy requires a 3-field ZCtrl gain set command "
                f"(P, T, I); parameter '{spec.name}' defines {len(fields)} set field(s)."
            )
        return fields[0].name, fields[1].name, fields[2].name

    @staticmethod
    def _require_positive_finite(spec: ParameterSpec, label: str, value: float) -> None:
        if not math.isfinite(value) or value <= 0.0:
            raise PolicyViolation(
                f"Cannot apply gain coordinate on '{spec.name}': {label} is non-positive "
                f"or non-finite ({value}). The controller couples the gains via I = P / T, "
                f"so the held/derived values must be positive."
            )

    def _compute_zctrl_gain_tuple(
        self,
        spec: ParameterSpec,
        strategy: str,
        current: tuple[float, float, float],
        target: float,
    ) -> tuple[float, float, float]:
        """Return the self-consistent (P, T, I) tuple for a coordinate ramp step.

        ``current`` is the freshly-read (P, T, I). ``target`` is the new value of
        the strategy's coordinate. The partner variable named in the strategy is
        held; the third is derived so that I = P / T holds exactly.
        """
        p0, t0, i0 = current
        if strategy == "zctrl_i_gain":  # ramp I, hold P -> T = P / I
            self._require_positive_finite(spec, "current P-gain", p0)
            return p0, p0 / target, target
        if strategy == "zctrl_t_const":  # ramp T, hold P -> I = P / T
            self._require_positive_finite(spec, "current P-gain", p0)
            return p0, target, p0 / target
        if strategy == "zctrl_p_gain_hold_t":  # ramp P, hold T -> I = P / T
            self._require_positive_finite(spec, "current time constant", t0)
            return target, t0, target / t0
        if strategy == "zctrl_p_gain_hold_i":  # ramp P, hold I -> T = P / I
            self._require_positive_finite(spec, "current I-gain", i0)
            return target, target / i0, i0
        raise ValueError(f"Unsupported ZCtrl gain strategy '{strategy}' for '{spec.name}'.")

    def _read_zctrl_gain_tuple(self, spec: ParameterSpec) -> tuple[float, float, float]:
        if spec.get_cmd is None:
            raise ValueError(f"Parameter '{spec.name}' is not readable.")
        response = self._call(spec.get_cmd.command, args={})
        payload = response.get("payload")
        if not isinstance(payload, list) or len(payload) < 3:
            raise NanonisProtocolError(
                f"Command '{spec.get_cmd.command}' must return [P, T, I]; got {payload!r}."
            )
        return float(payload[0]), float(payload[1]), float(payload[2])

    def _write_zctrl_gain_coordinate(
        self, spec: ParameterSpec, target: float, *, strategy: str, plan_only: bool
    ) -> Mapping[str, Any]:
        """Ramp one Z-controller gain coordinate with a self-consistent tuple.

        The controller has two degrees of freedom (P, and the integral action as
        I or T, coupled by I = P / T) and is PT-authoritative. Each strategy sets
        one coordinate while holding a partner and derives the third, then sends a
        complete (P, T, I) tuple so no coupled field is ever silently zeroed.
        """
        if spec.get_cmd is None or spec.set_cmd is None:
            raise ValueError(f"Parameter '{spec.name}' must be readable and writable.")
        if not math.isfinite(target) or target <= 0.0:
            raise PolicyViolation(
                f"Coordinate target for '{spec.name}' must be a positive, finite value; "
                f"got {target}."
            )

        # Enforce the configured channel bounds on structured/single writes too, not
        # only on the ramp planning path (which goes through plan_scalar_write_single_step).
        limit = self._write_policy.limits.get(spec.name)
        if limit is not None:
            if limit.min_value is not None and target < limit.min_value:
                raise PolicyViolation(
                    f"Coordinate target {target} for '{spec.name}' is below the "
                    f"configured minimum {limit.min_value}."
                )
            if limit.max_value is not None and target > limit.max_value:
                raise PolicyViolation(
                    f"Coordinate target {target} for '{spec.name}' exceeds the "
                    f"configured maximum {limit.max_value}."
                )

        p_name, t_name, i_name = self._zctrl_gain_arg_names(spec)
        current = self._read_zctrl_gain_tuple(spec)
        p_new, t_new, i_new = self._compute_zctrl_gain_tuple(spec, strategy, current, target)

        command_args: dict[str, Any] = {p_name: p_new, t_name: t_new, i_name: i_new}
        typed_args: dict[str, Any] = {}
        for arg_field in spec.set_cmd.arg_fields:
            if arg_field.name not in command_args:
                continue
            typed_args[arg_field.name] = _coerce_action_value(
                command_args[arg_field.name],
                value_type=arg_field.type,
                field_name=f"{spec.name}.{arg_field.name}",
            )

        self._write_policy.ensure_writes_enabled()
        dry_run = bool(plan_only or self._write_policy.dry_run)
        response: Mapping[str, Any] | None = None
        if not dry_run:
            response = self._call(spec.set_cmd.command, args=typed_args)

        p0, t0, i0 = current
        self._append_write_audit(
            operation=f"scalar_strategy:{spec.name}",
            status="dry_run" if dry_run else "applied",
            dry_run=dry_run,
            detail=f"Tuple-aware Z-controller gain write ({strategy}); sends complete (P, T, I).",
            metadata={
                "command": spec.set_cmd.command,
                "strategy": strategy,
                "args": _json_safe(typed_args),
                "previous_tuple": {"P": p0, "T": t0, "I": i0},
                "new_tuple": {"P": p_new, "T": t_new, "I": i_new},
                "coordinate_target": target,
            },
        )

        return {
            "name": spec.name,
            "command": spec.set_cmd.command,
            "args": typed_args,
            "dry_run": dry_run,
            "applied": not dry_run,
            "response": response,
            "strategy": strategy,
            "previous_tuple": {"P": p0, "T": t0, "I": i0},
            "new_tuple": {"P": p_new, "T": t_new, "I": i_new},
            "coordinate_target": target,
        }


def _build_channel_limits(
    specs: Iterable[ParameterSpec],
    *,
    default_ramp_interval_s: float,
) -> dict[str, ChannelLimit]:
    limits: dict[str, ChannelLimit] = {}
    for spec in specs:
        if not spec.writable:
            continue
        if spec.safety is None:
            raise ValueError(f"Writable parameter '{spec.name}' is missing safety configuration.")
        limits[spec.name] = _channel_limit_from_safety(
            spec.safety,
            default_ramp_interval_s=default_ramp_interval_s,
        )
    return limits


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _channel_limit_from_safety(
    safety: SafetySpec,
    *,
    default_ramp_interval_s: float,
) -> ChannelLimit:
    return ChannelLimit(
        min_value=None if safety.min_value is None else float(safety.min_value),
        max_value=None if safety.max_value is None else float(safety.max_value),
        max_step=None if safety.max_step is None else float(safety.max_step),
        max_slew_per_s=None if safety.max_slew_per_s is None else float(safety.max_slew_per_s),
        cooldown_s=None if safety.cooldown_s is None else float(safety.cooldown_s),
        ramp_interval_s=(
            default_ramp_interval_s
            if safety.ramp_interval_s is None
            else float(safety.ramp_interval_s)
        ),
    )


def _build_ramp_targets(*, start: float, end: float, step: float) -> tuple[float, ...]:
    start_value = float(start)
    end_value = float(end)
    step_value = float(step)
    if step_value <= 0:
        raise ValueError("Ramp step must be positive.")

    if math.isclose(start_value, end_value, rel_tol=0.0, abs_tol=1e-15):
        return (end_value,)

    direction = 1.0 if end_value > start_value else -1.0
    signed_step = step_value * direction

    targets: list[float] = []
    current = start_value
    for _ in range(1_000_000):
        if (direction > 0 and current >= end_value) or (direction < 0 and current <= end_value):
            targets.append(end_value)
            break

        targets.append(current)
        current = current + signed_step
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
    if not math.isclose(deduped_targets[-1], end_value, rel_tol=0.0, abs_tol=1e-15):
        deduped_targets.append(end_value)

    return tuple(deduped_targets)


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
    return str(value)
