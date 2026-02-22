from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from nanonis_qcodes_controller.client import format_report_text, probe_host_ports, report_to_dict
from nanonis_qcodes_controller.config import load_settings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bridge doctor: config, connectivity, and trajectory checks."
    )
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

    trajectory_check = _trajectory_directory_check(settings.trajectory.directory)

    payload: dict[str, Any] = {
        "config": {
            "host": settings.nanonis.host,
            "ports": list(settings.nanonis.ports),
            "timeout_s": settings.nanonis.timeout_s,
            "backend": settings.nanonis.backend,
            "allow_writes": settings.safety.allow_writes,
            "dry_run": settings.safety.dry_run,
            "trajectory_enabled": settings.trajectory.enabled,
            "trajectory_directory": settings.trajectory.directory,
        },
        "probe": report_to_dict(report),
        "trajectory_directory_check": trajectory_check,
    }

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_report_text(report))
        print("-")
        print("Trajectory directory check:")
        print(json.dumps(trajectory_check, indent=2, sort_keys=True))

    return 0 if report.candidate_ports else 1


def _trajectory_directory_check(directory: str) -> dict[str, Any]:
    root = Path(directory)
    result: dict[str, Any] = {
        "directory": str(root),
        "exists": False,
        "writable": False,
        "error": None,
    }

    try:
        root.mkdir(parents=True, exist_ok=True)
        result["exists"] = root.exists()

        marker = root / ".doctor_write_test"
        marker.write_text("ok", encoding="utf-8")
        marker.unlink(missing_ok=True)
        result["writable"] = True
    except Exception as exc:  # pragma: no cover - filesystem env dependent
        result["error"] = f"{type(exc).__name__}: {exc}"

    return result


if __name__ == "__main__":
    raise SystemExit(main())
