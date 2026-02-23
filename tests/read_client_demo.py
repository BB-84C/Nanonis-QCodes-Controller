from __future__ import annotations

import argparse
import time
from collections.abc import Sequence

from nanonis_qcodes_controller.client import create_client

DEFAULT_COMMANDS = (
    "Bias_Get",
    "Current_Get",
    "ZCtrl_ZPosGet",
    "ZCtrl_OnOffGet",
    "Scan_StatusGet",
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run repeated read commands through the transport client."
    )
    parser.add_argument(
        "--config-file",
        help="Path to runtime YAML config file. Defaults to NANONIS_CONFIG_FILE or config/default_runtime.yaml.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=5,
        help="How many read loop iterations to run.",
    )
    parser.add_argument(
        "--interval-s",
        type=float,
        default=0.2,
        help="Delay between loop iterations in seconds.",
    )
    parser.add_argument(
        "--commands",
        help="Comma-separated command list. Defaults to five core read commands.",
    )
    args = parser.parse_args()

    if args.iterations < 1:
        parser.error("--iterations must be >= 1")
    if args.interval_s < 0:
        parser.error("--interval-s must be >= 0")

    try:
        commands = _parse_commands(args.commands)
    except ValueError as exc:
        parser.error(str(exc))
    client = create_client(config_file=args.config_file)

    print(f"Backend version: {client.version()}")
    print(f"Commands: {', '.join(commands)}")

    try:
        client.connect()
        print(f"Connected endpoint: {client.health().endpoint}")
        for iteration in range(1, args.iterations + 1):
            print(f"Iteration {iteration}/{args.iterations}")
            for command in commands:
                response = client.call(command)
                value = response.get("value", response.get("payload"))
                print(f"  {command:<16} -> {value}")
            if iteration < args.iterations and args.interval_s > 0:
                time.sleep(args.interval_s)
    finally:
        client.close()

    print("Read demo completed successfully.")
    return 0


def _parse_commands(raw_commands: str | None) -> Sequence[str]:
    if raw_commands is None:
        return DEFAULT_COMMANDS
    parsed = [token.strip() for token in raw_commands.split(",") if token.strip()]
    if not parsed:
        raise ValueError("At least one command is required.")
    return tuple(parsed)


if __name__ == "__main__":
    raise SystemExit(main())
