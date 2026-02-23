from __future__ import annotations

import atexit
import importlib.resources as resources
from contextlib import ExitStack
from functools import cache
from pathlib import Path

_RESOURCE_PATHS = ExitStack()
atexit.register(_RESOURCE_PATHS.close)


@cache
def _resolve_packaged_default_cached(name: str) -> Path:
    config_file = (
        resources.files("nanonis_qcodes_controller.resources").joinpath("config").joinpath(name)
    )
    resolved = _RESOURCE_PATHS.enter_context(resources.as_file(config_file))
    if not resolved.exists():
        raise ValueError(f"Packaged default file does not exist: {name}")
    return resolved


def resolve_packaged_default(name: str) -> Path:
    return _resolve_packaged_default_cached(name)
