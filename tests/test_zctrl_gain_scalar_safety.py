"""Regression tests for GitHub issue #2.

`zctrl_gain` scalar ramp read P gain, wrote I gain, and zeroed the time
constant. These tests pin the fix:

  * a generic guard blocks scalar set/ramp for multi-field parameters;
  * `zctrl_i_gain` is a tuple-aware writable coordinate that preserves P and
    drives I via the time constant (T = P / I), matching the controller's
    documented `I = P / T` relationship.

The fake backend models the real firmware behavior confirmed against the
Nanonis SPM simulator: `ZCtrl_GainSet` stores P and T and ignores the wire-level
I argument; `ZCtrl_GainGet` returns I derived as P / T.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from nspmctl.client.base import NanonisHealth
from nspmctl.client.errors import NanonisProtocolError
from nspmctl.controller.extensions import DEFAULT_PARAMETERS_FILE
from nspmctl.safety import ChannelLimit, PolicyViolation, WritePolicy

_ZCTRL_PARAMS = (
    "zctrl_gain",
    "zctrl_i_gain",
    "zctrl_t_const",
    "zctrl_p_gain_hold_t",
    "zctrl_p_gain_hold_i",
)


class ZCtrlFakeClient:
    """Faithful model of ZCtrl gain firmware semantics (I = P / T)."""

    def __init__(
        self,
        *,
        p_gain: float = 2.0e-10,
        time_constant: float = 1.0e-3,
        fail_after: int | None = None,
    ) -> None:
        self._p = float(p_gain)
        self._t = float(time_constant)
        self._fail_after = fail_after
        self._set_count = 0
        self.calls: list[tuple[str, Mapping[str, Any] | None]] = []

    def connect(self) -> None:  # pragma: no cover - trivial
        pass

    def close(self) -> None:  # pragma: no cover - trivial
        pass

    def call(self, command: str, *, args: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        self.calls.append((command, args))
        if command == "ZCtrl_GainGet":
            i_gain = self._p / self._t if self._t else float("inf")
            return {"command": command, "method": command, "payload": [self._p, self._t, i_gain]}
        if command == "ZCtrl_GainSet":
            if self._fail_after is not None and self._set_count >= self._fail_after:
                raise NanonisProtocolError("simulated backend failure on ZCtrl_GainSet")
            assert args is not None
            # Firmware stores P and T; the passed I_gain is ignored (I = P / T).
            self._p = float(args["P_gain"])
            self._t = float(args["Time_constant_s"])
            self._set_count += 1
            return {"command": command, "method": command, "payload": []}
        raise KeyError(command)

    def available_commands(self) -> tuple[str, ...]:
        return ("ZCtrl_GainGet", "ZCtrl_GainSet")

    def version(self) -> str:
        return "fake/1.0"

    def health(self) -> NanonisHealth:
        return NanonisHealth(connected=True, endpoint="fake:1")

    # Test-only convenience.
    def tuple(self) -> tuple[float, float, float]:
        return self._p, self._t, (self._p / self._t if self._t else float("inf"))


def _make_controller(client: ZCtrlFakeClient):
    from nspmctl.controller import NanonisController

    policy = WritePolicy(
        allow_writes=True,
        dry_run=False,
        limits={
            "zctrl_i_gain": ChannelLimit(
                min_value=1.0e-12,
                max_value=1.0e-3,
                max_step=1.0e-6,
                ramp_interval_s=0.0,
            ),
            "zctrl_t_const": ChannelLimit(
                min_value=1.0e-9,
                max_value=1.0,
                max_step=1.0e-3,
                ramp_interval_s=0.0,
            ),
            "zctrl_p_gain_hold_t": ChannelLimit(
                min_value=1.0e-13,
                max_value=1.0e-6,
                max_step=1.0e-8,
                ramp_interval_s=0.0,
            ),
            "zctrl_p_gain_hold_i": ChannelLimit(
                min_value=1.0e-13,
                max_value=1.0e-6,
                max_step=1.0e-8,
                ramp_interval_s=0.0,
            ),
            # zctrl_gain scalar ops are guard-blocked before policy is consulted.
            "zctrl_gain": ChannelLimit(
                min_value=None, max_value=None, max_step=None, ramp_interval_s=0.0
            ),
        },
    )
    return NanonisController(
        name="zctrl_test",
        client=client,
        parameters_file=DEFAULT_PARAMETERS_FILE,
        include_parameters=_ZCTRL_PARAMS,
        write_policy=policy,
        auto_connect=False,
    )


def test_scalar_read_selects_i_gain_not_p_gain() -> None:
    client = ZCtrlFakeClient(p_gain=2.0e-10, time_constant=1.0e-3)
    instrument = _make_controller(client)
    try:
        # I = P / T = 2e-10 / 1e-3 = 2e-7, which is NOT the P gain (2e-10).
        value = instrument.get_parameter_value("zctrl_i_gain")
        assert value == pytest.approx(2.0e-7)
        assert value != pytest.approx(2.0e-10)
    finally:
        instrument.close()


def test_scalar_ramp_of_zctrl_gain_is_blocked() -> None:
    client = ZCtrlFakeClient()
    instrument = _make_controller(client)
    try:
        # Planning refuses the scalar ramp up front.
        with pytest.raises(PolicyViolation, match="multi-field"):
            instrument.plan_parameter_ramp(
                "zctrl_gain",
                start_value=2.0e-7,
                end_value=4.0e-7,
                step_value=5.0e-8,
                interval_s=0.0,
            )
        # The execution entrypoint also refuses and records a blocked audit entry.
        with pytest.raises(PolicyViolation, match="multi-field"):
            instrument.ramp_parameter(
                "zctrl_gain",
                start_value=2.0e-7,
                end_value=4.0e-7,
                step_value=5.0e-8,
                interval_s=0.0,
            )
        # No set command reached the backend.
        assert all(cmd != "ZCtrl_GainSet" for cmd, _ in client.calls)
        assert instrument.guarded_write_audit_log()[-1].status == "blocked"
    finally:
        instrument.close()


def test_scalar_single_step_of_zctrl_gain_is_blocked() -> None:
    client = ZCtrlFakeClient()
    instrument = _make_controller(client)
    try:
        with pytest.raises(PolicyViolation, match="multi-field"):
            instrument.set_parameter_single_step("zctrl_gain", 3.0e-7)
        assert all(cmd != "ZCtrl_GainSet" for cmd, _ in client.calls)
    finally:
        instrument.close()


def test_zctrl_gain_is_flagged_multi_field_but_still_structured_writable() -> None:
    client = ZCtrlFakeClient()
    instrument = _make_controller(client)
    try:
        assert instrument.is_scalar_rampable("zctrl_gain") is False
        assert instrument.is_scalar_rampable("zctrl_i_gain") is True
        # Explicit structured set with all three fields remains allowed.
        result = instrument.set_parameter_fields(
            "zctrl_gain",
            args={"P_gain": 3.0e-10, "Time_constant_s": 1.0e-3, "I_gain": 3.0e-7},
        )
        assert result["applied"] is True
        p, t, _ = client.tuple()
        assert p == pytest.approx(3.0e-10)
        assert t == pytest.approx(1.0e-3)
    finally:
        instrument.close()


def test_i_gain_ramp_preserves_p_and_follows_time_constant_rule() -> None:
    client = ZCtrlFakeClient(p_gain=2.0e-10, time_constant=1.0e-3)
    instrument = _make_controller(client)
    try:
        report = instrument.ramp_parameter(
            "zctrl_i_gain",
            start_value=2.0e-7,
            end_value=4.0e-7,
            step_value=5.0e-8,
            interval_s=0.0,
        )
        assert report.dry_run is False
        assert report.applied_steps >= 4

        p, t, i = client.tuple()
        # P preserved throughout.
        assert p == pytest.approx(2.0e-10)
        # I reached the target.
        assert i == pytest.approx(4.0e-7)
        # T follows the validated relationship T = P / I (never zeroed).
        assert t == pytest.approx(p / i)
        assert t != 0.0
    finally:
        instrument.close()


def test_i_gain_write_never_zeros_time_constant() -> None:
    client = ZCtrlFakeClient(p_gain=2.0e-10, time_constant=1.0e-3)
    instrument = _make_controller(client)
    try:
        instrument.set_parameter_fields("zctrl_i_gain", args={"I_gain": 5.0e-7})
        # The old bug sent Time_constant_s=0 via name-mismatch autofill.
        last_set = [entry for entry in client.calls if entry[0] == "ZCtrl_GainSet"][-1]
        set_args = last_set[1]
        assert set_args is not None
        assert set_args["Time_constant_s"] != 0.0
        assert set_args["Time_constant_s"] == pytest.approx(2.0e-10 / 5.0e-7)
        assert set_args["P_gain"] == pytest.approx(2.0e-10)
    finally:
        instrument.close()


def test_partial_ramp_failure_reports_last_confirmed_tuple() -> None:
    # Fail on the 3rd ZCtrl_GainSet: steps 1 and 2 commit, step 3 raises.
    client = ZCtrlFakeClient(p_gain=2.0e-10, time_constant=1.0e-3, fail_after=2)
    instrument = _make_controller(client)
    try:
        with pytest.raises(NanonisProtocolError):
            instrument.ramp_parameter(
                "zctrl_i_gain",
                start_value=2.0e-7,
                end_value=5.0e-7,
                step_value=5.0e-8,
                interval_s=0.0,
            )
        # Backend holds the last confirmed tuple (I = 2.5e-7 after 2 writes).
        p, t, i = client.tuple()
        assert p == pytest.approx(2.0e-10)
        assert i == pytest.approx(2.5e-7)
        assert t == pytest.approx(p / i)

        failed = instrument.guarded_write_audit_log()[-1]
        assert failed.status == "failed"
        assert failed.metadata["applied_steps"] == 2
    finally:
        instrument.close()


def test_restoration_reproduces_exact_initial_tuple() -> None:
    client = ZCtrlFakeClient(p_gain=2.0e-10, time_constant=1.0e-3)
    instrument = _make_controller(client)
    try:
        p0, t0, i0 = client.tuple()

        # Move I away, then restore it via the scalar coordinate.
        instrument.set_parameter_fields("zctrl_i_gain", args={"I_gain": 4.0e-7})
        assert client.tuple()[2] == pytest.approx(4.0e-7)

        instrument.set_parameter_fields("zctrl_i_gain", args={"I_gain": i0})
        p1, t1, i1 = client.tuple()
        assert p1 == pytest.approx(p0)
        assert t1 == pytest.approx(t0)
        assert i1 == pytest.approx(i0)
    finally:
        instrument.close()


def test_i_gain_rejects_non_positive_target() -> None:
    client = ZCtrlFakeClient()
    instrument = _make_controller(client)
    try:
        with pytest.raises((PolicyViolation, ValueError)):
            instrument.set_parameter_fields("zctrl_i_gain", args={"I_gain": 0.0})
    finally:
        instrument.close()


def test_i_gain_rejects_unsupported_extra_fields() -> None:
    client = ZCtrlFakeClient()
    instrument = _make_controller(client)
    try:
        with pytest.raises(ValueError, match="only accepts"):
            instrument.set_parameter_fields(
                "zctrl_i_gain", args={"I_gain": 3.0e-7, "P_gain": 1.0e-10}
            )
    finally:
        instrument.close()


def test_structured_partial_set_of_zctrl_gain_is_rejected() -> None:
    # The catastrophe path from issue #2: `set zctrl_gain --arg I_gain=X` used to
    # autofill P, fail to preserve the time constant (name mismatch), default it to
    # 0.0, and drive I = P/0 to infinity. It must now be refused before any write.
    client = ZCtrlFakeClient(p_gain=2.0e-10, time_constant=1.0e-3)
    instrument = _make_controller(client)
    try:
        with pytest.raises(ValueError, match="Time_constant_s"):
            instrument.set_parameter_fields("zctrl_gain", args={"I_gain": 3.0e-7})
        # Nothing was written to the backend; the tuple is untouched.
        assert all(cmd != "ZCtrl_GainSet" for cmd, _ in client.calls)
        assert client.tuple() == (2.0e-10, 1.0e-3, pytest.approx(2.0e-7))
    finally:
        instrument.close()


def test_structured_full_tuple_set_of_zctrl_gain_is_allowed() -> None:
    client = ZCtrlFakeClient()
    instrument = _make_controller(client)
    try:
        result = instrument.set_parameter_fields(
            "zctrl_gain",
            args={"P_gain": 3.0e-10, "Time_constant_s": 2.0e-3, "I_gain": 1.5e-7},
        )
        assert result["applied"] is True
        p, t, _ = client.tuple()
        assert p == pytest.approx(3.0e-10)
        assert t == pytest.approx(2.0e-3)  # never silently zeroed
    finally:
        instrument.close()


def test_i_gain_structured_set_enforces_channel_bounds() -> None:
    client = ZCtrlFakeClient()
    instrument = _make_controller(client)
    try:
        # max_value for zctrl_i_gain is 1e-3 in the test policy; 1.0 must be refused
        # even on the structured/single strategy path (not only during ramp planning).
        with pytest.raises(PolicyViolation, match="maximum"):
            instrument.set_parameter_fields("zctrl_i_gain", args={"I_gain": 1.0})
        assert all(cmd != "ZCtrl_GainSet" for cmd, _ in client.calls)
    finally:
        instrument.close()


def test_new_coordinates_read_the_correct_variable() -> None:
    client = ZCtrlFakeClient(p_gain=2.0e-10, time_constant=1.0e-3)  # I = 2e-7
    instrument = _make_controller(client)
    try:
        assert instrument.get_parameter_value("zctrl_t_const") == pytest.approx(1.0e-3)
        assert instrument.get_parameter_value("zctrl_p_gain_hold_t") == pytest.approx(2.0e-10)
        assert instrument.get_parameter_value("zctrl_p_gain_hold_i") == pytest.approx(2.0e-10)
        # all are scalar-rampable (strategy-backed) and multi-field
        for name in ("zctrl_t_const", "zctrl_p_gain_hold_t", "zctrl_p_gain_hold_i"):
            assert instrument.is_scalar_rampable(name) is True
    finally:
        instrument.close()


def test_t_const_ramp_holds_p_and_derives_i() -> None:
    client = ZCtrlFakeClient(p_gain=2.0e-10, time_constant=1.0e-3)
    instrument = _make_controller(client)
    try:
        instrument.ramp_parameter(
            "zctrl_t_const",
            start_value=1.0e-3,
            end_value=1.5e-3,
            step_value=2.5e-4,
            interval_s=0.0,
        )
        p, t, i = client.tuple()
        assert t == pytest.approx(1.5e-3)  # T reached target
        assert p == pytest.approx(2.0e-10)  # P held
        assert i == pytest.approx(p / t)  # I follows
    finally:
        instrument.close()


def test_p_gain_hold_t_ramp_holds_time_constant() -> None:
    client = ZCtrlFakeClient(p_gain=2.0e-10, time_constant=1.0e-3)
    instrument = _make_controller(client)
    try:
        instrument.ramp_parameter(
            "zctrl_p_gain_hold_t",
            start_value=2.0e-10,
            end_value=4.0e-10,
            step_value=5.0e-11,
            interval_s=0.0,
        )
        p, t, i = client.tuple()
        assert p == pytest.approx(4.0e-10)  # P reached target
        assert t == pytest.approx(1.0e-3)  # T held
        assert i == pytest.approx(p / t)  # I follows = P/T
    finally:
        instrument.close()


def test_p_gain_hold_i_ramp_holds_integral_gain() -> None:
    client = ZCtrlFakeClient(p_gain=2.0e-10, time_constant=1.0e-3)
    instrument = _make_controller(client)
    try:
        i_initial = client.tuple()[2]  # 2e-7
        instrument.ramp_parameter(
            "zctrl_p_gain_hold_i",
            start_value=2.0e-10,
            end_value=4.0e-10,
            step_value=5.0e-11,
            interval_s=0.0,
        )
        p, t, i = client.tuple()
        assert p == pytest.approx(4.0e-10)  # P reached target
        assert i == pytest.approx(i_initial)  # I held constant
        assert t == pytest.approx(p / i)  # T recomputed = P/I
    finally:
        instrument.close()


def test_new_coordinate_structured_set_enforces_bounds() -> None:
    client = ZCtrlFakeClient()
    instrument = _make_controller(client)
    try:
        # zctrl_t_const max is 1.0; 10.0 must be refused on the structured path.
        with pytest.raises(PolicyViolation, match="maximum"):
            instrument.set_parameter_fields("zctrl_t_const", args={"Time_constant_s": 10.0})
        assert all(cmd != "ZCtrl_GainSet" for cmd, _ in client.calls)
    finally:
        instrument.close()


def test_cli_positional_set_shorthand_blocked_for_multi_field(monkeypatch) -> None:
    import argparse
    from contextlib import contextmanager

    from nspmctl import cli
    from nspmctl.controller.extensions import (
        ArgFieldSpec,
        ParameterSpec,
        ReadCommandSpec,
        ResponseFieldSpec,
        SafetySpec,
        WriteCommandSpec,
    )

    def _resp(name: str) -> ResponseFieldSpec:
        return ResponseFieldSpec(
            index=0, name=name, type="float", unit="", wire_type="f", description=""
        )

    multi_field_spec = ParameterSpec(
        name="zctrl_gain",
        label="Zctrl Gain",
        get_cmd=ReadCommandSpec(
            command="ZCtrl_GainGet",
            payload_index=0,
            response_fields=(_resp("P-gain"), _resp("Time constant"), _resp("I-gain")),
        ),
        set_cmd=WriteCommandSpec(
            command="ZCtrl_GainSet",
            arg_fields=(
                ArgFieldSpec("P_gain", "float", "", "f", False, "", 0.0),
                ArgFieldSpec("Time_constant_s", "float", "s", "f", False, "", 0.0),
                ArgFieldSpec("I_gain", "float", "", "f", True, "", None),
            ),
        ),
        safety=SafetySpec(min_value=None, max_value=None, max_step=None),
    )

    class FakeInstrument:
        def parameter_spec(self, name: str) -> ParameterSpec:
            return multi_field_spec

        def is_scalar_rampable(self, name: str) -> bool:
            return False

        def set_parameter_fields(self, *args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("set_parameter_fields must not be reached")

    @contextmanager
    def fake_instrument_context(*_args, **_kwargs):
        yield FakeInstrument(), None

    monkeypatch.setattr(cli, "_instrument_context", fake_instrument_context)
    monkeypatch.setattr(cli, "_print_payload", lambda payload, *, as_json: None)

    args = argparse.Namespace(
        parameter="zctrl_gain",
        value="3e-7",
        arg=[],
        interval_s=None,
        plan_only=False,
        json=True,
    )
    with pytest.raises(ValueError, match="multi-field"):
        cli._cmd_set(args)
