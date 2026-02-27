# Policy Set Command and Live Defaults Design

## Goal
Add an `nqctl` command to set runtime policy flags (`allow_writes`, `dry_run`) and change packaged defaults to live-write mode (`allow_writes=true`, `dry_run=false`).

## Approved Scope
- Add a writable CLI surface under `policy`.
- Keep existing `policy show` behavior.
- Update release defaults in both packaged YAML and code fallback defaults.
- Document the new command in README.

## CLI Contract

```powershell
nqctl policy set --allow-writes true --dry-run false
nqctl policy set --allow-writes 1 --dry-run 0 --config-file config/default_runtime.yaml
```

### Behavior
- `policy set` accepts explicit booleans for `--allow-writes` and `--dry-run`.
- At least one flag is required.
- Command updates only `safety.allow_writes` / `safety.dry_run` in the selected runtime YAML file.
- Other config keys remain unchanged.
- Output payload reports file path and effective values after write.

## Config Write Rules
- Resolve target config path using `--config-file` if provided; otherwise use default runtime config path.
- If file does not exist, create minimal runtime YAML structure with `nanonis`, `safety`, and `trajectory` sections using current defaults.
- Preserve existing keys where possible; only mutate target safety fields.

## Default Policy Changes
- `nanonis_qcodes_controller/resources/config/default_runtime.yaml`
  - `safety.allow_writes: true`
  - `safety.dry_run: false`
- `nanonis_qcodes_controller/config/settings.py`
  - `SafetySettings.allow_writes = True`
  - `SafetySettings.dry_run = False`

## Safety and Compatibility Notes
- This intentionally changes baseline risk posture to live writes by default.
- Environment variables (`NANONIS_ALLOW_WRITES`, `NANONIS_DRY_RUN`) still override file and code defaults.
- Existing users with explicit config values keep their current behavior.

## Test Strategy
- Parser test: `policy set` arguments parse and require at least one flag.
- Handler tests: writes correct safety keys and returns expected payload.
- Settings/default tests: verify new default values in YAML and fallback dataclass defaults.
- Smoke test: `nqctl policy show` reflects new defaults when no overrides are present.

## Docs
- Update README policy section with `nqctl policy set` examples and note new shipped defaults.
