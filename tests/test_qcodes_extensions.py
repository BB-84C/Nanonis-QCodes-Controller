from __future__ import annotations

from pathlib import Path

import pytest

from nanonis_qcodes_controller.qcodes_driver.extensions import (
    load_action_specs,
    load_scalar_parameter_specs,
)


def test_load_scalar_parameter_specs_parses_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "extra_params.yaml"
    manifest_path.write_text(
        "\n".join(
            [
                "parameters:",
                "  - name: lockin_x",
                "    command: LockIn_DemodSignalGet",
                "    value_type: float",
                "    payload_index: 0",
                "    args:",
                "      Demodulator_number: 1",
                "    snapshot_value: true",
                "  - name: lockin_enabled",
                "    command: LockIn_ModOnOffGet",
                "    type: bool",
            ]
        ),
        encoding="utf-8",
    )

    specs = load_scalar_parameter_specs(manifest_path)

    assert len(specs) == 2
    assert specs[0].name == "lockin_x"
    assert specs[0].command == "LockIn_DemodSignalGet"
    assert specs[0].args["Demodulator_number"] == 1
    assert specs[1].value_type == "bool"


def test_load_scalar_parameter_specs_rejects_unknown_value_type(tmp_path: Path) -> None:
    manifest_path = tmp_path / "bad.yaml"
    manifest_path.write_text(
        "\n".join(
            [
                "parameters:",
                "  - name: invalid_param",
                "    command: Bias_Get",
                "    type: decimal",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        _ = load_scalar_parameter_specs(manifest_path)


def test_load_action_specs_parses_manifest_actions(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        "\n".join(
            [
                "actions:",
                "  Scan_Action:",
                "    action_cmd:",
                "      command: Scan_Action",
                "      description: Start or stop a scan.",
                "      arg_fields:",
                "        - name: Scan_action",
                "          type: int",
                "          unit: ''",
                "          wire_type: i",
                "          required: true",
                "          default: null",
                "          description: Scan action (int)",
                "    safety:",
                "      mode: alwaysAllowed",
            ]
        ),
        encoding="utf-8",
    )

    specs = load_action_specs(manifest_path)

    assert len(specs) == 1
    assert specs[0].name == "Scan_Action"
    assert specs[0].action_cmd.command == "Scan_Action"
    assert specs[0].action_cmd.arg_fields[0].name == "Scan_action"
    assert specs[0].action_cmd.arg_fields[0].wire_type == "i"
    assert specs[0].safety_mode == "alwaysAllowed"
