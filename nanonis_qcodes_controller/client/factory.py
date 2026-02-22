from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from nanonis_qcodes_controller.config import load_settings

from .transport import NanonisTransportClient, build_client_from_settings


def create_client(
    *,
    config_file: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> NanonisTransportClient:
    settings = load_settings(config_file=config_file, env=env)
    return build_client_from_settings(settings.nanonis)
