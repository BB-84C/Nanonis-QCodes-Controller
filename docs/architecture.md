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

    B --> H[TrajectoryJournal]
    H --> I[JSONL Segments]
    I --> J[Trajectory Reader / Follow]
```

## Components
- `nanonis_qcodes_controller/client`: transport client, backend registry, probe tools, normalized error mapping.
- `nanonis_qcodes_controller/qcodes_driver`: QCodes instrument interface, guarded writes, and dynamic manifest-based read parameter extension.
- `nanonis_qcodes_controller/cli.py`: agent-facing CLI contract (`nqctl`) for capabilities/read/write/manifest/trajectory workflows.
- `nanonis_qcodes_controller/safety`: write policy engine (gate, bounds, ramp/slew, cooldown, confirmation).
- `nanonis_qcodes_controller/trajectory`: non-blocking event journal and readers.
- `scripts/`: operator and diagnostics tools (`probe_nanonis.py`, `bridge_doctor.py`, sweep demos).

## Design properties
- Single in-flight command path in transport client to avoid protocol contention.
- Write path is explicit and policy-gated; default config blocks writes.
- Trajectory logging is append-only and non-blocking; dropped events are counted.
- Config-first deployment: host/ports/policy/trajectory are environment or YAML driven.
