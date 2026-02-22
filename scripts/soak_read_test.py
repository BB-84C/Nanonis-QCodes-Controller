from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from nanonis_qcodes_controller.qcodes_driver import QcodesNanonisSTM


@dataclass
class SoakSummary:
    duration_target_s: float
    duration_actual_s: float
    interval_s: float
    loops_completed: int
    read_operations: int
    read_errors: int
    snapshot_errors: int
    reconnect_events: int
    reconnect_storm_detected: bool
    endpoint_history: list[str]
    loop_latency_ms_p50: float | None
    loop_latency_ms_p95: float | None
    loop_latency_ms_max: float | None
    pass_strict: bool


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a 30-60 minute read-only soak test using QcodesNanonisSTM."
    )
    parser.add_argument(
        "--duration-s",
        type=float,
        default=1800.0,
        help="Total soak duration in seconds (default: 1800 = 30 minutes).",
    )
    parser.add_argument(
        "--interval-s",
        type=float,
        default=0.5,
        help="Delay between read loops in seconds.",
    )
    parser.add_argument(
        "--print-every-s",
        type=float,
        default=60.0,
        help="Progress print interval in seconds.",
    )
    parser.add_argument(
        "--snapshot-every-s",
        type=float,
        default=120.0,
        help="Run snapshot(update=True) every N seconds.",
    )
    parser.add_argument(
        "--config-file",
        help="Optional YAML config path (defaults to env/config defaults).",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="Optional path to write JSON summary.",
    )
    args = parser.parse_args()

    if args.duration_s <= 0:
        parser.error("--duration-s must be > 0")
    if args.interval_s <= 0:
        parser.error("--interval-s must be > 0")
    if args.print_every_s <= 0:
        parser.error("--print-every-s must be > 0")
    if args.snapshot_every_s <= 0:
        parser.error("--snapshot-every-s must be > 0")

    instrument = QcodesNanonisSTM("nanonis_soak", config_file=args.config_file, auto_connect=True)

    loop_latencies_ms: list[float] = []
    endpoint_history: list[str] = []
    reconnect_times: list[float] = []

    read_operations = 0
    read_errors = 0
    snapshot_errors = 0

    start = time.perf_counter()
    next_print_time = start + args.print_every_s
    next_snapshot_time = start + args.snapshot_every_s

    health = instrument.client_health()
    endpoint = health.endpoint or "none"
    endpoint_history.append(endpoint)
    print(f"Start endpoint: {endpoint}")

    loop_index = 0

    try:
        while True:
            now = time.perf_counter()
            elapsed = now - start
            if elapsed >= args.duration_s:
                break

            loop_start = time.perf_counter()
            loop_index += 1

            try:
                _assert_finite_float(instrument.bias_v())
                _assert_finite_float(instrument.current_a())
                _assert_finite_float(instrument.zctrl_z_m())
                _assert_finite_float(instrument.zctrl_setpoint_a())
                bool(instrument.zctrl_on())
                int(instrument.scan_status_code())
                _assert_finite_float(instrument.scan_frame_center_x_m())
                _assert_finite_float(instrument.scan_frame_center_y_m())
                _assert_finite_float(instrument.scan_frame_width_m())
                _assert_finite_float(instrument.scan_frame_height_m())
                _assert_finite_float(instrument.scan_frame_angle_deg())
                int(instrument.signals_count())
                read_operations += 12
            except Exception as exc:
                read_errors += 1
                print(f"Read error at loop {loop_index}: {type(exc).__name__}: {exc}")

            if loop_index % 20 == 0:
                try:
                    names = instrument.signals_names()
                    if not isinstance(names, tuple):
                        raise TypeError("signals_names must be a tuple")
                    read_operations += 1
                except Exception as exc:
                    read_errors += 1
                    print(f"Signals error at loop {loop_index}: {type(exc).__name__}: {exc}")

            if now >= next_snapshot_time:
                try:
                    _ = instrument.snapshot(update=True)
                except Exception as exc:
                    snapshot_errors += 1
                    print(f"Snapshot error at loop {loop_index}: {type(exc).__name__}: {exc}")
                finally:
                    next_snapshot_time = now + args.snapshot_every_s

            health = instrument.client_health()
            current_endpoint = health.endpoint or "none"
            if current_endpoint != endpoint_history[-1]:
                endpoint_history.append(current_endpoint)
                reconnect_times.append(now)

            loop_elapsed_ms = (time.perf_counter() - loop_start) * 1000.0
            loop_latencies_ms.append(loop_elapsed_ms)

            if now >= next_print_time:
                print(
                    "progress "
                    f"elapsed={elapsed:.1f}s loops={loop_index} "
                    f"read_errors={read_errors} snapshot_errors={snapshot_errors} "
                    f"endpoints={len(endpoint_history)}"
                )
                next_print_time = now + args.print_every_s

            sleep_s = args.interval_s - (time.perf_counter() - loop_start)
            if sleep_s > 0:
                time.sleep(sleep_s)
    finally:
        instrument.close()

    duration_actual_s = time.perf_counter() - start
    reconnect_storm_detected = _reconnect_storm_detected(reconnect_times)

    summary = SoakSummary(
        duration_target_s=args.duration_s,
        duration_actual_s=duration_actual_s,
        interval_s=args.interval_s,
        loops_completed=loop_index,
        read_operations=read_operations,
        read_errors=read_errors,
        snapshot_errors=snapshot_errors,
        reconnect_events=max(0, len(endpoint_history) - 1),
        reconnect_storm_detected=reconnect_storm_detected,
        endpoint_history=endpoint_history,
        loop_latency_ms_p50=_percentile(loop_latencies_ms, 50.0),
        loop_latency_ms_p95=_percentile(loop_latencies_ms, 95.0),
        loop_latency_ms_max=max(loop_latencies_ms) if loop_latencies_ms else None,
        pass_strict=(read_errors == 0 and snapshot_errors == 0 and not reconnect_storm_detected),
    )

    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(asdict(summary), indent=2), encoding="utf-8")

    print("-")
    print(json.dumps(asdict(summary), indent=2))
    return 0 if summary.pass_strict else 1


def _assert_finite_float(value: Any) -> None:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"Non-finite numeric value: {value}")


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]

    ordered = sorted(values)
    idx = (len(ordered) - 1) * (percentile / 100.0)
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    weight = idx - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def _reconnect_storm_detected(event_times: list[float]) -> bool:
    if len(event_times) < 4:
        return False

    for end in range(3, len(event_times)):
        window_s = event_times[end] - event_times[end - 3]
        if window_s <= 60.0:
            return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
