# nspmctl

A thin, fast CLI over `nanonis-spm` for agent-driven Nanonis SPM
controller automation (real controller or STM Simulator).

`nspmctl` runs as either a short-lived one-shot command or, by default,
a thin client that forwards to a persistent background daemon. The
daemon holds one warm `NanonisController` and the open TCP connection
to the instrument, so subsequent agent tool calls converge toward
loopback IPC + Python startup latency instead of paying the import +
connect cost every time.

## What this provides

- `nspmctl`: atomic CLI commands (`get` / `set` / `ramp` / `act` /
  `capabilities` / `policy` / `doctor` / `daemon ...`) with stable JSON
  output schemas.
- `nspmctl daemon`: persistent warm-controller daemon, auto-spawned on
  first call.
- Strict write semantics:
  - `set` is always a guarded single-step write.
  - `ramp` is always an explicit multi-step trajectory.
- Default runtime policy: `allow_writes=true`, `dry_run=false`.

## Performance

End-to-end `get bias_v` against the STM Simulator on a developer laptop:

| Mode                              | p50      |
|-----------------------------------|---------:|
| `nspmctl --no-daemon get bias_v`  |  525 ms  |
| `nspmctl get bias_v` (warm daemon)|  105 ms  |
| raw `nanonis_spm` one-shot floor  |  110 ms  |

The daemon path approaches the raw `nanonis_spm` floor and is ~30x
faster than the previous `nqctl` baseline.

## v0.2 support contract

- Stable CLI surface: documented `nspmctl` subcommands and JSON outputs.
- Stable Python symbols for embedding: `nspmctl.client.create_client`,
  `nspmctl.config.load_settings`.
- Other Python symbols are provisional and may change across minor
  releases.

## Install

```powershell
python -m pip install nspmctl
nspmctl capabilities
```

Editable / from source:

```powershell
python -m pip install -e .
```

## Configure

1. Optionally copy `.env.example` to `.env`.
2. Set runtime values in `config/default_runtime.yaml`.
3. Unified parameter specs are in `config/parameters.yaml`.
   - `parameters`: scalar `get`/`set` mappings.
   - `actions`: non-`Get`/`Set` backend methods with `action_cmd` metadata.
4. Regenerate from `nanonis_spm.Nanonis` with `scripts/generate_parameters_manifest.py`.

Runtime config controls host, candidate ports, timeout, backend, and write policy.

## Daemon

```powershell
# Auto-managed: the first cold call spawns a daemon in the background,
# subsequent calls are warm.
nspmctl get bias_v

# Explicit lifecycle (optional):
nspmctl daemon start
nspmctl daemon status
nspmctl daemon stop
nspmctl daemon restart
nspmctl daemon logs --tail 80

# Diagnostics / CI: bypass the daemon entirely.
nspmctl --no-daemon get bias_v
# or:
$env:NSPMCTL_NO_DAEMON = "1"; nspmctl get bias_v
```

The daemon exits automatically after 30 minutes of idle. PID + port +
log files live under `%LOCALAPPDATA%\nspmctl\` on Windows
(`~/.local/state/nspmctl/` on Linux/macOS).

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
