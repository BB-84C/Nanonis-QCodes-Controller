from __future__ import annotations


def test_stable_v1_symbols_import_from_submodules() -> None:
    from nspmctl.client import create_client
    from nspmctl.config import load_settings
    from nspmctl.controller import NanonisController

    assert callable(create_client)
    assert callable(load_settings)
    assert NanonisController.__name__ == "NanonisController"
