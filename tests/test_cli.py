from __future__ import annotations

from datetime import datetime

import pytest

from nanonis_qcodes_controller import cli


def test_build_ramp_targets_increasing() -> None:
    targets = cli._build_ramp_targets(start=0.1, end=0.16, step=0.02)
    assert targets == pytest.approx((0.1, 0.12, 0.14, 0.16))


def test_build_ramp_targets_decreasing() -> None:
    targets = cli._build_ramp_targets(start=0.2, end=0.05, step=0.05)
    assert targets == pytest.approx((0.2, 0.15, 0.1, 0.05))


def test_build_ramp_targets_rejects_non_positive_step() -> None:
    with pytest.raises(ValueError, match="positive"):
        _ = cli._build_ramp_targets(start=0.0, end=1.0, step=0.0)


def test_normalize_help_args_supports_prefix_topic_style() -> None:
    assert cli._normalize_help_args(["-help", "extensions"]) == ["extensions", "--help"]
    assert cli._normalize_help_args(["-h"]) == ["--help"]


def test_json_output_is_default() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["actions", "list"])
    assert args.json is True


def test_text_output_opt_in_flag() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["actions", "list", "--text"])
    assert args.json is False


def test_now_utc_iso_is_valid_iso8601_utc() -> None:
    timestamp = cli._now_utc_iso()
    assert timestamp.endswith("Z")
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
