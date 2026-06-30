# Nanonis-QCodes-Controller

Simulator-first Python bridge between Nanonis SPM controller interfaces and QCodes.

## What this project provides

- `nspmctl`: an agent-friendly CLI for atomic read/write/ramp operations.
- `NanonisController`: a QCodes instrument wrapper with spec-driven parameters.
- Strict write semantics:
  - `set` is always a guarded single-step write.
  - `ramp` is always an explicit multi-step trajectory.
- Default runtime policy (`allow_writes=true`, `dry_run=false`).

## v1 API support contract

- Stable Python API symbols: `NanonisController`, `create_client`, `load_settings`.
- Stable CLI contract: documented `nspmctl` commands and outputs.
- Other Python symbols are provisional/internal and may change across minor releases.

## Install

Install from a GitHub release (recommended for test users):

1. Open the releases page and download the wheel asset (`*.whl`), not the auto-generated source zip/tarball.
2. Create a virtual environment.
3. Install the wheel, then install optional runtime integrations.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install .\nspmctl-<version>-py3-none-any.whl
python -m pip install "qcodes>=0.46.0" "nanonis-spm>=1.0.3"
nspmctl capabilities
```

You can also install directly from a release URL:

```powershell
python -m pip install "https://github.com/BB-84C/Nanonis-QCodes-Controller/releases/download/v<version>/nspmctl-<version>-py3-none-any.whl"
```

Install from source:

```powershell
python -m pip install .
```

Optional extras:

```powershell
python -m pip install ".[qcodes]"
python -m pip install ".[nanonis]"
```

## Configure

1. Optionally copy `.env.example` to `.env`.
2. Set runtime values in `config/default_runtime.yaml`.
3. Unified parameter specs are in `config/parameters.yaml`.
   - `parameters`: scalar `get`/`set` mappings.
   - `actions`: non-`Get`/`Set` backend methods with `action_cmd` metadata.
4. Regenerate from `nanonis_spm.Nanonis` with `scripts/generate_parameters_manifest.py`.
Runtime config controls host, candidate ports, timeout, backend, and write policy.

## CLI command guide (`nspmctl`)

### Inspect and introspect

Get the machine-readable execution contract (lean payload):

```powershell
nspmctl capabilities
```

Capabilities item schemas (`nspmctl capabilities`):

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://bb-84c.github.io/nspmctl/schemas/capabilities-parameter-item.schema.json",
  "title": "nspmctl capabilities parameters.items[*]",
  "type": "object",
  "required": [
    "name",
    "label",
    "readable",
    "writable",
    "has_ramp",
    "get_cmd",
    "set_cmd",
    "safety"
  ],
  "properties": {
    "name": { "type": "string", "minLength": 1 },
    "label": { "type": "string" },
    "readable": { "type": "boolean" },
    "writable": { "type": "boolean" },
    "has_ramp": { "type": "boolean" },
    "get_cmd": {
      "oneOf": [
        { "type": "null" },
        {
          "type": "object",
          "required": ["command", "payload_index", "arg_fields", "response_fields"],
          "properties": {
            "command": { "type": "string", "minLength": 1 },
            "payload_index": { "type": "integer", "minimum": 0 },
            "description": { "type": "string" },
            "arg_fields": {
              "type": "array",
              "items": {
                "type": "object",
                "required": [
                  "name",
                  "type",
                  "unit",
                  "wire_type",
                  "required",
                  "description",
                  "default"
                ],
                "properties": {
                  "name": { "type": "string", "minLength": 1 },
                  "type": { "type": "string" },
                  "unit": { "type": "string" },
                  "wire_type": { "type": "string" },
                  "required": { "type": "boolean" },
                  "description": { "type": "string" },
                  "default": {}
                },
                "additionalProperties": false
              }
            },
            "response_fields": {
              "type": "array",
              "items": {
                "type": "object",
                "required": ["index", "name", "type", "unit", "wire_type", "description"],
                "properties": {
                  "index": { "type": "integer", "minimum": 0 },
                  "name": { "type": "string", "minLength": 1 },
                  "type": { "type": "string" },
                  "unit": { "type": "string" },
                  "wire_type": { "type": "string" },
                  "description": { "type": "string" }
                },
                "additionalProperties": false
              }
            }
          },
          "additionalProperties": true
        }
      ]
    },
    "set_cmd": {
      "oneOf": [
        { "type": "null" },
        {
          "type": "object",
          "required": ["command", "arg_fields"],
          "properties": {
            "command": { "type": "string", "minLength": 1 },
            "description": { "type": "string" },
            "arg_fields": {
              "type": "array",
              "items": {
                "type": "object",
                "required": [
                  "name",
                  "type",
                  "unit",
                  "wire_type",
                  "required",
                  "description",
                  "default"
                ],
                "properties": {
                  "name": { "type": "string", "minLength": 1 },
                  "type": { "type": "string" },
                  "unit": { "type": "string" },
                  "wire_type": { "type": "string" },
                  "required": { "type": "boolean" },
                  "description": { "type": "string" },
                  "default": {}
                },
                "additionalProperties": false
              }
            }
          },
          "additionalProperties": true
        }
      ]
    },
    "safety": {
      "oneOf": [
        { "type": "null" },
        {
          "type": "object",
          "required": [
            "min_value",
            "max_value",
            "max_step",
            "max_slew_per_s",
            "cooldown_s",
            "ramp_enabled",
            "ramp_interval_s"
          ],
          "properties": {
            "min_value": { "type": ["number", "null"] },
            "max_value": { "type": ["number", "null"] },
            "max_step": { "type": ["number", "null"] },
            "max_slew_per_s": { "type": ["number", "null"] },
            "cooldown_s": { "type": ["number", "null"] },
            "ramp_enabled": { "type": "boolean" },
            "ramp_interval_s": { "type": ["number", "null"] }
          },
          "additionalProperties": false
        }
      ]
    }
  },
  "additionalProperties": false
}
```

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://bb-84c.github.io/nspmctl/schemas/capabilities-action-command-item.schema.json",
  "title": "nspmctl capabilities action_commands.items[*]",
  "type": "object",
  "required": ["name", "action_cmd", "safety_mode"],
  "properties": {
    "name": { "type": "string", "minLength": 1 },
    "action_cmd": {
      "type": "object",
      "required": ["command", "arg_fields"],
      "properties": {
        "command": { "type": "string", "minLength": 1 },
        "description": { "type": "string" },
        "arg_fields": {
          "type": "array",
          "items": {
            "type": "object",
            "required": [
              "name",
              "type",
              "unit",
              "wire_type",
              "required",
              "description",
              "default"
            ],
            "properties": {
              "name": { "type": "string", "minLength": 1 },
              "type": { "type": "string" },
              "unit": { "type": "string" },
              "wire_type": { "type": "string" },
              "required": { "type": "boolean" },
              "description": { "type": "string" },
              "default": {}
            },
            "additionalProperties": false
          }
        }
      },
      "additionalProperties": true
    },
    "safety_mode": {
      "type": "string",
      "enum": ["alwaysAllowed", "guarded", "blocked"]
    }
  },
  "additionalProperties": false
}
```

Show the legacy full payload (old capabilities surface):

```powershell
nspmctl showall
```

Inspect backend command inventory and connectivity preflight:

```powershell
nspmctl backend commands --match Scan
nspmctl doctor --command-probe
```

List observable metadata and high-level CLI action descriptors:

```powershell
nspmctl observables list
nspmctl actions list
```

Inspect and update runtime policy:

```powershell
nspmctl policy show
nspmctl policy set --allow-writes true --dry-run false
```

### Execute operations

Read a parameter:

```powershell
nspmctl get bias_v
```

For multi-field responses, `get` returns structured fields (not only one scalar):

```powershell
nspmctl get scan_buffer
```

Apply writes with structured args (canonical form):

```powershell
nspmctl set bias_v --arg Bias_value_V=0.12 (single arg input)
nspmctl set scan_buffer --arg Pixels=512 --arg Lines=512 (multiple args input)
```


Defaulting/autofill mechanism for partial `set`:

- Explicit `--arg` values always win.
- Missing required set fields trigger one read (`get_cmd`) and are filled by normalized field name.
- Matching is by field name, not response index position.
- Get-only fields with no set counterpart are ignored.
- Remaining unresolved optional fields can fall back to manifest defaults.

Apply explicit guarded ramp (scalar parameters):

```powershell
nspmctl ramp bias_v 0.10 0.25 0.01 --interval-s 0.10
```

Invoke one manifest action command with structured args:

```powershell
nspmctl act Scan_Action --arg Scan_action=0 --arg Scan_direction=1
nspmctl act Scan_WaitEndOfScan --arg Timeout_ms=5000
```

For `act`, required/default behavior is driven by `action_cmd.arg_fields` in the manifest.

### `act` vs metadata surfaces

- `nspmctl act <action_name> --arg key=value` executes one backend action command from
  the manifest `actions` section.
- `nspmctl actions list` lists CLI-level action descriptors (what workflows the CLI
  supports, with safety hints and templates).
- `nspmctl capabilities` exposes executable manifest action inventory under
  `action_commands.items[*]` (command schema, `arg_fields`, safety mode).

### Output and help

JSON is the default output format. Use `--text` for human-readable key/value output.

```powershell
nspmctl -help
nspmctl -help showall
nspmctl -help set
nspmctl -help act
```

## QCodes usage

```python
from qcodes.station import Station
from nspmctl.controller import NanonisController

station = Station()
nanonis = NanonisController("nanonis", auto_connect=True)
station.add_component(nanonis)

print(nanonis.bias_v())
print(nanonis.current_a())

nanonis.close()
```

## Documentation index

- CLI contract: `docs/cli_contract.md`
- Extension workflow: `docs/extension_workflow.md`
- Safety model: `docs/safety_model.md`
- Architecture overview: `docs/architecture.md`
- Simulator quickstart: `docs/quickstart_simulator.md`
- Porting to real controller: `docs/porting_to_real_controller.md`
- Private-index release runbook: `docs/release_private_index.md`

Project planning and internal development workflow details: `PLAN.md`
