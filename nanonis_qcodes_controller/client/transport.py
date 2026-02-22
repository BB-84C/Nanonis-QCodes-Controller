from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from threading import RLock
from typing import Any

from nanonis_qcodes_controller.config import NanonisConnectionSettings

from .backend import BackendAdapter, BackendSession, build_backend_adapter
from .base import NanonisHealth
from .errors import NanonisClientError, NanonisConnectionError, NanonisTimeoutError


class NanonisTransportClient:
    def __init__(
        self,
        *,
        host: str,
        ports: Sequence[int],
        timeout_s: float,
        retry_count: int,
        backend: str,
        adapter: BackendAdapter | None = None,
    ) -> None:
        normalized_host = host.strip()
        if not normalized_host:
            raise ValueError("Host cannot be empty.")
        if timeout_s <= 0:
            raise ValueError("Timeout must be positive.")
        if retry_count < 0:
            raise ValueError("Retry count must be non-negative.")
        if not ports:
            raise ValueError("At least one port is required.")

        normalized_ports = tuple(int(port) for port in ports)

        self._host = normalized_host
        self._ports = normalized_ports
        self._timeout_s = timeout_s
        self._retry_count = retry_count
        self._backend_name = backend
        self._adapter = adapter or build_backend_adapter(backend)

        self._lock = RLock()
        self._session: BackendSession | None = None
        self._active_port: int | None = None
        self._last_latency_ms: float | None = None
        self._last_error: str | None = None

    @property
    def endpoint(self) -> str | None:
        with self._lock:
            if self._active_port is None:
                return None
            return f"{self._host}:{self._active_port}"

    def connect(self) -> None:
        with self._lock:
            if self._session is not None:
                return

            attempts_per_port = self._retry_count + 1
            failures: list[str] = []
            for port in self._ports:
                for attempt in range(1, attempts_per_port + 1):
                    attempt_start = time.perf_counter()
                    session: BackendSession | None = None
                    try:
                        session = self._adapter.open_session(
                            host=self._host,
                            port=port,
                            timeout_s=self._timeout_s,
                        )
                        session.call(self._adapter.probe_command)

                        self._session = session
                        self._active_port = port
                        self._last_latency_ms = (time.perf_counter() - attempt_start) * 1000.0
                        self._last_error = None
                        return
                    except NanonisClientError as exc:
                        if session is not None:
                            try:
                                session.close()
                            except Exception:
                                pass
                        failures.append(f"{self._host}:{port} attempt {attempt}: {exc}")
                        self._last_error = str(exc)
                        if attempt < attempts_per_port:
                            time.sleep(0.05)

            summary = " | ".join(failures[-5:]) if failures else "no attempts were made"
            raise NanonisConnectionError(
                f"Failed to connect using backend '{self._adapter.name}'. Last failures: {summary}"
            )

    def close(self) -> None:
        with self._lock:
            if self._session is not None:
                try:
                    self._session.close()
                finally:
                    self._session = None
                    self._active_port = None

    def call(self, command: str, *, args: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        with self._lock:
            if self._session is None:
                self.connect()

            assert self._session is not None

            attempts = self._retry_count + 1
            for attempt in range(1, attempts + 1):
                call_start = time.perf_counter()
                try:
                    response = self._session.call(command, args=args)
                    self._last_latency_ms = (time.perf_counter() - call_start) * 1000.0
                    self._last_error = None
                    return response
                except (NanonisConnectionError, NanonisTimeoutError) as exc:
                    self._last_error = str(exc)
                    if attempt >= attempts:
                        raise
                    self._reconnect_locked()
                except NanonisClientError as exc:
                    self._last_error = str(exc)
                    raise

            raise NanonisConnectionError("Command retry loop exited unexpectedly.")

    def version(self) -> str:
        return self._adapter.version_string()

    def health(self) -> NanonisHealth:
        with self._lock:
            return NanonisHealth(
                connected=self._session is not None,
                endpoint=self.endpoint,
                latency_ms=self._last_latency_ms,
                details={
                    "backend": self._adapter.name,
                    "host": self._host,
                    "candidate_ports": self._ports,
                    "active_port": self._active_port,
                    "retry_count": self._retry_count,
                    "timeout_s": self._timeout_s,
                    "last_error": self._last_error,
                },
            )

    def _reconnect_locked(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            finally:
                self._session = None
                self._active_port = None
        self.connect()


def build_client_from_settings(settings: NanonisConnectionSettings) -> NanonisTransportClient:
    return NanonisTransportClient(
        host=settings.host,
        ports=settings.ports,
        timeout_s=settings.timeout_s,
        retry_count=settings.retry_count,
        backend=settings.backend,
    )
