from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class NanonisHealth:
    connected: bool
    endpoint: str | None
    latency_ms: float | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


class NanonisClient(Protocol):
    def connect(self) -> None: ...

    def close(self) -> None: ...

    def call(self, command: str, *, args: Mapping[str, Any] | None = None) -> Mapping[str, Any]: ...

    def version(self) -> str: ...

    def health(self) -> NanonisHealth: ...
