from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest

from nanonis_qcodes_controller import cli
from nanonis_qcodes_controller.qcodes_driver.extensions import (
    DEFAULT_PARAMETERS_FILE,
    ActionCommandSpec,
    ActionSpec,
    ArgFieldSpec,
    ParameterSpec,
    ReadCommandSpec,
    ResponseFieldSpec,
    SafetySpec,
    ValidatorSpec,
    WriteCommandSpec,
)


def test_build_ramp_targets_increasing() -> None:
    targets = cli._build_ramp_targets(start=0.1, end=0.16, step=0.02)
    assert targets == pytest.approx((0.1, 0.12, 0.14, 0.16))


def test_build_ramp_targets_decreasing() -> None:
    targets = cli._build_ramp_targets(start=0.2, end=0.05, step=0.05)
    assert targets == pytest.approx((0.2, 0.15, 0.1, 0.05))


def test_build_ramp_targets_rejects_non_positive_step() -> None:
    with pytest.raises(ValueError, match="positive"):
        _ = cli._build_ramp_targets(start=0.0, end=1.0, step=0.0)


def test_normalize_help_args_supports_prefix_topic_style() -> None:
    assert cli._normalize_help_args(["-help", "extensions"]) == ["extensions", "--help"]
    assert cli._normalize_help_args(["-h"]) == ["--help"]


def test_json_output_is_default() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["actions", "list"])
    assert args.json is True


def test_text_output_opt_in_flag() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["actions", "list", "--text"])
    assert args.json is False


def test_act_parser_supports_repeatable_arg_flags() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(
        [
            "act",
            "Scan_Action",
            "--arg",
            "Scan_action=0",
            "--arg",
            "Scan_direction=1",
            "--plan-only",
        ]
    )
    assert args.action_name == "Scan_Action"
    assert args.arg == ["Scan_action=0", "Scan_direction=1"]
    assert args.plan_only is True


def test_cmd_act_invokes_instrument_execute_action(monkeypatch) -> None:
    class FakeInstrument:
        def execute_action(
            self,
            action_name: str,
            *,
            args: dict[str, str] | None,
            plan_only: bool,
        ) -> dict[str, object]:
            assert action_name == "Scan_Action"
            assert args == {"Scan_action": "0", "Scan_direction": "1"}
            assert args is not None
            assert plan_only is False
            return {
                "name": action_name,
                "command": "Scan_Action",
                "applied": True,
                "dry_run": False,
                "args": dict(args),
                "response": {"payload": []},
            }

    @contextmanager
    def fake_instrument_context(*_args, **_kwargs):
        yield FakeInstrument(), None

    monkeypatch.setattr(cli, "_instrument_context", fake_instrument_context)
    captured_payloads: list[dict[str, object]] = []

    def fake_print_payload(payload, *, as_json: bool) -> None:
        del as_json
        captured_payloads.append(dict(payload))

    monkeypatch.setattr(cli, "_print_payload", fake_print_payload)

    args = argparse.Namespace(
        action_name="Scan_Action",
        arg=["Scan_action=0", "Scan_direction=1"],
        plan_only=False,
        json=True,
    )
    exit_code = cli._cmd_act(args)

    assert exit_code == cli.EXIT_OK
    assert captured_payloads
    payload = captured_payloads[-1]
    assert payload["action"] == "Scan_Action"
    result = payload["result"]
    assert isinstance(result, dict)
    assert result["applied"] is True


def test_parse_action_args_rejects_invalid_entries() -> None:
    with pytest.raises(ValueError, match="key=value"):
        _ = cli._parse_action_args(raw_args=("Scan_action",))

    with pytest.raises(ValueError, match="Duplicate --arg key"):
        _ = cli._parse_action_args(raw_args=("Scan_action=0", "Scan_action=1"))


def test_now_utc_iso_is_valid_iso8601_utc() -> None:
    timestamp = cli._now_utc_iso()
    assert timestamp.endswith("Z")
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def test_capabilities_includes_parameter_specs_for_agents(monkeypatch) -> None:
    spec = ParameterSpec(
        name="bias_v",
        label="Bias",
        unit="V",
        value_type="float",
        get_cmd=ReadCommandSpec(
            command="Bias.Get",
            payload_index=0,
            args={"channel": 1},
            description="Read configured bias voltage.",
            docstring_full="Bias.Get\nReturns configured bias voltage.",
            response_fields=(
                ResponseFieldSpec(
                    index=0,
                    name="Bias value",
                    type="float",
                    unit="V",
                    description="Bias value (V) (float32)",
                ),
            ),
        ),
        set_cmd=WriteCommandSpec(
            command="Bias.Set",
            value_arg="bias",
            args={"channel": 1},
            description="Write configured bias voltage.",
            docstring_full="Bias.Set\nConfigures bias voltage.",
            arg_fields=(
                ArgFieldSpec(
                    name="bias",
                    type="float",
                    required=True,
                    description="Bias value (V) (float32)",
                ),
            ),
        ),
        vals=ValidatorSpec(kind="numbers", min_value=-10.0, max_value=10.0),
        safety=SafetySpec(min_value=-10.0, max_value=10.0, max_step=1.0, ramp_enabled=True),
        description="Tip-sample bias voltage.",
    )
    action_spec = ActionSpec(
        name="Scan_Action",
        action_cmd=ActionCommandSpec(
            command="Scan_Action",
            args={"Scan_action": 0, "Scan_direction": 0},
            arg_types={"Scan_action": "int", "Scan_direction": "int"},
            description="Start or stop scanner movement.",
            docstring_full="Scan.Action\nControls scan actions.",
            arg_fields=(
                ArgFieldSpec(
                    name="Scan_action",
                    type="int",
                    required=True,
                    description="Scan action (int)",
                ),
            ),
        ),
        safety_mode="guarded",
    )

    class FakeInstrument:
        def parameter_specs(self) -> tuple[ParameterSpec, ...]:
            return (spec,)

        def action_specs(self) -> tuple[ActionSpec, ...]:
            return (action_spec,)

    @contextmanager
    def fake_instrument_context(*_args, **_kwargs):
        yield FakeInstrument(), None

    monkeypatch.setattr(
        cli,
        "load_settings",
        lambda config_file=None: SimpleNamespace(
            safety=SimpleNamespace(
                allow_writes=False,
                dry_run=True,
                default_ramp_interval_s=0.05,
            )
        ),
    )
    monkeypatch.setattr(cli, "_instrument_context", fake_instrument_context)

    captured_payloads: list[dict[str, object]] = []

    def fake_print_payload(payload, *, as_json: bool) -> None:
        del as_json
        captured_payloads.append(dict(payload))

    monkeypatch.setattr(cli, "_print_payload", fake_print_payload)

    args = argparse.Namespace(
        config_file=None,
        parameters_file=str(DEFAULT_PARAMETERS_FILE),
        include_backend_commands=False,
        backend_match=None,
        json=True,
    )
    exit_code = cli._cmd_capabilities(args)

    assert exit_code == cli.EXIT_OK
    assert captured_payloads
    payload = captured_payloads[-1]
    assert "parameters" in payload
    parameters_payload = payload["parameters"]
    assert isinstance(parameters_payload, dict)
    assert parameters_payload["count"] == 1
    items = parameters_payload["items"]
    assert isinstance(items, list)
    parameter = items[0]
    assert parameter["name"] == "bias_v"
    assert "description" not in parameter
    assert parameter["get_cmd"]["command"] == "Bias.Get"
    assert parameter["get_cmd"]["description"] == "Read configured bias voltage."
    assert parameter["get_cmd"]["docstring_full"].startswith("Bias.Get")
    assert parameter["get_cmd"]["response_fields"][0]["name"] == "Bias value"
    assert parameter["get_cmd"]["args"] == {"channel": 1}
    assert parameter["set_cmd"]["command"] == "Bias.Set"
    assert parameter["set_cmd"]["value_arg"] == "bias"
    assert parameter["set_cmd"]["description"] == "Write configured bias voltage."
    assert parameter["set_cmd"]["arg_fields"][0]["name"] == "bias"
    assert parameter["vals"]["kind"] == "numbers"
    actions_payload = payload["action_commands"]
    assert isinstance(actions_payload, dict)
    assert actions_payload["count"] == 1
    action = actions_payload["items"][0]
    assert action["name"] == "Scan_Action"
    assert action["safety_mode"] == "guarded"
    assert action["action_cmd"]["command"] == "Scan_Action"
    assert action["action_cmd"]["docstring_full"].startswith("Scan.Action")
    assert action["action_cmd"]["arg_fields"][0]["name"] == "Scan_action"


def test_capabilities_drops_top_level_description_and_empty_nested_fields(monkeypatch) -> None:
    spec = ParameterSpec(
        name="zspectr_retractsecond",
        label="Zspectr Retractsecond",
        unit="",
        value_type="int",
        get_cmd=ReadCommandSpec(
            command="ZSpectr_RetractSecondGet",
            payload_index=0,
            args={},
            description=(
                "Returns the configuration for the Second condition of the Auto Retract "
                "in the Z-Spectroscopy module."
            ),
        ),
        set_cmd=WriteCommandSpec(
            command="ZSpectr_RetractSecondSet",
            value_arg="Threshold",
            args={"Second_condition": 0, "Signal_index": 1, "Comparison": 0},
            description="",
        ),
        vals=ValidatorSpec(kind="ints"),
        safety=SafetySpec(min_value=None, max_value=None, max_step=None, ramp_enabled=True),
        description="",
    )

    class FakeInstrument:
        def parameter_specs(self) -> tuple[ParameterSpec, ...]:
            return (spec,)

        def action_specs(self) -> tuple[ActionSpec, ...]:
            return ()

    @contextmanager
    def fake_instrument_context(*_args, **_kwargs):
        yield FakeInstrument(), None

    monkeypatch.setattr(
        cli,
        "load_settings",
        lambda config_file=None: SimpleNamespace(
            safety=SimpleNamespace(
                allow_writes=False,
                dry_run=True,
                default_ramp_interval_s=0.05,
            )
        ),
    )
    monkeypatch.setattr(cli, "_instrument_context", fake_instrument_context)

    captured_payloads: list[dict[str, object]] = []

    def fake_print_payload(payload, *, as_json: bool) -> None:
        del as_json
        captured_payloads.append(dict(payload))

    monkeypatch.setattr(cli, "_print_payload", fake_print_payload)

    args = argparse.Namespace(
        config_file=None,
        parameters_file=str(DEFAULT_PARAMETERS_FILE),
        include_backend_commands=False,
        backend_match=None,
        json=True,
    )
    exit_code = cli._cmd_capabilities(args)

    assert exit_code == cli.EXIT_OK
    assert captured_payloads
    payload = captured_payloads[-1]
    parameters_payload = payload["parameters"]
    assert isinstance(parameters_payload, dict)
    items = parameters_payload["items"]
    assert isinstance(items, list)
    parameter = items[0]
    assert "description" not in parameter
    assert parameter["get_cmd"]["description"] == (
        "Returns the configuration for the Second condition of the Auto Retract "
        "in the Z-Spectroscopy module."
    )
    assert "description" not in parameter["set_cmd"]
