# Action Surface Cutoff Design

Date: 2026-02-24

## Goal

Constrain manifest import to the intended Nanonis command surface by ignoring
class-callable members declared before `Bias_Set` in `nanonis_spm.Nanonis`.
This removes internal helper methods from generated `actions` while preserving
the current Get/Set parameter extraction and action generation flow.

## Decision

Use positional anchoring (Option 1):

- Discover candidate methods in class declaration order.
- Find `Bias_Set` as the anchor.
- Exclude all callable members before the anchor.
- Keep existing manifest behavior after discovery (split Get/Set to
  `parameters`, non-Get/Set to `actions`).

Rationale:

- Matches the requested contract exactly.
- Simple, deterministic, and low maintenance for a mostly stable upstream
  class layout.

## Design

### Discovery pipeline

Update `discover_nanonis_commands` in
`nanonis_qcodes_controller/qcodes_driver/manifest_generator.py`:

1. Read ordered members from `nanonis_spm.Nanonis.__dict__.items()`.
2. Keep callable members that do not start with `_`.
3. Locate the first member named `Bias_Set`.
4. Slice the ordered member list from `Bias_Set` onward.
5. Apply optional `match_pattern` filtering.
6. Build `CommandInfo` entries from remaining members.
7. Sort resulting commands by command name before returning (to keep stable
   generated YAML ordering independent of declaration order).

### Error handling

- If `Bias_Set` is not found in callable members, raise `ValueError` with a
  clear message indicating that command discovery cannot establish the Nanonis
  anchor.

### Compatibility

- No schema changes to `config/parameters.yaml`.
- No CLI contract changes (`nqctl capabilities`, `nqctl act` stay the same).
- Existing curated merge behavior remains unchanged.

## Testing strategy

Add/extend tests in `tests/test_parameter_manifest_generator.py`:

- Verify methods before `Bias_Set` are excluded by discovery.
- Verify methods at/after `Bias_Set` are included.
- Verify missing-anchor behavior raises a clear `ValueError`.

Run focused tests first, then full project checks before any release actions.

## Risks and mitigations

- Risk: upstream reorders class members unexpectedly.
  - Mitigation: fail fast when anchor is missing; tests exercise cutoff logic.
- Risk: inherited members might not appear in `__dict__`.
  - Mitigation: this design intentionally tracks the concrete class declaration
    surface; if upstream moves methods to a base class, update discovery rule
    explicitly in a follow-up.
