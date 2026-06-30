from __future__ import annotations

import importlib
import importlib.metadata
import inspect
import socket
import struct
from collections.abc import Callable, Mapping
from typing import Any

from .errors import (
    NanonisCommandUnavailableError,
    NanonisConnectionError,
    NanonisInvalidArgumentError,
    NanonisProtocolError,
    NanonisTimeoutError,
)


class NanonisSpmAdapter:
    name = "nanonis_spm"
    probe_command = "Bias_Get"

    def open_session(self, *, host: str, port: int, timeout_s: float) -> NanonisSpmSession:
        try:
            module = importlib.import_module("nanonis_spm")
        except ModuleNotFoundError as exc:
            raise NanonisCommandUnavailableError(
                "Backend package 'nanonis-spm' is not installed. Install with: pip install nanonis-spm"
            ) from exc

        nanonis_ctor_obj = getattr(module, "Nanonis", None)
        if nanonis_ctor_obj is None:
            raise NanonisProtocolError("Backend module 'nanonis_spm' does not expose 'Nanonis'.")

        if not callable(nanonis_ctor_obj):
            raise NanonisProtocolError("'nanonis_spm.Nanonis' is not callable.")

        try:
            connection = socket.create_connection((host, port), timeout=timeout_s)
            connection.settimeout(timeout_s)
        except TimeoutError as exc:
            raise NanonisTimeoutError(
                f"Timed out opening socket to {host}:{port} (timeout={timeout_s}s)."
            ) from exc
        except OSError as exc:
            raise NanonisConnectionError(f"Failed to open socket to {host}:{port}: {exc}") from exc

        try:
            instance = nanonis_ctor_obj(connection)
        except Exception as exc:
            connection.close()
            raise NanonisProtocolError(f"Failed to initialize nanonis_spm backend: {exc}") from exc

        endpoint = f"{host}:{port}"
        return NanonisSpmSession(endpoint=endpoint, connection=connection, instance=instance)

    def version_string(self) -> str:
        try:
            version = importlib.metadata.version("nanonis-spm")
        except importlib.metadata.PackageNotFoundError:
            version = "unknown"
        return f"nanonis-spm/{version}"


class NanonisSpmSession:
    def __init__(self, *, endpoint: str, connection: socket.socket, instance: Any) -> None:
        self.endpoint = endpoint
        self._connection = connection
        self._instance = instance
        self._method_index = self._build_method_index(instance)

    def close(self) -> None:
        self._connection.close()

    def available_commands(self) -> tuple[str, ...]:
        return tuple(sorted(self._method_index.values()))

    def call(self, command: str, *, args: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        command_text = command.strip()
        if not command_text:
            raise NanonisInvalidArgumentError("Command cannot be empty.")

        method_name, method = self._resolve_method(command_text)
        normalized_args = self._normalize_args(method=method, args=args)

        try:
            response = method(**normalized_args)
        except Exception as exc:
            raise self._map_backend_exception(
                exc, command=command_text, method_name=method_name
            ) from exc

        return self._parse_response(
            command=command_text, method_name=method_name, response=response
        )

    @staticmethod
    def _build_method_index(instance: Any) -> dict[str, str]:
        index: dict[str, str] = {}
        for name in sorted(dir(instance)):
            candidate = getattr(instance, name, None)
            if not callable(candidate):
                continue
            normalized = _normalize_key(name)
            if normalized not in index:
                index[normalized] = name
        return index

    def _resolve_method(self, command: str) -> tuple[str, Callable[..., Any]]:
        direct = getattr(self._instance, command, None)
        if callable(direct):
            return command, direct

        normalized = _normalize_key(command)
        method_name = self._method_index.get(normalized)
        if method_name is None:
            raise NanonisCommandUnavailableError(
                f"Command '{command}' is not available in backend."
            )

        method = getattr(self._instance, method_name)
        return method_name, method

    def _normalize_args(
        self,
        *,
        method: Callable[..., Any],
        args: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if args is None:
            args = {}
        if not isinstance(args, Mapping):
            raise NanonisInvalidArgumentError("Arguments must be provided as a mapping.")

        method_signature = inspect.signature(method)
        parameters = [
            parameter
            for parameter in method_signature.parameters.values()
            if parameter.kind
            in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
        ]

        if not parameters:
            if args:
                unknown = ", ".join(sorted(args))
                raise NanonisInvalidArgumentError(
                    f"Command does not take arguments. Unexpected: {unknown}"
                )
            return {}

        by_exact_name = {parameter.name: parameter for parameter in parameters}
        by_normalized_name = {_normalize_key(parameter.name): parameter for parameter in parameters}

        normalized_kwargs: dict[str, Any] = {}
        for key, value in args.items():
            parameter = by_exact_name.get(key)
            if parameter is None:
                parameter = by_normalized_name.get(_normalize_key(key))
            if parameter is None:
                allowed = ", ".join(parameter_name for parameter_name in by_exact_name)
                raise NanonisInvalidArgumentError(
                    f"Unexpected argument '{key}'. Allowed arguments: {allowed}"
                )
            normalized_kwargs[parameter.name] = value

        missing = [
            parameter.name
            for parameter in parameters
            if parameter.default is inspect.Parameter.empty
            and parameter.name not in normalized_kwargs
        ]
        if missing:
            formatted = ", ".join(missing)
            raise NanonisInvalidArgumentError(f"Missing required arguments: {formatted}")

        return normalized_kwargs

    def _parse_response(
        self,
        *,
        command: str,
        method_name: str,
        response: Any,
    ) -> Mapping[str, Any]:
        controller_error = ""
        payload: list[Any] = []

        if isinstance(response, tuple):
            if len(response) >= 1 and isinstance(response[0], str):
                controller_error = response[0].strip()
            if len(response) >= 3 and isinstance(response[2], list):
                payload = _normalize_value(response[2])
        else:
            payload = [_normalize_value(response)]

        if controller_error:
            raise NanonisProtocolError(
                f"Controller returned error for command '{command}' ({method_name}): {controller_error}"
            )

        result: dict[str, Any] = {
            "command": command,
            "method": method_name,
            "payload": payload,
        }
        if len(payload) == 1:
            result["value"] = payload[0]
        return result

    @staticmethod
    def _map_backend_exception(exc: Exception, *, command: str, method_name: str) -> Exception:
        if isinstance(exc, NanonisCommandUnavailableError):
            return exc
        if isinstance(exc, NanonisInvalidArgumentError):
            return exc
        if isinstance(exc, (socket.timeout, TimeoutError)):
            return NanonisTimeoutError(
                f"Timeout while running command '{command}' ({method_name}): {exc}"
            )
        if isinstance(exc, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
            return NanonisConnectionError(
                f"Connection dropped while running command '{command}' ({method_name}): {exc}"
            )
        if isinstance(exc, OSError):
            return NanonisConnectionError(
                f"Connection error while running command '{command}' ({method_name}): {exc}"
            )
        if isinstance(exc, struct.error):
            return NanonisProtocolError(
                f"Protocol decode error while running command '{command}' ({method_name}): {exc}"
            )
        if isinstance(exc, TypeError):
            return NanonisInvalidArgumentError(
                f"Invalid arguments for command '{command}' ({method_name}): {exc}"
            )

        return NanonisProtocolError(
            f"Unexpected backend error while running command '{command}' ({method_name}): {exc}"
        )


def _normalize_key(value: str) -> str:
    return "".join(char for char in value.lower() if char.isalnum())


def _normalize_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_normalize_value(item) for item in value)
    if isinstance(value, dict):
        return {str(key): _normalize_value(item) for key, item in value.items()}

    item_method = getattr(value, "item", None)
    if callable(item_method):
        try:
            return item_method()
        except Exception:
            return value

    return value
