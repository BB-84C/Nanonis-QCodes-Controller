# Safety Model

## Objectives
- Prevent unsafe writes by default.
- Make write intent explicit and auditable.
- Support bounded, gradual transitions instead of abrupt jumps.

## Policy controls
`WritePolicy` applies these controls before any set command is sent:

1. Write gate (`allow_writes`): hard enable/disable switch.
2. Channel bounds (`min`, `max`): reject out-of-range targets.
3. Max step (`max_step`): split large moves into ramp steps.
4. Optional max slew (`max_slew_per_s`): constrain change rate.
5. Cooldown (`cooldown_s`): block writes too soon after prior write on channel.
6. Dry-run (`dry_run`): generate plan and audit event without sending commands.

## Current guarded operations
- `set_parameter_single_step(...)`
- `ramp_parameter(...)`

Both operations are generic and parameter-spec driven.

## Execution model
- Validate policy constraints.
- Build a `WritePlan` with interpolated steps.
- Execute step-by-step with configured ramp interval.
- Record success/failure in guarded write audit log.

## Failure behavior
- Policy violations raise clear exceptions (`PolicyViolation`).
- Execution failures keep full exception context and are audit-logged with `status=failed`.
- Dry-run never sends instrument set commands.

## Recommended rollout sequence
1. Keep `allow_writes=false` during initial deployment.
2. Turn on `allow_writes=true` with `dry_run=true` and inspect plans/audits.
3. Enable live writes with conservative limits.
4. Expand limits only after supervised simulator and hardware validation.

## Tuning guidance
- Bias: start with small `max_step` (for example, 10-50 mV).
- Setpoint: keep very small steps and non-zero cooldown.
- Scan frame: constrain geometry/angle bounds and use small movement steps.
