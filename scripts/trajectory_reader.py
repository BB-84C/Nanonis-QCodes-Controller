from __future__ import annotations

import argparse
import json
from pathlib import Path

from nanonis_qcodes_controller.trajectory import follow_events, read_events


def main() -> int:
    parser = argparse.ArgumentParser(description="Read or follow trajectory journal events.")
    parser.add_argument(
        "--directory",
        type=Path,
        default=Path("artifacts/trajectory"),
        help="Trajectory directory.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Number of tail events for non-follow mode.",
    )
    parser.add_argument(
        "--follow",
        action="store_true",
        help="Follow new events as they are appended.",
    )
    parser.add_argument(
        "--interval-s",
        type=float,
        default=0.5,
        help="Polling interval for follow mode.",
    )
    args = parser.parse_args()

    if args.follow:
        print(f"Following trajectory events from: {args.directory}")
        try:
            for event in follow_events(
                args.directory,
                poll_interval_s=args.interval_s,
                start_at_end=False,
            ):
                print(json.dumps(event, ensure_ascii=True))
        except KeyboardInterrupt:
            return 0

        return 0

    events = read_events(args.directory, limit=args.limit)
    for event in events:
        print(json.dumps(event, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
