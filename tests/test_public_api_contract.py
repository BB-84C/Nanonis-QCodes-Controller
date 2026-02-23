from __future__ import annotations


def test_stable_v1_symbols_import_from_submodules() -> None:
    from nanonis_qcodes_controller.client import create_client
    from nanonis_qcodes_controller.config import load_settings
    from nanonis_qcodes_controller.qcodes_driver import QcodesNanonisSTM

    assert callable(create_client)
    assert callable(load_settings)
    assert QcodesNanonisSTM.__name__ == "QcodesNanonisSTM"
