# Nanonis <-> QCodes Bridge Plan (Simulator-First)

## Goal
Build a safe, testable Python intermediate layer between Nanonis STM Simulator and QCodes so automation code can control/query Nanonis without GUI interaction.

This project starts with read-only access, then introduces tightly guarded writes.
It also includes a non-blocking trajectory journal so agents can read operation history during experiments.

## Environment Assumptions
- Primary target: **STM Simulator**
- Known Nanonis install path: `C:\Program Files (x86)\Nanonis V5\STM Simulator`
- Expected simulator TCP ports: `3364`, `6501-6504`
- Runtime path/port values are configurable; code must not hardcode machine-specific values
- Real controller API access may depend on entitlement/runtime conditions
- LabVIEW `.vip` is not required for Python TCP client path in simulator workflow

## MVP Scope Boundary
### In scope
- Read-heavy monitoring via QCodes instrument parameters
- Minimal, policy-guarded writes (opt-in)
- Non-blocking trajectory capture of commands and state transitions
- Strong diagnostics, tests, and docs for internal lab use

### Out of scope (MVP)
- Autonomous scan decision-making
- Image-model closed-loop control
- High-rate feedback replacement

## Developer Workflow (Internal)
- Local setup:
  - `python -m venv .venv`
  - `.\.venv\Scripts\Activate.ps1`
  - `python -m pip install --upgrade pip`
  - `python -m pip install -e .[dev,qcodes,nanonis]`
- Quality checks:
  - `python -m pytest -q`
  - `ruff check .`
  - `black --check .`
  - `mypy nanonis_qcodes_controller`
- Simulator-gated integration:
  - `set NANONIS_RUN_SIMULATOR_TESTS=1`
  - `python -m pytest -q -m simulator`
- Manual probe/demo utilities now live under `tests/`:
  - `tests/probe_nanonis.py`
  - `tests/read_client_demo.py`
  - `tests/guarded_write_demo.py`

## Architecture (Target)
1. `client/`: transport and backend adapters (`NanonisClient` contract)
2. `qcodes_driver/`: QCodes instrument and parameter mapping
3. `safety/`: write policy, bounds, ramps/slew limits, dry-run gating
4. `config/`: YAML and env-based runtime config
5. `scripts/`: diagnostics and authoring helpers (`bridge_doctor.py`, `trajectory_reader.py`, `generate_parameters_manifest.py`)
6. `tests/`: unit and simulator integration suites
7. `trajectory/`: append-only event journal, transition detection, follow/replay API

## Phased Plan

### 1) Repo Bootstrap (Day 1)
Deliver:
- Package skeleton: `client/`, `qcodes_driver/`, `safety/`, `config/`, `tests/`, `scripts/`
- Tooling: `pytest`, `mypy`, `ruff`, `black`, optional `pre-commit`
- Config templates: `.env.example`, YAML profile with host/ports/version/write policy
- CI/local quality commands documented

Exit criteria:
- `pip install -e .` succeeds
- Baseline test passes
- Lint/type checks runnable and clean

### 2) Connectivity & Capability Probe (Day 1-2)
Deliver:
- `tests/probe_nanonis.py` to scan `127.0.0.1` ports `3364,6501-6504`
- TCP connect latency report
- Candidate API port detection and deterministic diagnostic output
- Optional minimal command probe via selected backend library

Exit criteria:
- Reliable simulator-open port identification on target machine

### 3) Transport + Adapter Layer (Day 2-4)
Deliver:
- Stable `NanonisClient` interface: `connect`, `close`, `call`, `version`, `health`
- One backend first (recommended: `nanonis_spm`), adapter-pluggable design
- Timeout/retry policy + serialized in-flight command lock
- Standardized error mapping (timeout/protocol/invalid arg/unavailable command)

Exit criteria:
- Standalone script performs 3-5 read commands repeatedly without instability

### 4) QCodes Read-Only Driver MVP (Week 2)
Deliver:
- `QcodesNanonisSTM` instrument
- Read-only parameter groups: `bias`, `current`, `zctrl`, `scan`, `signals`
- Metadata mapping: unit/label/validator + consistent parameter naming

Exit criteria:
- `station.add_component(nanonis)` and `nanonis.snapshot()` work reliably
- Repeated reads stable for 30-60 minutes without reconnect storms

### 5) Safety/Policy Layer for Writes (Week 3)
Deliver:
- Write gate (`allow_writes=false` default)
- Bounds by channel (bias/setpoint/frame)
- Max step + max slew/ramp helper
- Cooldown control for risky operations
- Dry-run mode (log-only)

Exit criteria:
- Invalid writes blocked with clear reasons
- Valid writes bounded/ramped and auditable

### 6) First Guarded Write Actions (Week 3)
Deliver:
- Bias set with ramp
- Setpoint set with bounds
- Optional scan frame update (if simulator-safe)
- Complex/destructive operations remain disabled

Exit criteria:
- No abrupt jumps in control changes
- Write flow fully logged and reversible in tests

### 7) Test Matrix (Week 3-4)
Deliver:
- Unit tests: adapter contract, parsing, policy checks, ramp math
- Integration tests (`-m simulator`): connect loops, read loops, guarded writes
- Local/CI runbook split by test marker

Exit criteria:
- Unit suite stable
- Simulator suite consistently passes on target machine

### 8) Operational Observability + Trajectory Journal (Week 4)
Deliver:
- Structured logs (optional JSON): timestamp, command, arg hash, latency, status
- Audit log for write attempts (allowed/blocked)
- Health/doctor CLI for port/protocol checks
- Trajectory journal for command + state transition events (scan/zctrl/bias/current/setpoint)
- Non-blocking telemetry pipeline (background writer, bounded queue, drop counters)
- Trajectory reader modes: follow stream and interval polling (`--interval`)

Exit criteria:
- Failures diagnosable without packet sniffing
- Trajectory logging does not block control/read path under load
- Long-duration replay is available from persisted journal segments

### 9) Docs + Handoff (Week 4)
Deliver:
- `docs/quickstart_simulator.md`
- `docs/safety_model.md`
- `docs/porting_to_real_controller.md`
- `docs/trajectory_model.md` (event schema, retention, follow/replay usage)
- Known limitations + entitlement gate checklist + architecture diagram + notebook example

Exit criteria:
- Teammate can run simulator demo from clean machine in under 1 hour

## Definition of Done (MVP)
- QCodes driver works against STM Simulator
- Read-only operations are stable and typed
- Writes are opt-in and policy-gated
- Trajectory journal can run for long sessions without blocking instrument operations
- Logs, tests, and docs are complete for internal lab use

## Phase 2 (Post-MVP)
- Scan workflow primitives (start/pause/stop, ROI templates)
- Image/signal quality metrics pipeline
- MCP server exposure for agent orchestration
- Human-intent annotation loop (agent asks operator "why" at configured intervals)
- Real controller rollout through supervised dry runs with strict policy defaults

## Trajectory + Human Intent Addendum

### Non-blocking requirement (hard requirement)
- Trajectory write path must never block instrument command path.
- If storage is slow/unavailable, control path continues and telemetry drop metrics are emitted.

### Indefinite trajectory capture
- Use rolling segment files with retention policy (time/size based), optional compression.
- Keep event IDs and timestamps so streams are replayable and queryable by range.

### Agent read behavior
- Agent can read the trajectory in follow mode or poll mode every `<interval>`.
- Reader access must be independent from control path (no lock contention on command execution).

### Human-intent learning loop
- During human-led operation, agent may ask rationale questions tied to recent trajectory transitions.
- Questions must be rate-limited, contextual, and linked to specific event IDs/timestamps.

---

## Stage 1 Implementation Thinking (Immediate Next Work)

### Objective
Create a clean, repeatable developer foundation before any protocol logic.

### Concrete tasks
1. Create initial Python package layout and `__init__.py` files.
2. Add `pyproject.toml` with:
   - Runtime deps: `qcodes`, selected backend (`nanonis_spm` or placeholder adapter contract only)
   - Dev deps: `pytest`, `pytest-cov`, `mypy`, `ruff`, `black`
3. Add tooling config blocks in `pyproject.toml` (or dedicated config files if preferred).
4. Add `.env.example`:
   - `NANONIS_HOST=127.0.0.1`
   - `NANONIS_PORTS=3364,6501,6502,6503,6504`
   - `NANONIS_ALLOW_WRITES=false`
   - `NANONIS_TIMEOUT_S=2.0`
5. Add `config/default.yaml` with equivalent defaults and policy placeholders.
6. Add a minimal smoke test (`tests/test_smoke.py`) verifying package import/version.
7. Add developer runbook snippet to `README.md`:
   - create venv
   - install editable
   - run tests/lint/type check

### Suggested first acceptance checks
- `python -m pip install -e .[dev]`
- `pytest -q`
- `ruff check .`
- `black --check .`
- `mypy client qcodes_driver safety`

### Risks to watch early
- Backend library API mismatch across versions
- QCodes dependency weight and environment conflicts
- Simulator port behavior differing before/after simulator startup

### Decision to lock in during Stage 1
Choose adapter strategy:
- **Option A (recommended):** define internal adapter contract first, implement backend in Stage 2/3.
- **Option B:** couple directly to one backend now, refactor to adapters later.

Recommendation: **Option A** for lower migration cost and cleaner tests.
