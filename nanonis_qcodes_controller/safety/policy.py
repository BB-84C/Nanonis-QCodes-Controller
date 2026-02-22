from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from nanonis_qcodes_controller.config import SafetySettings, ScalarLimitSettings


class PolicyViolation(ValueError):
    pass


class ConfirmationRequired(PolicyViolation):
    pass


@dataclass(frozen=True)
class ChannelLimit:
    min_value: float
    max_value: float
    max_step: float
    max_slew_per_s: float | None = None
    cooldown_s: float = 0.0
    require_confirmation: bool = False
    ramp_interval_s: float = 0.05

    @classmethod
    def from_settings(cls, settings: ScalarLimitSettings) -> ChannelLimit:
        return cls(
            min_value=settings.min,
            max_value=settings.max,
            max_step=settings.max_step,
            max_slew_per_s=settings.max_slew_per_s,
            cooldown_s=settings.cooldown_s,
            require_confirmation=settings.require_confirmation,
            ramp_interval_s=settings.ramp_interval_s,
        )


@dataclass(frozen=True)
class WritePlan:
    channel: str
    current_value: float
    target_value: float
    bounded_target: float
    steps: tuple[float, ...]
    interval_s: float
    dry_run: bool
    reason: str | None = None

    @property
    def step_count(self) -> int:
        return len(self.steps)


@dataclass(frozen=True)
class WriteExecutionReport:
    channel: str
    dry_run: bool
    attempted_steps: int
    applied_steps: int
    initial_value: float
    target_value: float
    final_value: float


ConfirmationHook = Callable[[str, float, float, str | None], bool]


@dataclass
class WritePolicy:
    allow_writes: bool = False
    dry_run: bool = True
    limits: Mapping[str, ChannelLimit] = field(default_factory=dict)
    confirmation_hook: ConfirmationHook | None = None
    _last_write_at: dict[str, float] = field(default_factory=dict, init=False, repr=False)

    @classmethod
    def from_settings(
        cls,
        settings: SafetySettings,
        *,
        confirmation_hook: ConfirmationHook | None = None,
    ) -> WritePolicy:
        limits = {
            channel: ChannelLimit.from_settings(limit)
            for channel, limit in sorted(settings.limits.items())
        }
        return cls(
            allow_writes=settings.allow_writes,
            dry_run=settings.dry_run,
            limits=limits,
            confirmation_hook=confirmation_hook,
        )

    def ensure_writes_enabled(self) -> None:
        if not self.allow_writes:
            raise PolicyViolation("Writes are disabled by policy (allow_writes=false).")

    def plan_scalar_write(
        self,
        *,
        channel: str,
        current_value: float,
        target_value: float,
        confirmed: bool = False,
        reason: str | None = None,
        now_s: float | None = None,
    ) -> WritePlan:
        self.ensure_writes_enabled()
        limit = self._require_channel_limit(channel)

        current = float(current_value)
        target = float(target_value)

        if target < limit.min_value or target > limit.max_value:
            raise PolicyViolation(
                f"Channel '{channel}' target {target} is outside bounds "
                f"[{limit.min_value}, {limit.max_value}]."
            )

        effective_now = time.monotonic() if now_s is None else now_s
        self._enforce_cooldown(channel=channel, limit=limit, now_s=effective_now)
        self._enforce_confirmation(
            channel=channel,
            current_value=current,
            target_value=target,
            reason=reason,
            confirmed=confirmed,
            require_confirmation=limit.require_confirmation,
        )

        steps = _build_steps(current=current, target=target, limit=limit)
        return WritePlan(
            channel=channel,
            current_value=current,
            target_value=target,
            bounded_target=target,
            steps=steps,
            interval_s=limit.ramp_interval_s,
            dry_run=self.dry_run,
            reason=reason,
        )

    def execute_plan(
        self,
        plan: WritePlan,
        *,
        send_step: Callable[[float], None],
        sleep: Callable[[float], None] = time.sleep,
        now_s: float | None = None,
    ) -> WriteExecutionReport:
        attempted_steps = len(plan.steps)

        if plan.dry_run:
            final_value = plan.steps[-1] if plan.steps else plan.current_value
            return WriteExecutionReport(
                channel=plan.channel,
                dry_run=True,
                attempted_steps=attempted_steps,
                applied_steps=0,
                initial_value=plan.current_value,
                target_value=plan.target_value,
                final_value=final_value,
            )

        applied_steps = 0
        for step_index, step_value in enumerate(plan.steps):
            send_step(step_value)
            applied_steps += 1
            if step_index < attempted_steps - 1 and plan.interval_s > 0:
                sleep(plan.interval_s)

        self.record_write(channel=plan.channel, at_time_s=now_s)

        final_value = plan.steps[-1] if plan.steps else plan.current_value
        return WriteExecutionReport(
            channel=plan.channel,
            dry_run=False,
            attempted_steps=attempted_steps,
            applied_steps=applied_steps,
            initial_value=plan.current_value,
            target_value=plan.target_value,
            final_value=final_value,
        )

    def record_write(self, *, channel: str, at_time_s: float | None = None) -> None:
        self._last_write_at[channel] = time.monotonic() if at_time_s is None else at_time_s

    def _require_channel_limit(self, channel: str) -> ChannelLimit:
        limit = self.limits.get(channel)
        if limit is None:
            raise PolicyViolation(f"No channel limit configured for '{channel}'.")
        return limit

    def _enforce_cooldown(self, *, channel: str, limit: ChannelLimit, now_s: float) -> None:
        if limit.cooldown_s <= 0:
            return
        last_time = self._last_write_at.get(channel)
        if last_time is None:
            return

        elapsed = now_s - last_time
        if elapsed < limit.cooldown_s:
            remaining = limit.cooldown_s - elapsed
            raise PolicyViolation(
                f"Channel '{channel}' is in cooldown for another {remaining:.3f} s."
            )

    def _enforce_confirmation(
        self,
        *,
        channel: str,
        current_value: float,
        target_value: float,
        reason: str | None,
        confirmed: bool,
        require_confirmation: bool,
    ) -> None:
        if not require_confirmation:
            return
        if confirmed:
            return

        if self.confirmation_hook is not None:
            approved = self.confirmation_hook(channel, current_value, target_value, reason)
            if approved:
                return

        raise ConfirmationRequired(
            f"Channel '{channel}' requires confirmation before applying this write."
        )


def _build_steps(*, current: float, target: float, limit: ChannelLimit) -> tuple[float, ...]:
    delta = target - current
    if delta == 0:
        return (target,)

    step_count = max(1, math.ceil(abs(delta) / limit.max_step))

    if limit.max_slew_per_s is not None:
        if limit.ramp_interval_s <= 0:
            raise PolicyViolation(
                "ramp_interval_s must be positive when max_slew_per_s is configured."
            )
        slew_step_size = limit.max_slew_per_s * limit.ramp_interval_s
        step_count = max(step_count, math.ceil(abs(delta) / slew_step_size))

    increment = delta / step_count
    steps = tuple(current + increment * step_index for step_index in range(1, step_count + 1))
    if steps[-1] != target:
        steps = steps[:-1] + (target,)
    return steps
