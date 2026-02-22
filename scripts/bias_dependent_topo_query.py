from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from nanonis_qcodes_controller.config import load_settings
from nanonis_qcodes_controller.qcodes_driver import QcodesNanonisSTM
from nanonis_qcodes_controller.safety import ChannelLimit, WriteExecutionReport, WritePolicy
from nanonis_qcodes_controller.trajectory import TrajectoryJournal, read_events


@dataclass(frozen=True)
class SweepStepResult:
    index: int
    bias_mv: float
    bias_v: float
    setpoint_a: float
    setpoint_pa: float
    bias_report: dict[str, Any]
    setpoint_report: dict[str, Any]
    scan_timed_out: bool
    scan_file_path: str
    elapsed_s: float
    readback_bias_v: float
    readback_setpoint_a: float
    readback_current_a: float


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a bias-dependent topography scan query with constant junction resistance."
    )
    parser.add_argument("--start-bias-mv", type=float, default=50.0)
    parser.add_argument("--stop-bias-mv", type=float, default=150.0)
    parser.add_argument("--step-bias-mv", type=float, default=25.0)
    parser.add_argument("--start-current-pa", type=float, default=100.0)
    parser.add_argument(
        "--direction",
        choices=("down", "up"),
        default="down",
        help="Scan direction for Scan_Action start.",
    )
    parser.add_argument(
        "--scan-timeout-ms",
        type=int,
        default=900000,
        help="Per-scan wait timeout in milliseconds.",
    )
    parser.add_argument(
        "--restore-initial",
        action="store_true",
        default=True,
        help="Restore initial bias/setpoint at the end (default true).",
    )
    parser.add_argument(
        "--no-restore-initial",
        dest="restore_initial",
        action="store_false",
        help="Do not restore initial bias/setpoint.",
    )
    parser.add_argument("--config-file")
    parser.add_argument(
        "--output-json",
        type=Path,
        help="Optional JSON output path. Defaults to artifacts/bias_dependent_topo_query.json",
    )
    parser.add_argument(
        "--trajectory-enable",
        action="store_true",
        help="Enable local non-blocking trajectory journal for this run.",
    )
    parser.add_argument(
        "--trajectory-dir",
        type=Path,
        help="Trajectory journal directory (default from config or artifacts/trajectory).",
    )
    args = parser.parse_args()

    if args.step_bias_mv <= 0:
        parser.error("--step-bias-mv must be > 0")
    if args.start_current_pa == 0:
        parser.error("--start-current-pa must be non-zero")

    bias_points_mv = _build_bias_points(
        start_mv=args.start_bias_mv,
        stop_mv=args.stop_bias_mv,
        step_mv=args.step_bias_mv,
    )
    if not bias_points_mv:
        parser.error("No bias points generated; check start/stop/step settings.")

    start_bias_v = args.start_bias_mv * 1e-3
    start_current_a = args.start_current_pa * 1e-12
    junction_resistance_ohm = abs(start_bias_v / start_current_a)
    current_sign = 1.0 if start_current_a >= 0 else -1.0

    settings = load_settings(config_file=args.config_file)
    live_policy = WritePolicy(
        allow_writes=True,
        dry_run=False,
        limits={
            channel: ChannelLimit.from_settings(limit)
            for channel, limit in settings.safety.limits.items()
        },
    )

    trajectory_journal: TrajectoryJournal | None = None
    trajectory_directory: Path | None = None
    if args.trajectory_enable:
        resolved_trajectory_directory = args.trajectory_dir or Path(settings.trajectory.directory)
        trajectory_directory = resolved_trajectory_directory
        trajectory_journal = TrajectoryJournal(
            directory=resolved_trajectory_directory,
            queue_size=settings.trajectory.queue_size,
            max_events_per_file=settings.trajectory.max_events_per_file,
        )
        trajectory_journal.start()

    instrument = QcodesNanonisSTM(
        "nanonis_bias_query",
        config_file=args.config_file,
        write_policy=live_policy,
        trajectory_journal=trajectory_journal,
        auto_connect=True,
    )

    summary: dict[str, Any] = {
        "started_at": time.time(),
        "endpoint": instrument.client_health().endpoint,
        "junction_resistance_ohm": junction_resistance_ohm,
        "bias_points_mv": bias_points_mv,
        "scan_direction": args.direction,
        "scan_timeout_ms": args.scan_timeout_ms,
        "trajectory_enabled": trajectory_journal is not None,
        "steps": [],
        "restored_initial": False,
    }

    initial_bias_v = float(instrument.bias_v())
    initial_setpoint_a = float(instrument.zctrl_setpoint_a())
    summary["initial_bias_v"] = initial_bias_v
    summary["initial_setpoint_a"] = initial_setpoint_a

    if not instrument.zctrl_on():
        instrument.close()
        raise RuntimeError("Z controller is OFF. Turn Z feedback ON before running this query.")

    if instrument.scan_status_code() != 0:
        instrument.close()
        raise RuntimeError("Scan is already running. Stop the current scan and retry.")

    previous_bias_v = initial_bias_v
    final_bias_readback_v = initial_bias_v
    final_setpoint_readback_a = initial_setpoint_a
    audit_tail: list[dict[str, Any]] = []
    run_error: Exception | None = None

    try:
        for index, bias_mv in enumerate(bias_points_mv, start=1):
            step_start = time.perf_counter()
            bias_v = bias_mv * 1e-3
            setpoint_a = current_sign * abs(bias_v) / junction_resistance_ohm

            if bias_v >= previous_bias_v:
                bias_report = instrument.set_bias_v_guarded(
                    bias_v,
                    reason=f"bias-sweep step {index} bias-first",
                    confirmed=True,
                )
                setpoint_report = instrument.set_zctrl_setpoint_a_guarded(
                    setpoint_a,
                    reason=f"bias-sweep step {index} setpoint-second",
                    confirmed=True,
                )
            else:
                setpoint_report = instrument.set_zctrl_setpoint_a_guarded(
                    setpoint_a,
                    reason=f"bias-sweep step {index} setpoint-first",
                    confirmed=True,
                )
                bias_report = instrument.set_bias_v_guarded(
                    bias_v,
                    reason=f"bias-sweep step {index} bias-second",
                    confirmed=True,
                )

            instrument.start_scan(direction_up=args.direction == "up")
            timed_out, file_path = instrument.wait_end_of_scan(timeout_ms=args.scan_timeout_ms)
            if timed_out:
                instrument.stop_scan(direction_up=args.direction == "up")
                raise TimeoutError(
                    f"Scan timed out at step {index} (bias={bias_mv} mV). "
                    f"Timeout={args.scan_timeout_ms} ms"
                )

            step_result = SweepStepResult(
                index=index,
                bias_mv=bias_mv,
                bias_v=bias_v,
                setpoint_a=setpoint_a,
                setpoint_pa=setpoint_a * 1e12,
                bias_report=_report_to_dict(bias_report),
                setpoint_report=_report_to_dict(setpoint_report),
                scan_timed_out=timed_out,
                scan_file_path=file_path,
                elapsed_s=time.perf_counter() - step_start,
                readback_bias_v=float(instrument.bias_v()),
                readback_setpoint_a=float(instrument.zctrl_setpoint_a()),
                readback_current_a=float(instrument.current_a()),
            )
            summary["steps"].append(asdict(step_result))
            previous_bias_v = bias_v

            print(
                f"step {index}/{len(bias_points_mv)}: bias={bias_mv:.1f} mV "
                f"setpoint={setpoint_a * 1e12:.2f} pA done "
                f"elapsed={step_result.elapsed_s:.2f}s"
            )

        summary["status"] = "completed"
    except Exception as exc:
        run_error = exc
        summary["status"] = "failed"
        summary["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if args.restore_initial:
            try:
                _ = instrument.set_zctrl_setpoint_a_guarded(
                    initial_setpoint_a,
                    reason="restore-initial-setpoint",
                    confirmed=True,
                )
                _ = instrument.set_bias_v_guarded(
                    initial_bias_v,
                    reason="restore-initial-bias",
                    confirmed=True,
                )
                summary["restored_initial"] = True
            except Exception as exc:
                summary["restore_error"] = f"{type(exc).__name__}: {exc}"

        try:
            final_bias_readback_v = float(instrument.bias_v())
            final_setpoint_readback_a = float(instrument.zctrl_setpoint_a())
        except Exception as exc:
            summary["final_readback_error"] = f"{type(exc).__name__}: {exc}"

        audit_tail = [
            {
                "timestamp_s": item.timestamp_s,
                "operation": item.operation,
                "status": item.status,
                "dry_run": item.dry_run,
                "detail": item.detail,
            }
            for item in instrument.guarded_write_audit_log()[-10:]
        ]
        instrument.close()

    summary["finished_at"] = time.time()
    summary["final_bias_v"] = final_bias_readback_v
    summary["final_setpoint_a"] = final_setpoint_readback_a
    summary["audit_tail"] = audit_tail

    if trajectory_journal is not None and trajectory_directory is not None:
        trajectory_journal.close()
        summary["trajectory_directory"] = str(trajectory_directory)
        summary["trajectory_stats"] = asdict(trajectory_journal.stats())
        summary["trajectory_tail"] = read_events(trajectory_directory, limit=20)

    output_path = args.output_json or Path("artifacts/bias_dependent_topo_query.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"summary_file: {output_path}")

    if run_error is not None:
        print(f"run_failed: {type(run_error).__name__}: {run_error}")
        return 1

    return 0


def _build_bias_points(*, start_mv: float, stop_mv: float, step_mv: float) -> list[float]:
    direction = 1.0 if stop_mv >= start_mv else -1.0
    effective_step = step_mv * direction

    points: list[float] = []
    current = start_mv
    if direction > 0:
        while current <= stop_mv + 1e-9:
            points.append(round(current, 9))
            current += effective_step
    else:
        while current >= stop_mv - 1e-9:
            points.append(round(current, 9))
            current += effective_step

    return points


def _report_to_dict(report: WriteExecutionReport) -> dict[str, Any]:
    return {
        "channel": report.channel,
        "dry_run": report.dry_run,
        "attempted_steps": report.attempted_steps,
        "applied_steps": report.applied_steps,
        "initial_value": report.initial_value,
        "target_value": report.target_value,
        "final_value": report.final_value,
    }


if __name__ == "__main__":
    raise SystemExit(main())
