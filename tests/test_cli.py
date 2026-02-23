from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest

from nanonis_qcodes_controller import cli
from nanonis_qcodes_controller.qcodes_driver.extensions import (
    DEFAULT_PARAMETERS_FILE,
    ParameterSpec,
    ReadCommandSpec,
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
        ),
        set_cmd=WriteCommandSpec(
            command="Bias.Set",
            value_arg="bias",
            args={"channel": 1},
            description="Write configured bias voltage.",
        ),
        vals=ValidatorSpec(kind="numbers", min_value=-10.0, max_value=10.0),
        safety=SafetySpec(min_value=-10.0, max_value=10.0, max_step=1.0, ramp_enabled=True),
        description="Tip-sample bias voltage.",
    )

    class FakeInstrument:
        def parameter_specs(self) -> tuple[ParameterSpec, ...]:
            return (spec,)

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
    assert parameter["description"] == "Tip-sample bias voltage."
    assert parameter["get_cmd"]["command"] == "Bias.Get"
    assert parameter["get_cmd"]["description"] == "Read configured bias voltage."
    assert parameter["get_cmd"]["args"] == {"channel": 1}
    assert parameter["set_cmd"]["command"] == "Bias.Set"
    assert parameter["set_cmd"]["value_arg"] == "bias"
    assert parameter["set_cmd"]["description"] == "Write configured bias voltage."
    assert parameter["vals"]["kind"] == "numbers"
