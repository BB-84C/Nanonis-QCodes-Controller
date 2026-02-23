# Unified Parameters Manifest Redesign

Date: 2026-02-23

## Objective
Streamline parameter authoring and loading by replacing split manifests (`default_parameters.yaml` + `extra_parameters.yaml`) with a single committed manifest (`config/parameters.yaml`) generated from `nanonis_spm.Nanonis`, while preserving existing curated write-safe definitions.

## Scope

### In scope
- One manifest: `config/parameters.yaml`.
- Import the full NanonisClass command surface (Get/Set methods) into manifest parameters.
- Preserve existing curated writable entries and safety where already defined.
- Remove extra-parameter scaffolding and split-file merge behavior.
- Add per-parameter description text from method docstrings.
- Support nullable safety fields so missing numeric limits do not block writable channels.

### Out of scope
- Reworking guarded-write global policy (`allow_writes`, `dry_run`).
- Changing explicit CLI ramp semantics (`start/end/step/interval`).
- Runtime dynamic generation at process startup.

## Confirmed Decisions
- Recommended approach selected: static, deterministic generated manifest committed to repo.
- Keep curated writable specs and append/import the rest from NanonisClass.
- If a parameter has both Get and Set commands, keep both in one parameter entry.
- `description` is populated from docstrings, excluding `Arguments:` and `Return arguments` blocks.
- For generated writable parameters, safety fields exist but may all be `null`.
- Policy must accept nullable safety values (no limit enforcement when `null`).
- Ramp behavior remains explicit from CLI inputs, independent from nullable policy limits.

## Target Manifest Layout

### File path
- `config/parameters.yaml`

### Top-level schema
- `version`
- `defaults`
- `meta`
- `parameters`

### Parameter schema additions
- Existing fields remain (`label`, `unit`, `type`, `get_cmd`, `set_cmd`, `vals`, `safety`, `snapshot_value`).
- New optional field:
  - `description`: short prose extracted from method docstring.

### Safety schema
For writable parameters, keep a `safety` mapping but allow nullable values:
- `min: null | number`
- `max: null | number`
- `max_step: null | number`
- `max_slew_per_s: null | number`
- `cooldown_s: null | number`
- `ramp_enabled: bool`
- `ramp_interval_s: null | number`

`null` means "no per-channel limit" for that field.

## Generation Strategy

### Source and discovery
- Introspect public callable methods on `nanonis_spm.Nanonis`.
- Include methods ending with `Get` and `Set`.
- Merge sibling `Get`/`Set` methods with the same base stem into one parameter candidate.

### Naming and merge precedence
- Derive parameter names from command stems using existing normalization conventions.
- Curated manifest entries are authoritative overlays:
  - curated values win for label/unit/type/args/payload_index/set_cmd/safety/vals/description.
  - generated values fill missing fields.

### Description extraction
- Parse method docstring text.
- Drop heading noise (e.g., `Bias.Get`) when present.
- Keep descriptive prose before `Arguments:`/`Return arguments` sections.
- Normalize whitespace and HTML entities.

### Set command argument mapping
For generated setters:
- Use signature argument names (excluding `self`).
- Infer selector/config args vs value arg using signature + docstring argument lines.
- Set `value_arg` to the detected scalar value argument.
- Keep remaining non-value args in `args` (with generated defaults/placeholders).
- If inference is uncertain, rely on curated override precedence.

### Deterministic output
- Sort parameters by name.
- Keep stable field ordering.
- Emit generation metadata counts in `meta` (e.g., commands scanned, pairs merged, parameters emitted).

## Loader and Driver Changes

### `qcodes_driver/extensions.py`
- Default file constant becomes `config/parameters.yaml`.
- Remove `DEFAULT_EXTRA_PARAMETERS_FILE`.
- Remove split-file merge workflow (`load_parameter_spec_bundle`) from active path.
- Parse nullable safety values without raising on missing numeric limits.
- Add support for `description` in `ParameterSpec`.

### `qcodes_driver/instrument.py`
- Remove `extra_parameters_file` constructor argument and call sites.
- Register writable parameters even when channel limits are nullable.
- Maintain guarded write execution through policy.
- Keep explicit ramp planning path unchanged.

### `safety/policy.py`
- Make channel limit fields nullable where applicable.
- Enforce each check only when corresponding limit is non-null.
- Keep `allow_writes` and `dry_run` semantics unchanged.

## CLI and Tooling Changes

### CLI surface
- Remove `--extra-parameters-file` from runtime args.
- Remove `parameters scaffold` command and scaffold helper internals.
- Keep `parameters discover` and `parameters validate`.
- Update `capabilities` payload to report one active parameters file.

### Scripts
- Replace scaffold-style script usage with a full-manifest generator workflow.

## Documentation Updates
Update all references from split manifests/scaffold flow to unified manifest flow:
- `README.md`
- `docs/cli_contract.md`
- `docs/extension_workflow.md`
- `docs/quickstart_simulator.md`
- `docs/architecture.md`
- `PLAN.md`

## Migration Plan
1. Generate `config/parameters.yaml` from current curated + imported commands.
2. Switch loader/constants/callers to the new path.
3. Remove `config/extra_parameters.yaml` and scaffold-specific code paths.
4. Update docs and examples.
5. Run verification suite and ensure deterministic regeneration behavior.

## Testing Strategy

### Unit tests
- Docstring description extraction (prose-only behavior).
- Setter argument mapping (value arg detection + selector args).
- Nullable safety parsing and validation acceptance.
- Policy checks skipped when nullable limits are unset.

### Integration tests
- Driver loads unified manifest and registers expected read/write parameters.
- Writable generated parameters are not rejected due to missing numeric limits.
- CLI contract checks for removed scaffold/extra-parameter flags.

### Regression checks
- Existing curated writable channels retain previous guarded constraints.
- Regeneration idempotence: rerun generator with unchanged inputs yields no manifest diff.
