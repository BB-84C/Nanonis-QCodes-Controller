from __future__ import annotations

import pytest

from nanonis_qcodes_controller.safety import (
    ChannelLimit,
    PolicyViolation,
    WritePolicy,
)


def test_write_gate_blocks_when_disabled() -> None:
    policy = WritePolicy(
        allow_writes=False,
        dry_run=True,
        limits={"bias_v": ChannelLimit(min_value=-5.0, max_value=5.0, max_step=0.1)},
    )

    with pytest.raises(PolicyViolation, match="disabled"):
        policy.plan_scalar_write(channel="bias_v", current_value=2.0, target_value=2.1)


def test_bounds_violation_is_blocked() -> None:
    policy = WritePolicy(
        allow_writes=True,
        dry_run=True,
        limits={"bias_v": ChannelLimit(min_value=-1.0, max_value=1.0, max_step=0.1)},
    )

    with pytest.raises(PolicyViolation, match="outside bounds"):
        policy.plan_scalar_write(channel="bias_v", current_value=0.0, target_value=2.0)


def test_ramp_plan_obeys_step_and_slew_limits() -> None:
    policy = WritePolicy(
        allow_writes=True,
        dry_run=True,
        limits={
            "bias_v": ChannelLimit(
                min_value=-5.0,
                max_value=5.0,
                max_step=0.1,
                max_slew_per_s=0.5,
                ramp_interval_s=0.1,
            )
        },
    )

    plan = policy.plan_scalar_write(channel="bias_v", current_value=2.0, target_value=2.4)

    assert plan.step_count == 8
    assert plan.steps[-1] == 2.4


def test_cooldown_enforced_between_writes() -> None:
    policy = WritePolicy(
        allow_writes=True,
        dry_run=False,
        limits={
            "setpoint_a": ChannelLimit(
                min_value=0.0,
                max_value=1.0e-6,
                max_step=1.0e-12,
                cooldown_s=2.0,
            )
        },
    )
    policy.record_write(channel="setpoint_a", at_time_s=10.0)

    with pytest.raises(PolicyViolation, match="cooldown"):
        policy.plan_scalar_write(
            channel="setpoint_a",
            current_value=5.0e-11,
            target_value=5.2e-11,
            now_s=11.0,
        )


def test_execute_plan_respects_dry_run_and_live_modes() -> None:
    dry_policy = WritePolicy(
        allow_writes=True,
        dry_run=True,
        limits={"bias_v": ChannelLimit(min_value=-5.0, max_value=5.0, max_step=0.1)},
    )
    dry_plan = dry_policy.plan_scalar_write(channel="bias_v", current_value=2.0, target_value=2.2)

    dry_calls: list[float] = []
    dry_report = dry_policy.execute_plan(dry_plan, send_step=dry_calls.append)
    assert dry_report.dry_run is True
    assert dry_report.applied_steps == 0
    assert dry_calls == []

    live_policy = WritePolicy(
        allow_writes=True,
        dry_run=False,
        limits={"bias_v": ChannelLimit(min_value=-5.0, max_value=5.0, max_step=0.1)},
    )
    live_plan = live_policy.plan_scalar_write(channel="bias_v", current_value=2.0, target_value=2.2)

    live_calls: list[float] = []
    live_report = live_policy.execute_plan(
        live_plan, send_step=live_calls.append, sleep=lambda _: None
    )
    assert live_report.dry_run is False
    assert live_report.applied_steps == live_plan.step_count
    assert live_calls[-1] == 2.2


def test_single_step_plan_rejects_delta_above_max_step() -> None:
    policy = WritePolicy(
        allow_writes=True,
        dry_run=True,
        limits={"bias_v": ChannelLimit(min_value=-5.0, max_value=5.0, max_step=0.05)},
    )

    with pytest.raises(PolicyViolation, match="Single-step write"):
        _ = policy.plan_scalar_write_single_step(
            channel="bias_v",
            current_value=0.0,
            target_value=0.2,
        )


def test_single_step_plan_uses_one_step_and_custom_interval() -> None:
    policy = WritePolicy(
        allow_writes=True,
        dry_run=True,
        limits={"bias_v": ChannelLimit(min_value=-5.0, max_value=5.0, max_step=0.5)},
    )

    plan = policy.plan_scalar_write_single_step(
        channel="bias_v",
        current_value=1.0,
        target_value=1.2,
        interval_s=0.25,
    )

    assert plan.step_count == 1
    assert plan.steps == (1.2,)
    assert plan.interval_s == pytest.approx(0.25)


def test_single_step_plan_enforces_slew_using_interval() -> None:
    policy = WritePolicy(
        allow_writes=True,
        dry_run=True,
        limits={
            "bias_v": ChannelLimit(
                min_value=-5.0,
                max_value=5.0,
                max_step=1.0,
                max_slew_per_s=0.5,
                ramp_interval_s=0.1,
            )
        },
    )

    with pytest.raises(PolicyViolation, match="max_slew_per_s"):
        _ = policy.plan_scalar_write_single_step(
            channel="bias_v",
            current_value=0.0,
            target_value=0.2,
            interval_s=0.1,
        )

    ok_plan = policy.plan_scalar_write_single_step(
        channel="bias_v",
        current_value=0.0,
        target_value=0.2,
        interval_s=0.5,
    )
    assert ok_plan.step_count == 1
