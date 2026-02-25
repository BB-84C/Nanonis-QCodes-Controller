from __future__ import annotations

from pathlib import Path

from nanonis_qcodes_controller.qcodes_driver.extensions import (
    load_action_specs,
    load_parameter_specs,
)


def test_load_parameter_specs_parses_methods_only_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "parameters.yaml"
    manifest_path.write_text(
        "\n".join(
            [
                "defaults:",
                "  ramp_default_interval_s: 0.1",
                "parameters:",
                "  bias_v:",
                "    label: Bias",
                "    get_cmd:",
                "      command: Bias_Get",
                "      response_fields:",
                "        - name: Bias",
                "          type: float",
                "          unit: V",
                "          wire_type: f",
                "          index: 0",
                "    set_cmd:",
                "      command: Bias_Set",
                "      arg_fields:",
                "        - name: Bias",
                "          type: float",
                "          unit: V",
                "          wire_type: f",
                "          required: true",
                "    safety:",
                "      min: -5.0",
                "      max: 5.0",
                "      max_step: 0.5",
            ]
        ),
        encoding="utf-8",
    )

    specs = load_parameter_specs(manifest_path)

    assert len(specs) == 1
    assert specs[0].name == "bias_v"
    assert specs[0].set_cmd is not None
    assert specs[0].set_cmd.arg_fields[0].name == "Bias"
    assert specs[0].safety is not None


def test_load_parameter_specs_ignores_legacy_scalar_keys(tmp_path: Path) -> None:
    manifest_path = tmp_path / "legacy_keys.yaml"
    manifest_path.write_text(
        "\n".join(
            [
                "defaults:",
                "  ramp_default_interval_s: 0.1",
                "parameters:",
                "  scan_speed:",
                "    label: Scan Speed",
                "    value_type: float",
                "    unit: m/s",
                "    vals:",
                "      kind: numbers",
                "      min: 0",
                "      max: 1",
                "    snapshot_value: false",
                "    get_cmd:",
                "      command: Scan_SpeedGet",
                "      response_fields:",
                "        - name: Speed",
                "          type: float",
                "          index: 0",
                "    set_cmd:",
                "      command: Scan_SpeedSet",
                "      arg_fields:",
                "        - name: Speed",
                "          type: float",
                "          required: true",
                "    safety:",
                "      min: 0",
                "      max: 1",
                "      max_step: 0.2",
            ]
        ),
        encoding="utf-8",
    )

    specs = load_parameter_specs(manifest_path)

    assert len(specs) == 1
    assert specs[0].name == "scan_speed"
    assert specs[0].label == "Scan Speed"
    assert specs[0].get_cmd is not None
    assert specs[0].set_cmd is not None


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
