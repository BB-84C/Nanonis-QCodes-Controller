# Porting to Real Controller

## Scope
This document covers the simulator-to-real transition strategy and preflight checks.

## Entitlement gate checklist
Before connecting to real hardware, verify all items:

- Controller license/entitlement includes remote API access.
- Runtime mode enables required TCP endpoints.
- Host firewall allows inbound/outbound traffic on selected API ports.
- User account has permissions for controller API operations.
- Nanonis version and API behavior are validated against current backend adapter.
- Emergency stop and operator supervision procedures are active.

If any item is uncertain, do not enable live writes.

## Recommended rollout path
1. Probe only: run `scripts/bridge_doctor.py` and `scripts/probe_nanonis.py`.
2. Read-only mode: keep `allow_writes=false` and run repeated read loops.
3. Dry-run writes: set `allow_writes=true`, `dry_run=true`, inspect plans/audits.
4. Live guarded writes: enable `dry_run=false` with conservative limits.
5. Supervised scaling: gradually expand limits under lab oversight.

## Porting checks
- Confirm stable endpoint selection over repeated reconnect cycles.
- Validate readback consistency for bias, current, setpoint, and scan state.
- Run simulator-marked tests on target machine where feasible.
- Verify trajectory logging path permissions and storage health.

## Known limitations (current)
- Backend adapter currently targets `nanonis_spm`; alternate backends are not yet implemented.
- Some command payload parsing assumes current simulator/API response shapes.
- Trajectory retention is segment-based but does not yet include built-in compression/aging cleanup.
- Human-intent Q&A loop is planned post-MVP and not implemented yet.

## Safety reminder
Never disable policy protections for convenience on real hardware.
If behavior is unexpected, stop scans, restore safe setpoints, and inspect trajectory and audit logs before retrying.
