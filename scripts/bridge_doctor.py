from __future__ import annotations

import argparse
import json
from typing import Any

from nspmctl.client import format_report_text, probe_host_ports, report_to_dict
from nspmctl.config import load_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Bridge doctor: config and connectivity checks.")
    parser.add_argument("--config-file")
    parser.add_argument("--json", action="store_true", help="Print JSON report.")
    parser.add_argument(
        "--attempts",
        type=int,
        default=2,
        help="TCP attempts per port for probe checks.",
    )
    parser.add_argument(
        "--command-probe",
        action="store_true",
        help="Enable backend-level read command probe.",
    )
    args = parser.parse_args()

    settings = load_settings(config_file=args.config_file)

    report = probe_host_ports(
        host=settings.nanonis.host,
        ports=settings.nanonis.ports,
        timeout_s=settings.nanonis.timeout_s,
        attempts=args.attempts,
        backend=settings.nanonis.backend,
        command_probe=args.command_probe,
    )

    payload: dict[str, Any] = {
        "config": {
            "host": settings.nanonis.host,
            "ports": list(settings.nanonis.ports),
            "timeout_s": settings.nanonis.timeout_s,
            "backend": settings.nanonis.backend,
            "allow_writes": settings.safety.allow_writes,
            "dry_run": settings.safety.dry_run,
        },
        "probe": report_to_dict(report),
    }

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_report_text(report))

    return 0 if report.candidate_ports else 1


if __name__ == "__main__":
    raise SystemExit(main())
