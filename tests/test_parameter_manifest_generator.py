from __future__ import annotations

import inspect

import pytest

from nanonis_qcodes_controller.qcodes_driver.manifest_generator import (
    CommandInfo,
    InferredSetMapping,
    build_unified_manifest,
    discover_nanonis_commands,
    extract_description,
    infer_set_mapping,
)


def _command_info(
    *,
    command: str,
    signature: inspect.Signature,
    arguments: tuple[str, ...],
    doc: str,
    body_arg_names: tuple[str, ...] = (),
    body_wire_types: tuple[str, ...] = (),
    response_wire_types: tuple[str, ...] = (),
) -> CommandInfo:
    return CommandInfo(
        command=command,
        arguments=arguments,
        signature=signature,
        doc=doc,
        body_arg_names=body_arg_names,
        body_wire_types=body_wire_types,
        response_wire_types=response_wire_types,
    )


def test_extract_description_drops_arguments_and_return_sections() -> None:
    doc = """LockIn.ModAmpGet
Returns the modulation amplitude of the specified Lock-In modulator.
Arguments:
-- Modulator number (int)
Return arguments:
-- Amplitude (float32)
"""

    assert extract_description(doc) == (
        "Returns the modulation amplitude of the specified Lock-In modulator."
    )


def test_infer_set_mapping_prefers_value_argument_and_sets_selector_default() -> None:
    def _setter(self, Modulator_number: int, Amplitude_: float) -> None:  # noqa: N802
        del self, Modulator_number, Amplitude_

    signature = inspect.signature(_setter)
    mapping = infer_set_mapping(
        signature=signature,
        args=("Modulator_number", "Amplitude_"),
        arg_docs={
            "modulatornumber": "Modulator number (int)",
            "amplitude": "Amplitude (float32)",
        },
    )

    assert isinstance(mapping, InferredSetMapping)
    assert mapping.value_arg == "Amplitude_"
    assert mapping.fixed_args == {"Modulator_number": 1}


def test_generated_manifest_emits_get_and_set_command_descriptions() -> None:
    def _bias_get(self) -> None:
        del self

    def _bias_set(self, Bias_value_V: float) -> None:  # noqa: N802
        del self, Bias_value_V

    get_info = _command_info(
        command="Bias_Get",
        arguments=(),
        signature=inspect.signature(_bias_get),
        doc=(
            "Bias.Get\n"
            "Returns the Bias voltage value.\n"
            "Arguments: None\n"
            "Return arguments:\n"
            "-- Bias value (V) (float32)"
        ),
        response_wire_types=("f",),
    )
    set_info = _command_info(
        command="Bias_Set",
        arguments=("Bias_value_V",),
        signature=inspect.signature(_bias_set),
        doc=(
            "Bias.Set\n"
            "Sets the Bias voltage to the specified value.\n"
            "Arguments:\n"
            "-- Bias value (V) (float32)\n"
            "Return arguments:\n"
            "-- Error described in response"
        ),
        body_arg_names=("Bias_value_V",),
        body_wire_types=("f",),
    )

    manifest = build_unified_manifest(
        curated_defaults={},
        curated_parameters={},
        curated_actions={},
        commands=(get_info, set_info),
    )
    parameter = manifest["parameters"]["bias"]

    assert "description" not in parameter
    assert parameter["get_cmd"]["description"] == "Returns the Bias voltage value."
    assert parameter["set_cmd"]["description"] == "Sets the Bias voltage to the specified value."
    assert "docstring_full" not in parameter["get_cmd"]
    assert "docstring_full" not in parameter["set_cmd"]
    assert parameter["get_cmd"]["response_fields"][0]["index"] == 0
    assert parameter["get_cmd"]["response_fields"][0]["type"] == "float"
    assert parameter["get_cmd"]["response_fields"][0]["wire_type"] == "f"
    assert parameter["set_cmd"]["arg_fields"][0]["name"] == "Bias_value_V"
    assert parameter["set_cmd"]["arg_fields"][0]["required"] is True


def test_generated_manifest_emits_non_get_set_commands_as_actions() -> None:
    def _scan_action(self, Scan_action: int, Scan_direction: int) -> None:  # noqa: N802
        del self, Scan_action, Scan_direction

    action_info = _command_info(
        command="Scan_Action",
        arguments=("Scan_action", "Scan_direction"),
        signature=inspect.signature(_scan_action),
        doc=(
            "Scan.Action\n"
            "Controls scanner action and direction.\n"
            "Arguments:\n"
            "-- Scan action (int)\n"
            "-- Scan direction (int)\n"
            "Return arguments:\n"
            "-- Error described in response"
        ),
        body_arg_names=("Scan_action", "Scan_direction"),
        body_wire_types=("i", "i"),
    )

    manifest = build_unified_manifest(
        curated_defaults={},
        curated_parameters={},
        curated_actions={},
        commands=(action_info,),
    )

    action = manifest["actions"]["Scan_Action"]
    assert action["action_cmd"]["command"] == "Scan_Action"
    assert "arg_types" not in action["action_cmd"]
    assert "args" not in action["action_cmd"]
    assert "docstring_full" not in action["action_cmd"]
    assert action["action_cmd"]["arg_fields"][0]["name"] == "Scan_action"
    assert action["action_cmd"]["arg_fields"][0]["type"] == "int"
    assert action["action_cmd"]["arg_fields"][0]["wire_type"] == "i"
    assert action["safety"]["mode"] == "guarded"


def test_generated_manifest_drops_curated_actions_not_discovered() -> None:
    def _scan_action(self, Scan_action: int, Scan_direction: int) -> None:  # noqa: N802
        del self, Scan_action, Scan_direction

    action_info = _command_info(
        command="Scan_Action",
        arguments=("Scan_action", "Scan_direction"),
        signature=inspect.signature(_scan_action),
        doc=(
            "Scan.Action\n"
            "Controls scanner action and direction.\n"
            "Arguments:\n"
            "-- Scan action (int)\n"
            "-- Scan direction (int)\n"
            "Return arguments:\n"
            "-- Error described in response"
        ),
        body_arg_names=("Scan_action", "Scan_direction"),
        body_wire_types=("i", "i"),
    )

    manifest = build_unified_manifest(
        curated_defaults={},
        curated_parameters={},
        curated_actions={
            "quickSend": {
                "action_cmd": {
                    "command": "quickSend",
                },
                "safety": {"mode": "guarded"},
            }
        },
        commands=(action_info,),
    )

    assert "Scan_Action" in manifest["actions"]
    assert "quickSend" not in manifest["actions"]


def test_discover_nanonis_commands_ignores_methods_before_bias_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeNanonis:
        def quickSend(self) -> None:  # noqa: N802
            return None

        def decodeArray(self) -> None:  # noqa: N802
            return None

        def Bias_Set(self, Bias_value_V: float) -> None:  # noqa: N802
            del Bias_value_V

        def Bias_Get(self) -> None:  # noqa: N802
            return None

        def Scan_Action(self, Scan_action: int) -> None:  # noqa: N802
            del Scan_action

    class FakeModule:
        Nanonis = FakeNanonis

    monkeypatch.setattr(
        "nanonis_qcodes_controller.qcodes_driver.manifest_generator.importlib.import_module",
        lambda _name: FakeModule,
    )

    names = [item.command for item in discover_nanonis_commands()]
    assert names == ["Bias_Get", "Bias_Set", "Scan_Action"]


def test_discover_nanonis_commands_requires_bias_set_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeNanonis:
        def quickSend(self) -> None:  # noqa: N802
            return None

        def Scan_Action(self, Scan_action: int) -> None:  # noqa: N802
            del Scan_action

    class FakeModule:
        Nanonis = FakeNanonis

    monkeypatch.setattr(
        "nanonis_qcodes_controller.qcodes_driver.manifest_generator.importlib.import_module",
        lambda _name: FakeModule,
    )

    with pytest.raises(ValueError, match="Bias_Set"):
        _ = discover_nanonis_commands()
