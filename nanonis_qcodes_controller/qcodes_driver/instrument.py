from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, cast

from qcodes.instrument import Instrument
from qcodes.validators import Bool, Ints, Numbers

from nanonis_qcodes_controller.client import NanonisClient, build_client_from_settings
from nanonis_qcodes_controller.client.base import NanonisHealth
from nanonis_qcodes_controller.client.errors import NanonisProtocolError
from nanonis_qcodes_controller.config import load_settings
from nanonis_qcodes_controller.safety import WriteExecutionReport, WritePlan, WritePolicy
from nanonis_qcodes_controller.trajectory import TrajectoryJournal, TrajectoryStats

from .extensions import ScalarParameterSpec, load_scalar_parameter_specs


@dataclass(frozen=True)
class ScanFrameState:
    center_x_m: float
    center_y_m: float
    width_m: float
    height_m: float
    angle_deg: float

    @classmethod
    def from_payload(cls, payload: list[Any]) -> ScanFrameState:
        if len(payload) < 5:
            raise NanonisProtocolError("Scan frame payload must have five values.")
        return cls(
            center_x_m=float(payload[0]),
            center_y_m=float(payload[1]),
            width_m=float(payload[2]),
            height_m=float(payload[3]),
            angle_deg=float(payload[4]),
        )

    def as_command_args(self) -> dict[str, float]:
        return {
            "Center_X_m": float(self.center_x_m),
            "Center_Y_m": float(self.center_y_m),
            "Width_m": float(self.width_m),
            "Height_m": float(self.height_m),
            "Angle_deg": float(self.angle_deg),
        }


@dataclass(frozen=True)
class ScanFrameWritePlan:
    current_frame: ScanFrameState
    target_frame: ScanFrameState
    steps: tuple[ScanFrameState, ...]
    interval_s: float
    dry_run: bool
    component_plans: Mapping[str, WritePlan]
    reason: str | None = None

    @property
    def step_count(self) -> int:
        return len(self.steps)


@dataclass(frozen=True)
class ScanFrameWriteReport:
    dry_run: bool
    attempted_steps: int
    applied_steps: int
    initial_frame: ScanFrameState
    target_frame: ScanFrameState
    final_frame: ScanFrameState


@dataclass(frozen=True)
class GuardedWriteAuditEntry:
    timestamp_s: float
    operation: str
    status: str
    dry_run: bool
    detail: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


class QcodesNanonisSTM(Instrument):  # type: ignore[misc,unused-ignore]
    def __init__(
        self,
        name: str,
        *,
        client: NanonisClient | None = None,
        config_file: str | Path | None = None,
        write_policy: WritePolicy | None = None,
        trajectory_journal: TrajectoryJournal | None = None,
        extra_parameters_manifest: str | Path | None = None,
        auto_connect: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, **kwargs)
        self._owns_client = client is None
        self._client: NanonisClient
        self._owns_trajectory = False
        self._trajectory_journal: TrajectoryJournal | None = trajectory_journal
        self._last_state_values: dict[str, Any] = {}
        self._dynamic_parameter_specs: dict[str, ScalarParameterSpec] = {}

        resolved_policy: WritePolicy
        settings = None
        if client is None:
            settings = load_settings(config_file=config_file)
            self._client = build_client_from_settings(settings.nanonis)
            resolved_policy = WritePolicy.from_settings(settings.safety)
        else:
            self._client = client
            resolved_policy = WritePolicy()

        self._write_policy = write_policy if write_policy is not None else resolved_policy
        self._write_audit_log: list[GuardedWriteAuditEntry] = []

        if (
            self._trajectory_journal is None
            and settings is not None
            and settings.trajectory.enabled
        ):
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
        if extra_parameters_manifest is not None:
            _ = self.load_scalar_parameter_manifest(extra_parameters_manifest)

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

        command_names = sorted(list_commands())
        if match is None:
            return tuple(command_names)

        token = match.strip().lower()
        if not token:
            return tuple(command_names)
        return tuple(command for command in command_names if token in command.lower())

    def call_backend_command(
        self,
        command: str,
        *,
        args: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        return self._call(command, args=args)

    @property
    def write_policy(self) -> WritePolicy:
        return self._write_policy

    def dynamic_parameter_specs(self) -> tuple[ScalarParameterSpec, ...]:
        names = sorted(self._dynamic_parameter_specs)
        return tuple(self._dynamic_parameter_specs[name] for name in names)

    def load_scalar_parameter_manifest(self, manifest_file: str | Path) -> tuple[str, ...]:
        specs = load_scalar_parameter_specs(manifest_file)
        registered_names: list[str] = []
        for spec in specs:
            self.register_scalar_parameter(spec)
            registered_names.append(spec.name)
        return tuple(registered_names)

    def register_scalar_parameter(self, spec: ScalarParameterSpec) -> None:
        parameter_name = spec.name
        if parameter_name in self.parameters or hasattr(self, parameter_name):
            raise ValueError(f"Parameter '{parameter_name}' is already registered.")

        call_args = dict(spec.args)

        def get_value() -> Any:
            response = self._call(
                spec.command,
                args=call_args if call_args else None,
            )
            raw_value = self._extract_payload_value(
                response,
                command=spec.command,
                payload_index=spec.payload_index,
            )
            return _coerce_scalar_value(raw_value, value_type=spec.value_type)

        parameter_kwargs: dict[str, Any] = {
            "name": parameter_name,
            "label": spec.label or parameter_name,
            "unit": spec.unit,
            "get_cmd": get_value,
            "set_cmd": False,
            "snapshot_value": spec.snapshot_value,
        }
        validator = _validator_for_value_type(spec.value_type)
        if validator is not None:
            parameter_kwargs["vals"] = validator

        self.add_parameter(**parameter_kwargs)
        self._dynamic_parameter_specs[parameter_name] = spec

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
            "model": "STM Simulator Bridge",
            "serial": health.endpoint,
            "firmware": self._client.version(),
        }

    def _register_parameters(self) -> None:
        self.add_parameter(
            "bias_v",
            label="Bias",
            unit="V",
            get_cmd=self._get_bias_v,
            set_cmd=False,
            vals=Numbers(),
        )
        self.add_parameter(
            "current_a",
            label="Tunnel Current",
            unit="A",
            get_cmd=self._get_current_a,
            set_cmd=False,
            vals=Numbers(),
        )
        self.add_parameter(
            "zctrl_z_m",
            label="Z Position",
            unit="m",
            get_cmd=self._get_zctrl_z_m,
            set_cmd=False,
            vals=Numbers(),
        )
        self.add_parameter(
            "zctrl_setpoint_a",
            label="Z Setpoint",
            unit="A",
            get_cmd=self._get_zctrl_setpoint_a,
            set_cmd=False,
            vals=Numbers(),
        )
        self.add_parameter(
            "zctrl_on",
            label="Z Controller Enabled",
            get_cmd=self._get_zctrl_on,
            set_cmd=False,
            vals=Bool(),
        )
        self.add_parameter(
            "scan_status_code",
            label="Scan Status Code",
            get_cmd=self._get_scan_status_code,
            set_cmd=False,
            vals=Ints(),
        )
        self.add_parameter(
            "scan_frame_center_x_m",
            label="Scan Frame Center X",
            unit="m",
            get_cmd=self._get_scan_frame_center_x_m,
            set_cmd=False,
            vals=Numbers(),
        )
        self.add_parameter(
            "scan_frame_center_y_m",
            label="Scan Frame Center Y",
            unit="m",
            get_cmd=self._get_scan_frame_center_y_m,
            set_cmd=False,
            vals=Numbers(),
        )
        self.add_parameter(
            "scan_frame_width_m",
            label="Scan Frame Width",
            unit="m",
            get_cmd=self._get_scan_frame_width_m,
            set_cmd=False,
            vals=Numbers(),
        )
        self.add_parameter(
            "scan_frame_height_m",
            label="Scan Frame Height",
            unit="m",
            get_cmd=self._get_scan_frame_height_m,
            set_cmd=False,
            vals=Numbers(),
        )
        self.add_parameter(
            "scan_frame_angle_deg",
            label="Scan Frame Angle",
            unit="deg",
            get_cmd=self._get_scan_frame_angle_deg,
            set_cmd=False,
            vals=Numbers(),
        )
        self.add_parameter(
            "signals_table_size_bytes",
            label="Signals Table Size",
            unit="B",
            get_cmd=self._get_signals_table_size_bytes,
            set_cmd=False,
            vals=Ints(),
        )
        self.add_parameter(
            "signals_count",
            label="Signals Count",
            get_cmd=self._get_signals_count,
            set_cmd=False,
            vals=Ints(),
        )
        self.add_parameter(
            "signals_names",
            label="Signals Names",
            get_cmd=self._get_signals_names,
            set_cmd=False,
            snapshot_value=False,
        )

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

    def _get_scalar_float(self, command: str) -> float:
        value = self._extract_scalar_value(self._call(command), command=command)
        return float(value)

    def _get_scalar_int(self, command: str) -> int:
        value = self._extract_scalar_value(self._call(command), command=command)
        return int(value)

    def _get_bias_v(self) -> float:
        bias_v = self._get_scalar_float("Bias_Get")
        self._record_state_transition(state_key="bias_v", value=round(bias_v, 12))
        return bias_v

    def _get_current_a(self) -> float:
        current_a = self._get_scalar_float("Current_Get")
        self._record_state_transition(state_key="current_a", value=round(current_a, 15))
        return current_a

    def _get_zctrl_z_m(self) -> float:
        return self._get_scalar_float("ZCtrl_ZPosGet")

    def _get_zctrl_setpoint_a(self) -> float:
        setpoint_a = self._get_scalar_float("ZCtrl_SetpntGet")
        self._record_state_transition(state_key="zctrl_setpoint_a", value=round(setpoint_a, 15))
        return setpoint_a

    def _get_zctrl_on(self) -> bool:
        zctrl_on = bool(self._get_scalar_int("ZCtrl_OnOffGet"))
        self._record_state_transition(state_key="zctrl_on", value=zctrl_on)
        return zctrl_on

    def _get_scan_status_code(self) -> int:
        status_code = self._get_scalar_int("Scan_StatusGet")
        self._record_state_transition(
            state_key="scan_status_code",
            value=status_code,
        )
        return status_code

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

    def _get_scan_frame_center_x_m(self) -> float:
        return self._get_scan_frame().center_x_m

    def _get_scan_frame_center_y_m(self) -> float:
        return self._get_scan_frame().center_y_m

    def _get_scan_frame_width_m(self) -> float:
        return self._get_scan_frame().width_m

    def _get_scan_frame_height_m(self) -> float:
        return self._get_scan_frame().height_m

    def _get_scan_frame_angle_deg(self) -> float:
        return self._get_scan_frame().angle_deg

    def _get_signals_table_size_bytes(self) -> int:
        payload = self._get_signals_payload()
        return int(payload[0])

    def _get_signals_count(self) -> int:
        payload = self._get_signals_payload()
        return int(payload[1])

    def _get_signals_names(self) -> tuple[str, ...]:
        payload = self._get_signals_payload()
        names = payload[2]
        if not isinstance(names, list):
            raise NanonisProtocolError("Signals_NamesGet payload index 2 must be a list of names.")
        return tuple(str(item) for item in names)

    def plan_bias_v_set(
        self,
        target_v: float,
        *,
        confirmed: bool = False,
        reason: str | None = None,
    ) -> WritePlan:
        return self._write_policy.plan_scalar_write(
            channel="bias_v",
            current_value=self._get_bias_v(),
            target_value=target_v,
            confirmed=confirmed,
            reason=reason,
        )

    def set_bias_v_guarded(
        self,
        target_v: float,
        *,
        confirmed: bool = False,
        reason: str | None = None,
    ) -> WriteExecutionReport:
        return self._run_guarded_scalar_write(
            operation="bias_v_set",
            planner=lambda: self.plan_bias_v_set(target_v, confirmed=confirmed, reason=reason),
            command="Bias_Set",
            argument_name="Bias_value_V",
        )

    def plan_zctrl_setpoint_a_set(
        self,
        target_a: float,
        *,
        confirmed: bool = False,
        reason: str | None = None,
    ) -> WritePlan:
        return self._write_policy.plan_scalar_write(
            channel="setpoint_a",
            current_value=self._get_zctrl_setpoint_a(),
            target_value=target_a,
            confirmed=confirmed,
            reason=reason,
        )

    def set_zctrl_setpoint_a_guarded(
        self,
        target_a: float,
        *,
        confirmed: bool = False,
        reason: str | None = None,
    ) -> WriteExecutionReport:
        return self._run_guarded_scalar_write(
            operation="setpoint_a_set",
            planner=lambda: self.plan_zctrl_setpoint_a_set(
                target_a, confirmed=confirmed, reason=reason
            ),
            command="ZCtrl_SetpntSet",
            argument_name="Z_Controller_setpoint",
        )

    def plan_scan_frame_set(
        self,
        *,
        center_x_m: float,
        center_y_m: float,
        width_m: float,
        height_m: float,
        angle_deg: float,
        confirmed: bool = False,
        reason: str | None = None,
    ) -> ScanFrameWritePlan:
        current = self._get_scan_frame()
        target = ScanFrameState(
            center_x_m=float(center_x_m),
            center_y_m=float(center_y_m),
            width_m=float(width_m),
            height_m=float(height_m),
            angle_deg=float(angle_deg),
        )

        component_plans = {
            "scan_frame_center_x_m": self._write_policy.plan_scalar_write(
                channel="scan_frame_center_x_m",
                current_value=current.center_x_m,
                target_value=target.center_x_m,
                confirmed=confirmed,
                reason=reason,
            ),
            "scan_frame_center_y_m": self._write_policy.plan_scalar_write(
                channel="scan_frame_center_y_m",
                current_value=current.center_y_m,
                target_value=target.center_y_m,
                confirmed=confirmed,
                reason=reason,
            ),
            "scan_frame_width_m": self._write_policy.plan_scalar_write(
                channel="scan_frame_width_m",
                current_value=current.width_m,
                target_value=target.width_m,
                confirmed=confirmed,
                reason=reason,
            ),
            "scan_frame_height_m": self._write_policy.plan_scalar_write(
                channel="scan_frame_height_m",
                current_value=current.height_m,
                target_value=target.height_m,
                confirmed=confirmed,
                reason=reason,
            ),
            "scan_frame_angle_deg": self._write_policy.plan_scalar_write(
                channel="scan_frame_angle_deg",
                current_value=current.angle_deg,
                target_value=target.angle_deg,
                confirmed=confirmed,
                reason=reason,
            ),
        }

        max_steps = max(plan.step_count for plan in component_plans.values())
        interval_s = max(plan.interval_s for plan in component_plans.values())
        steps = _interpolate_scan_frame_steps(current=current, target=target, step_count=max_steps)

        return ScanFrameWritePlan(
            current_frame=current,
            target_frame=target,
            steps=steps,
            interval_s=interval_s,
            dry_run=any(plan.dry_run for plan in component_plans.values()),
            component_plans=component_plans,
            reason=reason,
        )

    def set_scan_frame_guarded(
        self,
        *,
        center_x_m: float,
        center_y_m: float,
        width_m: float,
        height_m: float,
        angle_deg: float,
        confirmed: bool = False,
        reason: str | None = None,
    ) -> ScanFrameWriteReport:
        operation = "scan_frame_set"
        try:
            plan = self.plan_scan_frame_set(
                center_x_m=center_x_m,
                center_y_m=center_y_m,
                width_m=width_m,
                height_m=height_m,
                angle_deg=angle_deg,
                confirmed=confirmed,
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

        attempted_steps = plan.step_count
        if plan.dry_run:
            report = ScanFrameWriteReport(
                dry_run=True,
                attempted_steps=attempted_steps,
                applied_steps=0,
                initial_frame=plan.current_frame,
                target_frame=plan.target_frame,
                final_frame=plan.steps[-1],
            )
            self._append_write_audit(
                operation=operation,
                status="dry_run",
                dry_run=True,
                detail="Scan frame write was planned but not applied.",
                metadata={
                    "attempted_steps": attempted_steps,
                    "applied_steps": 0,
                    "target_frame": plan.target_frame,
                },
            )
            return report

        applied_steps = 0
        try:
            for step_index, step in enumerate(plan.steps):
                _ = self._call("Scan_FrameSet", args=step.as_command_args())
                applied_steps += 1
                if step_index < attempted_steps - 1 and plan.interval_s > 0:
                    time.sleep(plan.interval_s)
        except Exception as exc:
            self._append_write_audit(
                operation=operation,
                status="failed",
                dry_run=False,
                detail=f"{type(exc).__name__}: {exc}",
                metadata={
                    "attempted_steps": attempted_steps,
                    "applied_steps": applied_steps,
                },
            )
            raise

        write_timestamp = time.monotonic()
        for channel_name in plan.component_plans:
            self._write_policy.record_write(channel=channel_name, at_time_s=write_timestamp)

        final_frame = self._get_scan_frame()
        report = ScanFrameWriteReport(
            dry_run=False,
            attempted_steps=attempted_steps,
            applied_steps=applied_steps,
            initial_frame=plan.current_frame,
            target_frame=plan.target_frame,
            final_frame=final_frame,
        )
        self._append_write_audit(
            operation=operation,
            status="applied",
            dry_run=False,
            detail="Scan frame write applied.",
            metadata={
                "attempted_steps": attempted_steps,
                "applied_steps": applied_steps,
                "target_frame": plan.target_frame,
                "final_frame": final_frame,
            },
        )
        return report

    def _get_scan_frame(self) -> ScanFrameState:
        response = self._call("Scan_FrameGet")
        payload = response.get("payload")
        if not isinstance(payload, list):
            raise NanonisProtocolError("Scan_FrameGet must return a list payload.")
        return ScanFrameState.from_payload(payload)

    def _get_signals_payload(self) -> list[Any]:
        response = self._call("Signals_NamesGet")
        payload = response.get("payload")
        if not isinstance(payload, list) or len(payload) < 3:
            raise NanonisProtocolError("Signals_NamesGet must return [size_bytes, count, names].")
        return payload

    def _execute_scalar_write(
        self,
        plan: WritePlan,
        *,
        command: str,
        argument_name: str,
    ) -> WriteExecutionReport:
        def send_step(value: float) -> None:
            _ = self._call(command, args={argument_name: float(value)})

        return self._write_policy.execute_plan(plan, send_step=send_step)

    def _run_guarded_scalar_write(
        self,
        *,
        operation: str,
        planner: Callable[[], WritePlan],
        command: str,
        argument_name: str,
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
            report = self._execute_scalar_write(plan, command=command, argument_name=argument_name)
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
            timestamp_s=time.time(),
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
    def _extract_scalar_value(response: Mapping[str, Any], *, command: str) -> Any:
        return QcodesNanonisSTM._extract_payload_value(response, command=command, payload_index=0)


def _interpolate_scan_frame_steps(
    *,
    current: ScanFrameState,
    target: ScanFrameState,
    step_count: int,
) -> tuple[ScanFrameState, ...]:
    if step_count <= 0:
        return (target,)

    steps: list[ScanFrameState] = []
    for step_index in range(1, step_count + 1):
        fraction = step_index / step_count
        steps.append(
            ScanFrameState(
                center_x_m=current.center_x_m + (target.center_x_m - current.center_x_m) * fraction,
                center_y_m=current.center_y_m + (target.center_y_m - current.center_y_m) * fraction,
                width_m=current.width_m + (target.width_m - current.width_m) * fraction,
                height_m=current.height_m + (target.height_m - current.height_m) * fraction,
                angle_deg=current.angle_deg + (target.angle_deg - current.angle_deg) * fraction,
            )
        )

    if steps[-1] != target:
        steps[-1] = target

    return tuple(steps)


def _args_hash(args: Mapping[str, Any] | None) -> str:
    if args is None:
        text = "{}"
    else:
        text = json.dumps(_json_safe(dict(args)), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(cast(Any, value)))
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


def _validator_for_value_type(value_type: str) -> Any | None:
    if value_type == "float":
        return Numbers()
    if value_type == "int":
        return Ints()
    if value_type == "bool":
        return Bool()
    return None
