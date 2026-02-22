from __future__ import annotations

import datetime as dt
import importlib
import inspect
import socket
import statistics
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast


@dataclass(frozen=True)
class CommandProbeResult:
    attempted: bool
    success: bool
    backend: str
    command: str | None
    duration_ms: float | None
    detail: str


@dataclass(frozen=True)
class PortProbeResult:
    host: str
    port: int
    attempts: int
    success_count: int
    median_latency_ms: float | None
    min_latency_ms: float | None
    max_latency_ms: float | None
    last_error: str | None
    command_probe: CommandProbeResult | None

    @property
    def open(self) -> bool:
        return self.success_count > 0


@dataclass(frozen=True)
class ProbeReport:
    host: str
    ports: tuple[int, ...]
    timeout_s: float
    attempts: int
    backend: str
    command_probe_enabled: bool
    generated_utc: str
    results: tuple[PortProbeResult, ...]
    candidate_ports: tuple[int, ...]
    recommended_port: int | None


def parse_ports(value: str | Sequence[object]) -> tuple[int, ...]:
    parsed: list[int] = []

    if isinstance(value, str):
        tokens = [token.strip() for token in value.split(",") if token.strip()]
    else:
        tokens = [str(item).strip() for item in value if str(item).strip()]

    if not tokens:
        raise ValueError("At least one TCP port must be provided.")

    for token in tokens:
        parsed.extend(_parse_port_token(token))

    if not parsed:
        raise ValueError("At least one TCP port must be provided.")

    return tuple(sorted(set(parsed)))


def probe_host_ports(
    *,
    host: str,
    ports: str | Sequence[object],
    timeout_s: float,
    attempts: int = 1,
    backend: str = "adapter",
    command_probe: bool = False,
) -> ProbeReport:
    normalized_host = host.strip()
    if not normalized_host:
        raise ValueError("Host cannot be empty.")
    if timeout_s <= 0:
        raise ValueError("Timeout must be positive.")
    if attempts < 1:
        raise ValueError("Attempts must be at least 1.")

    normalized_ports = parse_ports(ports)
    results = tuple(
        _probe_single_port(
            host=normalized_host,
            port=port,
            timeout_s=timeout_s,
            attempts=attempts,
            backend=backend,
            command_probe=command_probe,
        )
        for port in normalized_ports
    )

    candidate_ports = select_candidate_ports(results)
    recommended_port = select_recommended_port(results, candidate_ports)

    generated_utc = (
        dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )

    return ProbeReport(
        host=normalized_host,
        ports=normalized_ports,
        timeout_s=timeout_s,
        attempts=attempts,
        backend=backend,
        command_probe_enabled=command_probe,
        generated_utc=generated_utc,
        results=results,
        candidate_ports=candidate_ports,
        recommended_port=recommended_port,
    )


def select_candidate_ports(results: Sequence[PortProbeResult]) -> tuple[int, ...]:
    command_validated = sorted(
        result.port
        for result in results
        if result.command_probe is not None and result.command_probe.success
    )
    if command_validated:
        return tuple(command_validated)

    tcp_open = sorted(result.port for result in results if result.open)
    return tuple(tcp_open)


def select_recommended_port(
    results: Sequence[PortProbeResult], candidate_ports: Sequence[int]
) -> int | None:
    if not candidate_ports:
        return None

    result_map = {result.port: result for result in results}
    ranked = sorted(
        candidate_ports,
        key=lambda port: _recommendation_key(result_map[port]),
    )
    return ranked[0]


def run_command_probe(
    *, host: str, port: int, timeout_s: float, backend: str
) -> CommandProbeResult:
    backend_key = backend.strip().lower()

    if backend_key in {"", "adapter", "none", "tcp"}:
        return CommandProbeResult(
            attempted=False,
            success=False,
            backend=backend,
            command=None,
            duration_ms=None,
            detail="No backend command probe configured.",
        )

    if backend_key == "nanonis_spm":
        return _run_nanonis_spm_probe(host=host, port=port, timeout_s=timeout_s)

    return CommandProbeResult(
        attempted=False,
        success=False,
        backend=backend,
        command=None,
        duration_ms=None,
        detail=f"Unsupported backend for command probe: {backend}",
    )


def report_to_dict(report: ProbeReport) -> dict[str, Any]:
    return {
        "host": report.host,
        "ports": list(report.ports),
        "timeout_s": report.timeout_s,
        "attempts": report.attempts,
        "backend": report.backend,
        "command_probe_enabled": report.command_probe_enabled,
        "generated_utc": report.generated_utc,
        "results": [_port_result_to_dict(item) for item in report.results],
        "candidate_ports": list(report.candidate_ports),
        "recommended_port": report.recommended_port,
    }


def format_report_text(report: ProbeReport) -> str:
    lines: list[str] = []
    lines.append(f"Probe target      : {report.host}")
    lines.append(f"Ports tested      : {', '.join(str(item) for item in report.ports)}")
    lines.append(f"Timeout (s)       : {report.timeout_s}")
    lines.append(f"Attempts per port : {report.attempts}")
    lines.append(f"Backend           : {report.backend}")
    lines.append(f"Command probe     : {'enabled' if report.command_probe_enabled else 'disabled'}")
    lines.append(f"Generated (UTC)   : {report.generated_utc}")
    lines.append("-")
    lines.append("PORT  STATE   OK/TRY  LAT(ms)  CMD   DETAIL")

    for result in report.results:
        state = "OPEN" if result.open else "CLOSED"
        ratio = f"{result.success_count}/{result.attempts}"
        latency = f"{result.median_latency_ms:.2f}" if result.median_latency_ms is not None else "-"
        command_state, detail = _command_cell(
            result, command_probe_enabled=report.command_probe_enabled
        )
        lines.append(
            f"{result.port:<5} {state:<7} {ratio:<7} {latency:>7}  {command_state:<5} {detail}"
        )

    lines.append("-")
    lines.append(
        f"Candidate ports   : {', '.join(str(item) for item in report.candidate_ports) if report.candidate_ports else 'none'}"
    )
    lines.append(
        f"Recommended port  : {report.recommended_port if report.recommended_port is not None else 'none'}"
    )
    return "\n".join(lines)


def _probe_single_port(
    *,
    host: str,
    port: int,
    timeout_s: float,
    attempts: int,
    backend: str,
    command_probe: bool,
) -> PortProbeResult:
    latencies_ms: list[float] = []
    last_error: str | None = None

    for _ in range(attempts):
        start = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=timeout_s):
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                latencies_ms.append(elapsed_ms)
                last_error = None
        except OSError as exc:
            last_error = f"{type(exc).__name__}: {exc}"

    command_result: CommandProbeResult | None = None
    if command_probe and latencies_ms:
        command_result = run_command_probe(
            host=host, port=port, timeout_s=timeout_s, backend=backend
        )

    if latencies_ms:
        median_latency_ms = statistics.median(latencies_ms)
        min_latency_ms = min(latencies_ms)
        max_latency_ms = max(latencies_ms)
    else:
        median_latency_ms = None
        min_latency_ms = None
        max_latency_ms = None

    return PortProbeResult(
        host=host,
        port=port,
        attempts=attempts,
        success_count=len(latencies_ms),
        median_latency_ms=median_latency_ms,
        min_latency_ms=min_latency_ms,
        max_latency_ms=max_latency_ms,
        last_error=last_error,
        command_probe=command_result,
    )


def _run_nanonis_spm_probe(*, host: str, port: int, timeout_s: float) -> CommandProbeResult:
    start = time.perf_counter()
    try:
        module = importlib.import_module("nanonis_spm")
    except ModuleNotFoundError:
        return CommandProbeResult(
            attempted=True,
            success=False,
            backend="nanonis_spm",
            command=None,
            duration_ms=None,
            detail="Python package 'nanonis-spm' is not installed.",
        )

    nanonis_cls_obj = getattr(module, "Nanonis", None)
    if nanonis_cls_obj is None:
        return CommandProbeResult(
            attempted=True,
            success=False,
            backend="nanonis_spm",
            command=None,
            duration_ms=None,
            detail="'nanonis_spm.Nanonis' class not found.",
        )

    nanonis_ctor = cast(Callable[[socket.socket], Any], nanonis_cls_obj)

    attempt_errors: list[str] = []

    for probe_attempt in range(2):
        try:
            with socket.create_connection((host, port), timeout=timeout_s) as connection:
                instance = nanonis_ctor(connection)
                command_errors: list[str] = []
                for method_name, method in _resolve_read_probe_methods(instance):
                    try:
                        value = method()
                    except Exception as exc:
                        command_errors.append(f"{method_name}(): {type(exc).__name__}: {exc}")
                        if isinstance(
                            exc, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)
                        ):
                            break
                        continue

                    success, detail = _interpret_probe_response(value)
                    if success:
                        elapsed_ms = (time.perf_counter() - start) * 1000.0
                        return CommandProbeResult(
                            attempted=True,
                            success=True,
                            backend="nanonis_spm",
                            command=f"{method_name}()",
                            duration_ms=elapsed_ms,
                            detail=detail,
                        )

                    command_errors.append(f"{method_name}(): {detail}")

                if command_errors:
                    attempt_errors.append("; ".join(command_errors[:3]))
                else:
                    attempt_errors.append(
                        "No callable no-argument read probe methods were found on nanonis_spm.Nanonis."
                    )
        except Exception as exc:  # pragma: no cover - depends on live backend behavior
            attempt_errors.append(f"{type(exc).__name__}: {exc}")

        if probe_attempt == 0:
            time.sleep(0.05)

    return CommandProbeResult(
        attempted=True,
        success=False,
        backend="nanonis_spm",
        command=None,
        duration_ms=None,
        detail=_trimmed_repr(" | ".join(attempt_errors[:2])),
    )


def _resolve_read_probe_methods(instance: Any) -> tuple[tuple[str, Callable[[], Any]], ...]:
    preferred_names = (
        "Bias_Get",
        "Current_Get",
        "ZCtrl_ZPosGet",
        "ZCtrl_OnOffGet",
        "Scan_StatusGet",
        "Util_AcqPeriodGet",
        "Util_RTFreqGet",
        "Util_VersionGet",
    )

    resolved: list[tuple[str, Callable[[], Any]]] = []

    for name in preferred_names:
        candidate = getattr(instance, name, None)
        if callable(candidate) and _callable_accepts_no_required_args(candidate):
            resolved.append((name, cast(Callable[[], Any], candidate)))

    return tuple(resolved)


def _interpret_probe_response(value: object) -> tuple[bool, str]:
    if isinstance(value, tuple) and len(value) >= 1 and isinstance(value[0], str):
        controller_error = value[0].strip()
        if controller_error:
            return False, f"Controller error: {controller_error}"
        return True, _summarize_probe_value(value)

    return True, _trimmed_repr(value)


def _summarize_probe_value(value: tuple[Any, ...]) -> str:
    if len(value) >= 3:
        payload = value[2]
        if isinstance(payload, list) and payload:
            return _trimmed_repr(payload[0])
    return _trimmed_repr(value)


def _callable_accepts_no_required_args(func: Callable[..., Any]) -> bool:
    try:
        signature = inspect.signature(func)
    except (ValueError, TypeError):
        return True

    for parameter in signature.parameters.values():
        if parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
            continue
        if parameter.default is inspect.Parameter.empty:
            return False
    return True


def _parse_port_token(token: str) -> list[int]:
    if "-" not in token:
        return [_validate_port(int(token))]

    left, right = token.split("-", maxsplit=1)
    start = _validate_port(int(left.strip()))
    end = _validate_port(int(right.strip()))
    if start > end:
        raise ValueError(f"Invalid TCP port range: {token}")

    return list(range(start, end + 1))


def _validate_port(port: int) -> int:
    if port < 1 or port > 65535:
        raise ValueError(f"Invalid TCP port: {port}")
    return port


def _recommendation_key(result: PortProbeResult) -> tuple[int, int, float, int]:
    command_rank = 0 if result.command_probe is not None and result.command_probe.success else 1
    failures = result.attempts - result.success_count
    latency = result.median_latency_ms if result.median_latency_ms is not None else float("inf")
    return (command_rank, failures, latency, result.port)


def _command_cell(result: PortProbeResult, *, command_probe_enabled: bool) -> tuple[str, str]:
    if not command_probe_enabled:
        return "off", _detail_or_fallback(result.last_error, fallback="tcp-connect-only")

    if result.command_probe is None:
        return "skip", _detail_or_fallback(result.last_error, fallback="port not open")

    command_probe = result.command_probe
    if not command_probe.attempted:
        return "skip", command_probe.detail
    if command_probe.success:
        detail = command_probe.command or command_probe.detail
        return "ok", detail

    return "fail", command_probe.detail


def _detail_or_fallback(value: str | None, *, fallback: str) -> str:
    if value:
        return value
    return fallback


def _trimmed_repr(value: object) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = repr(value)
    if len(text) <= 180:
        return text
    return f"{text[:177]}..."


def _port_result_to_dict(result: PortProbeResult) -> dict[str, Any]:
    return {
        "host": result.host,
        "port": result.port,
        "open": result.open,
        "attempts": result.attempts,
        "success_count": result.success_count,
        "median_latency_ms": result.median_latency_ms,
        "min_latency_ms": result.min_latency_ms,
        "max_latency_ms": result.max_latency_ms,
        "last_error": result.last_error,
        "command_probe": (
            None
            if result.command_probe is None
            else {
                "attempted": result.command_probe.attempted,
                "success": result.command_probe.success,
                "backend": result.command_probe.backend,
                "command": result.command_probe.command,
                "duration_ms": result.command_probe.duration_ms,
                "detail": result.command_probe.detail,
            }
        ),
    }
