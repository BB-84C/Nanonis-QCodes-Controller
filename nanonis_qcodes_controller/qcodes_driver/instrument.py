from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qcodes.instrument import Instrument
from qcodes.validators import Bool, Enum, Ints, Numbers

from nanonis_qcodes_controller.client import NanonisClient, build_client_from_settings
from nanonis_qcodes_controller.client.base import NanonisHealth
from nanonis_qcodes_controller.client.errors import NanonisProtocolError
from nanonis_qcodes_controller.config import load_settings
from nanonis_qcodes_controller.safety import (
    ChannelLimit,
    WriteExecutionReport,
    WritePlan,
    WritePolicy,
)
from nanonis_qcodes_controller.trajectory import TrajectoryJournal, TrajectoryStats

from .extensions import (
    DEFAULT_PARAMETERS_FILE,
    ParameterSpec,
    SafetySpec,
    ValidatorSpec,
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


class QcodesNanonisSTM(Instrument):  # type: ignore[misc,unused-ignore]
    def __init__(
        self,
        name: str,
        *,
        client: NanonisClient | None = None,
        config_file: str | Path | None = None,
        parameters_file: str | Path | None = None,
        include_parameters: Sequence[str] | None = None,
        write_policy: WritePolicy | None = None,
        trajectory_journal: TrajectoryJournal | None = None,
        auto_connect: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, **kwargs)

        self._owns_client = client is None
        self._client: NanonisClient
        self._owns_trajectory = False
        self._trajectory_journal: TrajectoryJournal | None = trajectory_journal
        self._last_state_values: dict[str, Any] = {}
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
        self._parameter_specs = self._filter_specs(all_specs, include_parameters)

        if write_policy is None:
            limits = _build_channel_limits(
                self._parameter_specs.values(),
                default_ramp_interval_s=settings.safety.default_ramp_interval_s,
            )
            self._write_policy = WritePolicy.from_settings(settings.safety, limits=limits)
        else:
            self._write_policy = write_policy

        if self._trajectory_journal is None and settings.trajectory.enabled:
            self._trajectory_journal = TrajectoryJournal(
                directory=settings.trajectory.directory,
                queue_size=settings.trajectory.queue_size,
                max_events_per_file=settings.trajectory.max_events_per_file,
            )
            self._trajectory_journal.start()
            self._owns_trajectory = True

        if auto_connect:
            self._client.connect()

        self._register_parameters()

    def close(self) -> None:
        try:
            if self._owns_client:
                self._client.close()
            if self._owns_trajectory and self._trajectory_journal is not None:
                self._trajectory_journal.close()
        finally:
            super().close()

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

    def writable_parameter_names(self) -> tuple[str, ...]:
        names = [spec.name for spec in self.parameter_specs() if spec.writable]
        return tuple(names)

    def readable_parameter_names(self) -> tuple[str, ...]:
        names = [spec.name for spec in self.parameter_specs() if spec.readable]
        return tuple(names)

    def guarded_write_audit_log(self) -> tuple[GuardedWriteAuditEntry, ...]:
        return tuple(self._write_audit_log)

    def trajectory_stats(self) -> TrajectoryStats | None:
        if self._trajectory_journal is None:
            return None
        return self._trajectory_journal.stats()

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

        response = self._call(spec.get_cmd.command, args=spec.get_cmd.args)
        raw_value = self._extract_payload_value(
            response,
            command=spec.get_cmd.command,
            payload_index=spec.get_cmd.payload_index,
        )
        value = _coerce_scalar_value(raw_value, value_type=spec.value_type)
        self._record_state_transition(state_key=spec.name, value=_state_value(value))
        return value

    def plan_parameter_single_step(
        self,
        parameter_name: str,
        target_value: float,
        *,
        reason: str | None = None,
        interval_s: float | None = None,
    ) -> WritePlan:
        spec = self._require_writable_spec(parameter_name)
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

    def _register_parameters(self) -> None:
        for spec in self.parameter_specs():
            parameter_kwargs: dict[str, Any] = {
                "name": spec.name,
                "label": spec.label,
                "unit": spec.unit,
                "get_cmd": (
                    (lambda name=spec.name: self.get_parameter_value(name))
                    if spec.readable
                    else False
                ),
                "set_cmd": (
                    (
                        lambda value, name=spec.name: self.set_parameter_single_step(
                            name, float(value)
                        )
                    )
                    if spec.writable
                    else False
                ),
                "snapshot_value": spec.snapshot_value,
            }
            validator = _validator_for_spec(spec.vals, value_type=spec.value_type)
            if validator is not None:
                parameter_kwargs["vals"] = validator

            self.add_parameter(**parameter_kwargs)

    def _call(self, command: str, *, args: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        call_start = time.perf_counter()
        args_digest = _args_hash(args)
        try:
            response = self._client.call(command, args=args)
        except Exception as exc:
            self._emit_trajectory_event(
                "command_result",
                {
                    "command": command,
                    "status": "error",
                    "latency_ms": (time.perf_counter() - call_start) * 1000.0,
                    "args_hash": args_digest,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            raise

        self._emit_trajectory_event(
            "command_result",
            {
                "command": command,
                "status": "ok",
                "latency_ms": (time.perf_counter() - call_start) * 1000.0,
                "args_hash": args_digest,
            },
        )
        return response

    def _send_parameter_value(self, spec: ParameterSpec, value: float) -> None:
        if spec.set_cmd is None:
            raise ValueError(f"Parameter '{spec.name}' is not writable.")

        typed_value = _coerce_scalar_value(value, value_type=spec.value_type)
        command_args = dict(spec.set_cmd.args)
        if spec.value_type == "bool":
            command_args[spec.set_cmd.value_arg] = int(bool(typed_value))
        elif spec.value_type == "int":
            command_args[spec.set_cmd.value_arg] = int(typed_value)
        elif spec.value_type == "float":
            command_args[spec.set_cmd.value_arg] = float(typed_value)
        else:
            command_args[spec.set_cmd.value_arg] = str(typed_value)

        _ = self._call(spec.set_cmd.command, args=command_args)

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
        self._emit_trajectory_event(
            "write_audit",
            {
                "operation": operation,
                "status": status,
                "dry_run": dry_run,
                "detail": detail,
                "metadata": _json_safe(entry.metadata),
            },
        )

    def _emit_trajectory_event(self, event_type: str, payload: Mapping[str, Any]) -> None:
        if self._trajectory_journal is None:
            return
        _ = self._trajectory_journal.emit(event_type, dict(payload))

    def _record_state_transition(self, *, state_key: str, value: Any) -> None:
        previous = self._last_state_values.get(state_key)
        if previous == value:
            return
        self._last_state_values[state_key] = value
        self._emit_trajectory_event(
            "state_transition",
            {
                "state_key": state_key,
                "old": _json_safe(previous),
                "new": _json_safe(value),
            },
        )

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


def _args_hash(args: Mapping[str, Any] | None) -> str:
    if args is None:
        text = "{}"
    else:
        text = json.dumps(_json_safe(dict(args)), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _state_value(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 15)
    return value


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


def _coerce_scalar_value(value: Any, *, value_type: str) -> Any:
    if value_type == "float":
        return float(value)
    if value_type == "int":
        return int(value)
    if value_type == "bool":
        return bool(value)
    if value_type == "str":
        return str(value)
    raise ValueError(f"Unsupported scalar value type: {value_type}")


def _validator_for_spec(vals: ValidatorSpec | None, *, value_type: str) -> Any | None:
    if vals is None:
        if value_type == "float":
            return Numbers()
        if value_type == "int":
            return Ints()
        if value_type == "bool":
            return Bool()
        return None

    if vals.kind == "numbers":
        if vals.min_value is None and vals.max_value is None:
            return Numbers()
        if vals.min_value is None:
            assert vals.max_value is not None
            max_value = float(vals.max_value)
            return Numbers(max_value=max_value)
        if vals.max_value is None:
            return Numbers(min_value=float(vals.min_value))
        return Numbers(min_value=float(vals.min_value), max_value=float(vals.max_value))
    if vals.kind == "ints":
        min_int = None if vals.min_value is None else int(vals.min_value)
        max_int = None if vals.max_value is None else int(vals.max_value)
        if min_int is None and max_int is None:
            return Ints()
        if min_int is None:
            assert max_int is not None
            return Ints(max_value=max_int)
        if max_int is None:
            return Ints(min_value=min_int)
        return Ints(min_value=min_int, max_value=max_int)
    if vals.kind == "bool":
        return Bool()
    if vals.kind == "enum":
        return Enum(*vals.choices)
    return None
