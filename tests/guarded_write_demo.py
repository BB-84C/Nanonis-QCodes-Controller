from __future__ import annotations

import argparse

from nanonis_qcodes_controller.qcodes_driver import QcodesNanonisSTM
from nanonis_qcodes_controller.safety import PolicyViolation


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preview or execute a guarded write through QCodes."
    )
    parser.add_argument(
        "--channel",
        choices=("bias_v", "zctrl_setpoint_a"),
        required=True,
        help="Target write channel.",
    )
    parser.add_argument("--target", type=float, required=True, help="Target scalar value.")
    parser.add_argument(
        "--config-file",
        help="Optional YAML config path (defaults to env/config defaults).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute the guarded write. Without this flag, only a plan is printed.",
    )
    args = parser.parse_args()

    instrument = QcodesNanonisSTM(
        "nanonis_guarded_demo", config_file=args.config_file, auto_connect=True
    )

    try:
        try:
            plan = instrument.plan_parameter_single_step(
                args.channel,
                args.target,
            )
            print(plan)
            if args.apply:
                report = instrument.set_parameter_single_step(
                    args.channel,
                    args.target,
                )
                print(report)
        except PolicyViolation as exc:
            print(f"Policy blocked write: {exc}")
            return 1
    finally:
        instrument.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
