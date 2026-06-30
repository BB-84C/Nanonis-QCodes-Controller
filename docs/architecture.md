# Architecture Overview

## Context
This bridge sits between Nanonis controller endpoints and QCodes-facing automation code.
The design goal is simulator-first safety, with clear extension points for real-controller rollout and later MCP exposure.

## High-level flow

```mermaid
flowchart LR
    A[Agent or Notebook] --> B[QcodesNanonisSTM]
    B --> C[NanonisTransportClient]
    C --> D[Backend Adapter\n(nanonis_spm)]
    D --> E[Nanonis TCP API]
    E --> F[Nanonis STM Simulator or Real Controller]

    B --> G[WritePolicy]
    G --> B
```

## Components
- `nanonis_qcodes_controller/client`: transport client, backend registry, probe tools, normalized error mapping.
- `nanonis_qcodes_controller/qcodes_driver`: QCodes instrument interface with generic spec-driven parameter registration and guarded writes.
- `nanonis_qcodes_controller/cli.py`: agent-facing CLI contract (`nqctl`) for capabilities/read/write/ramp/parameter-file workflows.
- `nanonis_qcodes_controller/safety`: write policy engine (gate, bounds, ramp/slew, cooldown).
- `scripts/`: diagnostics and parameter-manifest tooling (`bridge_doctor.py`, `generate_parameters_manifest.py`).
- `tests/`: automated tests plus manual probe/demo helpers (`probe_nanonis.py`, `read_client_demo.py`, `guarded_write_demo.py`).

## Design properties
- Single in-flight command path in transport client to avoid protocol contention.
- Write path is explicit and policy-gated; default config blocks writes.
- Config-first deployment: host/ports/policy are environment or YAML driven.

## v1 API support contract
- Stable Python API symbols: `QcodesNanonisSTM`, `create_client`, `load_settings`.
- Stable CLI contract: documented `nqctl` commands and outputs.
- Other Python symbols are provisional/internal and may change across minor releases.
