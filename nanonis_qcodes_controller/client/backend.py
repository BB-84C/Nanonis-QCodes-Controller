from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from .errors import NanonisCommandUnavailableError


class BackendSession(Protocol):
    endpoint: str

    def call(self, command: str, *, args: Mapping[str, Any] | None = None) -> Mapping[str, Any]: ...

    def close(self) -> None: ...


class BackendAdapter(Protocol):
    name: str
    probe_command: str

    def open_session(self, *, host: str, port: int, timeout_s: float) -> BackendSession: ...

    def version_string(self) -> str: ...


BackendFactory = Callable[[], BackendAdapter]
_BACKEND_FACTORIES: dict[str, BackendFactory] = {}
_DEFAULT_BACKENDS_REGISTERED = False


def register_backend(name: str, factory: BackendFactory, *, aliases: Sequence[str] = ()) -> None:
    normalized_name = _normalize_key(name)
    _BACKEND_FACTORIES[normalized_name] = factory
    for alias in aliases:
        _BACKEND_FACTORIES[_normalize_key(alias)] = factory


def build_backend_adapter(name: str) -> BackendAdapter:
    _ensure_default_backends_registered()
    factory = _BACKEND_FACTORIES.get(_normalize_key(name))
    if factory is None:
        available = ", ".join(sorted(_BACKEND_FACTORIES))
        raise NanonisCommandUnavailableError(
            f"Unknown backend '{name}'. Available backends: {available}"
        )
    return factory()


def _ensure_default_backends_registered() -> None:
    global _DEFAULT_BACKENDS_REGISTERED
    if _DEFAULT_BACKENDS_REGISTERED:
        return

    from .nanonis_spm_backend import NanonisSpmAdapter

    register_backend(
        "nanonis_spm",
        NanonisSpmAdapter,
        aliases=("nanonis", "adapter"),
    )
    _DEFAULT_BACKENDS_REGISTERED = True


def _normalize_key(value: str) -> str:
    return "".join(char for char in value.strip().lower() if char.isalnum() or char == "_")
