# Architecture Overview

## Context
`nspmctl` is a thin, fast CLI over the `nanonis-spm` Python package. It exposes atomic read / guarded-write / ramp / action commands so orchestration agents can drive the Nanonis SPM controller (or its STM Simulator) without a GUI.

## High-level flow

```mermaid
flowchart LR
    A[Agent or Notebook] --> B[nspmctl CLI]
    B --> C[NanonisController]
    C --> D[NanonisTransportClient]
    D --> E[Backend Adapter\n(nanonis_spm)]
    E --> F[Nanonis TCP API]
    F --> G[Nanonis STM Simulator or Real Controller]

    C --> H[WritePolicy]
    H --> C
```

## Components
- `nspmctl/client`: transport client, backend registry, probe tools, normalized error mapping.
- `nspmctl/controller`: `NanonisController` class with spec-driven parameter access, guarded writes, and action dispatch.
- `nspmctl/cli.py`: agent-facing CLI contract (`nspmctl`) for capabilities/read/write/ramp/action/parameter-file workflows.
- `nspmctl/safety`: write policy engine (gate, bounds, ramp/slew, cooldown).
- `scripts/`: diagnostics and parameter-manifest tooling (`bridge_doctor.py`, `generate_parameters_manifest.py`).
- `tests/`: automated tests plus manual probe/demo helpers (`probe_nanonis.py`, `read_client_demo.py`, `guarded_write_demo.py`).

## Design properties
- Single in-flight command path in transport client to avoid protocol contention.
- Write path is explicit and policy-gated.
- Config-first deployment: host/ports/policy are environment or YAML driven.
- Parameter manifest is YAML-defined and parsed lazily; load is cached per process and uses libyaml's C loader when available.

## Public API surface (0.2.0)
- Stable CLI contract: documented `nspmctl` commands and JSON outputs.
- Python symbols `create_client` and `load_settings` are stable for embedding scenarios.
- Other internal Python symbols are provisional and may change across minor releases.
