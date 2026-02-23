from __future__ import annotations

import inspect

from nanonis_qcodes_controller.qcodes_driver.manifest_generator import (
    CommandInfo,
    InferredSetMapping,
    build_unified_manifest,
    extract_description,
    infer_set_mapping,
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

    get_info = CommandInfo(
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
    )
    set_info = CommandInfo(
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
    )

    manifest = build_unified_manifest(
        curated_defaults={},
        curated_parameters={},
        commands=(get_info, set_info),
    )
    parameter = manifest["parameters"]["bias"]

    assert "description" not in parameter
    assert parameter["get_cmd"]["description"] == "Returns the Bias voltage value."
    assert parameter["set_cmd"]["description"] == "Sets the Bias voltage to the specified value."
