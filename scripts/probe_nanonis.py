from __future__ import annotations

import argparse
import json
import sys

from nanonis_qcodes_controller.client import (
    format_report_text,
    probe_host_ports,
    report_to_dict,
)
from nanonis_qcodes_controller.config import load_settings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe candidate Nanonis TCP ports and rank likely API endpoints."
    )
    parser.add_argument(
        "--config-file",
        help="Path to YAML config file. Defaults to NANONIS_CONFIG_FILE or config/default.yaml.",
    )
    parser.add_argument("--host", help="Target host. Defaults to configured host.")
    parser.add_argument(
        "--ports",
        help="Ports list or ranges (example: 3364,6501-6504). Defaults to configured ports.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="TCP connection timeout in seconds. Defaults to configured timeout.",
    )
    parser.add_argument(
        "--attempts",
        type=int,
        default=1,
        help="Connection attempts per port for latency/availability scoring.",
    )
    parser.add_argument(
        "--backend",
        help="Backend name for optional command probe (example: nanonis_spm).",
    )
    parser.add_argument(
        "--command-probe",
        action="store_true",
        help="Run optional backend-level minimal read command on open ports.",
    )
    parser.add_argument("--json", action="store_true", help="Print deterministic JSON output.")
    args = parser.parse_args()

    if args.attempts < 1:
        parser.error("--attempts must be >= 1")

    try:
        settings = load_settings(config_file=args.config_file)
        report = probe_host_ports(
            host=args.host or settings.nanonis.host,
            ports=args.ports if args.ports is not None else settings.nanonis.ports,
            timeout_s=args.timeout if args.timeout is not None else settings.nanonis.timeout_s,
            attempts=args.attempts,
            backend=args.backend or settings.nanonis.backend,
            command_probe=args.command_probe,
        )
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report_to_dict(report), indent=2, sort_keys=True))
    else:
        print(format_report_text(report))

    return 0 if report.candidate_ports else 1


if __name__ == "__main__":
    raise SystemExit(main())
