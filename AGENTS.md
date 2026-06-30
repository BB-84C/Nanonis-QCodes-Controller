
## nqctl Slimming / Performance Directives (2026-06-29)
- Slim the implementation aggressively, but preserve the existing CLI command contract and tool-result schema.
- Remove the trajectory-related CLI commands.
- Preferred rename is `nspmctl`; do not build a Rust client for this effort.
- Keep both one-shot/non-daemon mode and a daemon mode; daemon instances should auto-exit after 30 minutes of idle time.
- Treat this CLI as agent-facing high-call-volume tooling. Benchmark raw `nanonis-spm` `bias get` latency as the baseline, prioritize persistence/warm execution so repeated calls avoid re-importing `nanonis_spm`, target hot-call latency around `0.3 ms`, and treat roughly `19 ms` as acceptable if TCP-connect overhead cannot be reduced further.
