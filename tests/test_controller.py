from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from nspmctl.client.base import NanonisHealth
from nspmctl.safety import ChannelLimit, PolicyViolation, WritePolicy


class FakeClient:
    def __init__(self) -> None:
        self.connect_called = False
        self.close_called = False
        self.calls: list[tuple[str, Mapping[str, Any] | None]] = []
        self._bias_v = 2.0
        self._setpoint_a = 5.0e-11
        self._scan_frame = [0.0, 0.0, 20.0e-9, 20.0e-9, 0.0]
        self._scan_buffer = [[14], 256, 256]
        self._scan_status = 0

    def connect(self) -> None:
        self.connect_called = True

    def close(self) -> None:
        self.close_called = True

    def call(self, command: str, *, args: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        self.calls.append((command, args))

        if command == "Bias_Set":
            assert args is not None
            self._bias_v = float(args["Bias_value_V"])
            return {"command": command, "method": command, "payload": []}

        if command == "ZCtrl_SetpntSet":
            assert args is not None
            self._setpoint_a = float(args["Z_Controller_setpoint"])
            return {"command": command, "method": command, "payload": []}

        if command == "Scan_Action":
            assert args is not None
            action = int(args["Scan_action"])
            if action == 0:
                self._scan_status = 1
            elif action == 1:
                self._scan_status = 0
            return {"command": command, "method": command, "payload": []}

        if command == "Scan_BufferSet":
            assert args is not None
            channel_indexes = args["Channel_indexes"]
            if isinstance(channel_indexes, (list, tuple)):
                channels = [int(item) for item in channel_indexes]
            else:
                channels = [int(channel_indexes)]
            self._scan_buffer = [channels, int(args["Pixels"]), int(args["Lines"])]
            return {"command": command, "method": command, "payload": []}

        if command == "Scan_WaitEndOfScan":
            self._scan_status = 0
            return {"command": command, "method": command, "payload": [0, 0, ""]}

        if command == "Script_Autosave":
            return {"command": command, "method": command, "payload": []}

        table: dict[str, dict[str, Any]] = {
            "Bias_Get": {"payload": [self._bias_v], "value": self._bias_v},
            "Current_Get": {"payload": [5.0e-11], "value": 5.0e-11},
            "ZCtrl_ZPosGet": {"payload": [-1.0e-9], "value": -1.0e-9},
            "ZCtrl_SetpntGet": {"payload": [self._setpoint_a], "value": self._setpoint_a},
            "ZCtrl_OnOffGet": {"payload": [1], "value": 1},
            "Scan_StatusGet": {"payload": [self._scan_status], "value": self._scan_status},
            "Scan_FrameGet": {"payload": list(self._scan_frame)},
            "Scan_BufferGet": {
                "payload": [
                    len(self._scan_buffer[0]),
                    list(self._scan_buffer[0]),
                    int(self._scan_buffer[1]),
                    int(self._scan_buffer[2]),
                ]
            },
            "Signals_NamesGet": {
                "payload": [48, 3, ["Current (A)", "Bias (V)", "Z (m)"]],
            },
            "LockIn_ModOnOffGet": {"payload": [1], "value": 1},
        }

        payload = table[command]
        return {
            "command": command,
            "method": command,
            **payload,
        }

    def available_commands(self) -> tuple[str, ...]:
        return (
            "Bias_Get",
            "Bias_Set",
            "Current_Get",
            "Scan_Action",
            "Scan_BufferGet",
            "Scan_BufferSet",
            "Scan_FrameGet",
            "Scan_StatusGet",
            "Scan_WaitEndOfScan",
            "Script_Autosave",
            "Signals_NamesGet",
            "ZCtrl_OnOffGet",
            "ZCtrl_SetpntGet",
            "ZCtrl_SetpntSet",
            "ZCtrl_ZPosGet",
            "LockIn_ModOnOffGet",
        )

    def version(self) -> str:
        return "fake/1.0"

    def health(self) -> NanonisHealth:
        return NanonisHealth(connected=True, endpoint="fake:1")


def _write_parameter_file(
    file_path: Path,
    *,
    include_lockin: bool = False,
    include_actions: bool = False,
) -> None:
    lines = [
        "version: 1",
        "defaults:",
        "  snapshot_value: true",
        "  ramp_default_interval_s: 0.05",
        "parameters:",
        "  bias_v:",
        "    label: Bias",
        "    unit: V",
        "    type: float",
        "    get_cmd:",
        "      command: Bias_Get",
        "      payload_index: 0",
        "      args: {}",
        "    set_cmd:",
        "      command: Bias_Set",
        "      value_arg: Bias_value_V",
        "      args: {}",
        "    vals:",
        "      kind: numbers",
        "      min: -5.0",
        "      max: 5.0",
        "    safety:",
        "      min: -5.0",
        "      max: 5.0",
        "      max_step: 0.5",
        "      ramp_enabled: true",
        "      ramp_interval_s: 0.01",
        "  current_a:",
        "    label: Current",
        "    unit: A",
        "    type: float",
        "    get_cmd:",
        "      command: Current_Get",
        "      payload_index: 0",
        "      args: {}",
        "    set_cmd: false",
        "  zctrl_setpoint_a:",
        "    label: Z Setpoint",
        "    unit: A",
        "    type: float",
        "    get_cmd:",
        "      command: ZCtrl_SetpntGet",
        "      payload_index: 0",
        "      args: {}",
        "    set_cmd:",
        "      command: ZCtrl_SetpntSet",
        "      value_arg: Z_Controller_setpoint",
        "      args: {}",
        "    vals:",
        "      kind: numbers",
        "      min: 0.0",
        "      max: 1.0e-6",
        "    safety:",
        "      min: 0.0",
        "      max: 1.0e-6",
        "      max_step: 1.0e-10",
        "      ramp_enabled: true",
        "      ramp_interval_s: 0.01",
        "  zctrl_on:",
        "    label: Z Controller Enabled",
        "    unit: ''",
        "    type: bool",
        "    get_cmd:",
        "      command: ZCtrl_OnOffGet",
        "      payload_index: 0",
        "      args: {}",
        "    set_cmd: false",
        "  scan_status_code:",
        "    label: Scan Status Code",
        "    unit: ''",
        "    type: int",
        "    get_cmd:",
        "      command: Scan_StatusGet",
        "      payload_index: 0",
        "      args: {}",
        "    set_cmd: false",
        "  scan_frame_width_m:",
        "    label: Scan Frame Width",
        "    unit: m",
        "    type: float",
        "    get_cmd:",
        "      command: Scan_FrameGet",
        "      payload_index: 2",
        "      args: {}",
        "    set_cmd: false",
        "  scan_frame_height_m:",
        "    label: Scan Frame Height",
        "    unit: m",
        "    type: float",
        "    get_cmd:",
        "      command: Scan_FrameGet",
        "      payload_index: 3",
        "      args: {}",
        "    set_cmd: false",
        "  signals_table_size_bytes:",
        "    label: Signals Table Size",
        "    unit: B",
        "    type: int",
        "    get_cmd:",
        "      command: Signals_NamesGet",
        "      payload_index: 0",
        "      args: {}",
        "    set_cmd: false",
        "  signals_count:",
        "    label: Signals Count",
        "    unit: ''",
        "    type: int",
        "    get_cmd:",
        "      command: Signals_NamesGet",
        "      payload_index: 1",
        "      args: {}",
        "    set_cmd: false",
    ]

    if include_lockin:
        lines.extend(
            [
                "  lockin_mod_enabled:",
                "    label: LockIn Enabled",
                "    unit: ''",
                "    type: bool",
                "    get_cmd:",
                "      command: LockIn_ModOnOffGet",
                "      payload_index: 0",
                "      args: {}",
                "    set_cmd: false",
            ]
        )

    if include_actions:
        lines.extend(
            [
                "actions:",
                "  Scan_Action:",
                "    action_cmd:",
                "      command: Scan_Action",
                "      args:",
                "        Scan_action: 0",
                "        Scan_direction: 0",
                "      arg_types:",
                "        Scan_action: int",
                "        Scan_direction: int",
                "      description: Start or stop scanner movement.",
                "    safety:",
                "      mode: guarded",
                "  Scan_WaitEndOfScan:",
                "    action_cmd:",
                "      command: Scan_WaitEndOfScan",
                "      args:",
                "        Timeout_ms: -1",
                "      arg_types:",
                "        Timeout_ms: int",
                "      description: Wait until the current scan ends.",
                "    safety:",
                "      mode: alwaysAllowed",
                "  Script_Autosave:",
                "    action_cmd:",
                "      command: Script_Autosave",
                "      args:",
                "        Acquire_buffer: 0",
                "        Sweep_number: 1",
                "        All_sweeps_to_same_file: 0",
                "        Folder_path: 0.0",
                "        Basename: 0.0",
                "      arg_types:",
                "        Acquire_buffer: int",
                "        Sweep_number: int",
                "        All_sweeps_to_same_file: int",
                "        Folder_path: float",
                "        Basename: float",
                "      description: Configure autosave path and name.",
                "    safety:",
                "      mode: guarded",
            ]
        )

    file_path.write_text("\n".join(lines), encoding="utf-8")


def test_controller_methods_interface_reads_from_parameter_file(tmp_path: Path) -> None:
    from nspmctl.controller import NanonisController

    parameter_file = tmp_path / "default_parameters.yaml"
    _write_parameter_file(parameter_file)

    client = FakeClient()
    instrument = NanonisController(
        name="nanonis_test",
        client=client,
        parameters_file=parameter_file,
        auto_connect=False,
    )

    try:
        assert instrument.get_parameter_value("bias_v") == pytest.approx(2.0)
        assert instrument.get_parameter_value("current_a") == pytest.approx(5.0e-11)
        assert instrument.get_parameter_value("zctrl_setpoint_a") == pytest.approx(5.0e-11)
        assert instrument.get_parameter_value("zctrl_on") == 1
        assert instrument.get_parameter_value("scan_status_code") == 0
        assert instrument.get_parameter_value("scan_frame_width_m") == pytest.approx(20.0e-9)
        assert instrument.get_parameter_value("scan_frame_height_m") == pytest.approx(20.0e-9)
        assert instrument.get_parameter_value("signals_table_size_bytes") == 48
        assert instrument.get_parameter_value("signals_count") == 3

        assert instrument.parameter_spec("bias_v").writable is True
        assert instrument.parameter_spec("current_a").writable is False
    finally:
        instrument.close()


def test_auto_connect_uses_client_connect(tmp_path: Path) -> None:
    from nspmctl.controller import NanonisController

    parameter_file = tmp_path / "default_parameters.yaml"
    _write_parameter_file(parameter_file)

    client = FakeClient()
    instrument = NanonisController(
        name="nanonis_auto",
        client=client,
        parameters_file=parameter_file,
        auto_connect=True,
    )
    try:
        assert client.connect_called is True
    finally:
        instrument.close()


def test_single_step_dry_run_does_not_send_set_command(tmp_path: Path) -> None:
    from nspmctl.controller import NanonisController

    parameter_file = tmp_path / "default_parameters.yaml"
    _write_parameter_file(parameter_file)

    policy = WritePolicy(
        allow_writes=True,
        dry_run=True,
        limits={
            "bias_v": ChannelLimit(min_value=-5.0, max_value=5.0, max_step=0.5),
            "zctrl_setpoint_a": ChannelLimit(min_value=0.0, max_value=1.0e-6, max_step=1.0e-10),
        },
    )
    client = FakeClient()
    instrument = NanonisController(
        name="nanonis_dry",
        client=client,
        parameters_file=parameter_file,
        write_policy=policy,
        auto_connect=False,
    )

    try:
        report = instrument.set_parameter_single_step("bias_v", 2.2)
        assert report.dry_run is True
        assert report.applied_steps == 0
        assert all(command != "Bias_Set" for command, _ in client.calls)
    finally:
        instrument.close()


def test_single_step_live_applies_one_set_command(tmp_path: Path) -> None:
    from nspmctl.controller import NanonisController

    parameter_file = tmp_path / "default_parameters.yaml"
    _write_parameter_file(parameter_file)

    policy = WritePolicy(
        allow_writes=True,
        dry_run=False,
        limits={
            "bias_v": ChannelLimit(min_value=-5.0, max_value=5.0, max_step=0.5),
            "zctrl_setpoint_a": ChannelLimit(min_value=0.0, max_value=1.0e-6, max_step=1.0e-10),
        },
    )
    client = FakeClient()
    instrument = NanonisController(
        name="nanonis_live",
        client=client,
        parameters_file=parameter_file,
        write_policy=policy,
        auto_connect=False,
    )

    try:
        report = instrument.set_parameter_single_step("bias_v", 2.2)
        set_calls = [entry for entry in client.calls if entry[0] == "Bias_Set"]

        assert report.dry_run is False
        assert report.applied_steps == 1
        assert len(set_calls) == 1
        assert instrument.get_parameter_value("bias_v") == pytest.approx(2.2)
    finally:
        instrument.close()


def test_set_parameter_fields_autofills_from_get_snapshot(tmp_path: Path) -> None:
    from nspmctl.controller import NanonisController

    parameter_file = tmp_path / "default_parameters.yaml"
    _write_parameter_file(parameter_file)
    parameter_file.write_text(
        parameter_file.read_text(encoding="utf-8")
        + "\n".join(
            [
                "",
                "  scan_buffer:",
                "    label: Scan Buffer",
                "    unit: ''",
                "    type: int",
                "    get_cmd:",
                "      command: Scan_BufferGet",
                "      payload_index: 0",
                "      description: Returns the scan buffer parameters.",
                "      arg_fields: []",
                "      response_fields:",
                "        - index: 0",
                "          name: Number of channels",
                "          type: int",
                "          unit: ''",
                "          wire_type: i",
                "          description: Number of channels (int)",
                "        - index: 1",
                "          name: Channel indexes",
                "          type: array[int]",
                "          unit: ''",
                "          wire_type: '*i'",
                "          description: Channel indexes (1D array int)",
                "        - index: 2",
                "          name: Pixels",
                "          type: int",
                "          unit: ''",
                "          wire_type: i",
                "          description: Pixels (int)",
                "        - index: 3",
                "          name: Lines",
                "          type: int",
                "          unit: ''",
                "          wire_type: i",
                "          description: Lines (int)",
                "    set_cmd:",
                "      command: Scan_BufferSet",
                "      description: Configures the scan buffer parameters.",
                "      arg_fields:",
                "        - name: Channel_indexes",
                "          type: array[int]",
                "          unit: ''",
                "          wire_type: +*i",
                "          required: false",
                "          default: [1]",
                "          description: Channel indexes (1D array int)",
                "        - name: Pixels",
                "          type: int",
                "          unit: ''",
                "          wire_type: i",
                "          required: true",
                "          default: null",
                "          description: Pixels (int)",
                "        - name: Lines",
                "          type: int",
                "          unit: ''",
                "          wire_type: i",
                "          required: false",
                "          default: 1",
                "          description: Lines (int)",
                "    vals:",
                "      kind: ints",
                "    safety:",
                "      min: null",
                "      max: null",
                "      max_step: null",
                "      ramp_enabled: true",
            ]
        ),
        encoding="utf-8",
    )

    policy = WritePolicy(allow_writes=True, dry_run=False, limits={})
    client = FakeClient()
    instrument = NanonisController(
        name="nanonis_scan_buffer",
        client=client,
        parameters_file=parameter_file,
        write_policy=policy,
        auto_connect=False,
    )

    try:
        before = instrument.get_parameter_snapshot("scan_buffer")
        assert before["values"]["Channel indexes"] == [14]
        assert before["values"]["Lines"] == 256

        result = instrument.set_parameter_fields("scan_buffer", args={"Pixels": 512})
        set_calls = [entry for entry in client.calls if entry[0] == "Scan_BufferSet"]

        assert result["autofilled"]["Channel_indexes"] == [14]
        assert result["autofilled"]["Lines"] == 256
        assert len(set_calls) == 1
        set_args = set_calls[0][1]
        assert set_args is not None
        assert set_args["Channel_indexes"] == [14]
        assert set_args["Pixels"] == 512
        assert set_args["Lines"] == 256

        after = instrument.get_parameter_snapshot("scan_buffer")
        assert after["values"]["Channel indexes"] == [14]
        assert after["values"]["Pixels"] == 512
        assert after["values"]["Lines"] == 256
    finally:
        instrument.close()


def test_single_step_rejects_large_delta(tmp_path: Path) -> None:
    from nspmctl.controller import NanonisController

    parameter_file = tmp_path / "default_parameters.yaml"
    _write_parameter_file(parameter_file)

    policy = WritePolicy(
        allow_writes=True,
        dry_run=False,
        limits={
            "bias_v": ChannelLimit(min_value=-5.0, max_value=5.0, max_step=0.05),
            "zctrl_setpoint_a": ChannelLimit(min_value=0.0, max_value=1.0e-6, max_step=1.0e-10),
        },
    )
    client = FakeClient()
    instrument = NanonisController(
        name="nanonis_reject",
        client=client,
        parameters_file=parameter_file,
        write_policy=policy,
        auto_connect=False,
    )

    try:
        with pytest.raises(PolicyViolation, match="Single-step write"):
            _ = instrument.set_parameter_single_step("bias_v", 2.2)
    finally:
        instrument.close()


def test_ramp_parameter_applies_multiple_steps(tmp_path: Path) -> None:
    from nspmctl.controller import NanonisController

    parameter_file = tmp_path / "default_parameters.yaml"
    _write_parameter_file(parameter_file)

    policy = WritePolicy(
        allow_writes=True,
        dry_run=False,
        limits={
            "bias_v": ChannelLimit(
                min_value=-5.0, max_value=5.0, max_step=0.05, ramp_interval_s=0.0001
            ),
            "zctrl_setpoint_a": ChannelLimit(min_value=0.0, max_value=1.0e-6, max_step=1.0e-10),
        },
    )
    client = FakeClient()
    instrument = NanonisController(
        name="nanonis_ramp",
        client=client,
        parameters_file=parameter_file,
        write_policy=policy,
        auto_connect=False,
    )

    try:
        plan = instrument.plan_parameter_ramp(
            "bias_v",
            start_value=2.0,
            end_value=2.2,
            step_value=0.05,
            interval_s=0.0001,
        )
        assert plan.step_count >= 4

        report = instrument.ramp_parameter(
            "bias_v",
            start_value=2.0,
            end_value=2.2,
            step_value=0.05,
            interval_s=0.0001,
        )
        set_calls = [entry for entry in client.calls if entry[0] == "Bias_Set"]
        assert report.dry_run is False
        assert report.applied_steps == len(set_calls)
        assert instrument.get_parameter_value("bias_v") == pytest.approx(2.2)
    finally:
        instrument.close()


def test_guarded_write_audit_log_records_blocked_and_dry_run(tmp_path: Path) -> None:
    from nspmctl.controller import NanonisController

    parameter_file = tmp_path / "default_parameters.yaml"
    _write_parameter_file(parameter_file)

    blocked_policy = WritePolicy(allow_writes=False, dry_run=True, limits={})
    blocked_client = FakeClient()
    blocked_instrument = NanonisController(
        name="nanonis_blocked",
        client=blocked_client,
        parameters_file=parameter_file,
        write_policy=blocked_policy,
        auto_connect=False,
    )
    try:
        with pytest.raises(PolicyViolation):
            _ = blocked_instrument.set_parameter_single_step("bias_v", 2.1)

        blocked_entries = blocked_instrument.guarded_write_audit_log()
        assert blocked_entries[-1].operation == "set_single_step:bias_v"
        assert blocked_entries[-1].status == "blocked"
    finally:
        blocked_instrument.close()

    dry_policy = WritePolicy(
        allow_writes=True,
        dry_run=True,
        limits={
            "bias_v": ChannelLimit(min_value=-5.0, max_value=5.0, max_step=0.5),
            "zctrl_setpoint_a": ChannelLimit(min_value=0.0, max_value=1.0e-6, max_step=1.0e-10),
        },
    )
    dry_client = FakeClient()
    dry_instrument = NanonisController(
        name="nanonis_dry_audit",
        client=dry_client,
        parameters_file=parameter_file,
        write_policy=dry_policy,
        auto_connect=False,
    )
    try:
        _ = dry_instrument.set_parameter_single_step("bias_v", 2.2)
        dry_entries = dry_instrument.guarded_write_audit_log()
        assert dry_entries[-1].operation == "set_single_step:bias_v"
        assert dry_entries[-1].status == "dry_run"
    finally:
        dry_instrument.close()


def test_scan_control_helpers_issue_expected_commands(tmp_path: Path) -> None:
    from nspmctl.controller import NanonisController

    parameter_file = tmp_path / "default_parameters.yaml"
    _write_parameter_file(parameter_file)

    client = FakeClient()
    instrument = NanonisController(
        name="nanonis_scan_helpers",
        client=client,
        parameters_file=parameter_file,
        auto_connect=False,
    )

    try:
        instrument.start_scan(direction_up=True)
        assert client.calls[-1] == (
            "Scan_Action",
            {"Scan_action": 0, "Scan_direction": 1},
        )

        timed_out, file_path = instrument.wait_end_of_scan(timeout_ms=1234)
        assert timed_out is False
        assert file_path == ""

        instrument.stop_scan(direction_up=False)
        assert client.calls[-1] == (
            "Scan_Action",
            {"Scan_action": 1, "Scan_direction": 0},
        )
    finally:
        instrument.close()


def test_execute_action_guarded_respects_dry_run_policy(tmp_path: Path) -> None:
    from nspmctl.controller import NanonisController

    parameter_file = tmp_path / "default_parameters.yaml"
    _write_parameter_file(parameter_file, include_actions=True)

    policy = WritePolicy(allow_writes=True, dry_run=True, limits={})
    client = FakeClient()
    instrument = NanonisController(
        name="nanonis_action_dry_run",
        client=client,
        parameters_file=parameter_file,
        write_policy=policy,
        auto_connect=False,
    )

    try:
        report = instrument.execute_action(
            "Scan_Action", args={"Scan_action": 0, "Scan_direction": 1}
        )
        assert report["dry_run"] is True
        assert report["applied"] is False
        assert all(command != "Scan_Action" for command, _ in client.calls)
    finally:
        instrument.close()


def test_execute_action_guarded_applies_when_writes_enabled(tmp_path: Path) -> None:
    from nspmctl.controller import NanonisController

    parameter_file = tmp_path / "default_parameters.yaml"
    _write_parameter_file(parameter_file, include_actions=True)

    policy = WritePolicy(allow_writes=True, dry_run=False, limits={})
    client = FakeClient()
    instrument = NanonisController(
        name="nanonis_action_live",
        client=client,
        parameters_file=parameter_file,
        write_policy=policy,
        auto_connect=False,
    )

    try:
        report = instrument.execute_action(
            "Scan_Action", args={"Scan_action": 0, "Scan_direction": 1}
        )
        assert report["dry_run"] is False
        assert report["applied"] is True
        assert client.calls[-1] == ("Scan_Action", {"Scan_action": 0, "Scan_direction": 1})
    finally:
        instrument.close()


def test_execute_action_always_allowed_ignores_dry_run_for_readonly_actions(tmp_path: Path) -> None:
    from nspmctl.controller import NanonisController

    parameter_file = tmp_path / "default_parameters.yaml"
    _write_parameter_file(parameter_file, include_actions=True)

    policy = WritePolicy(allow_writes=False, dry_run=True, limits={})
    client = FakeClient()
    instrument = NanonisController(
        name="nanonis_action_allowed",
        client=client,
        parameters_file=parameter_file,
        write_policy=policy,
        auto_connect=False,
    )

    try:
        report = instrument.execute_action("Scan_WaitEndOfScan", args={"Timeout_ms": 1234})
        assert report["applied"] is True
        assert report["dry_run"] is False
        assert client.calls[-1] == ("Scan_WaitEndOfScan", {"Timeout_ms": 1234})
    finally:
        instrument.close()


def test_execute_action_preserves_string_value_when_float_coercion_fails(tmp_path: Path) -> None:
    from nspmctl.controller import NanonisController

    parameter_file = tmp_path / "default_parameters.yaml"
    _write_parameter_file(parameter_file, include_actions=True)

    policy = WritePolicy(allow_writes=True, dry_run=False, limits={})
    client = FakeClient()
    instrument = NanonisController(
        name="nanonis_action_string_fallback",
        client=client,
        parameters_file=parameter_file,
        write_policy=policy,
        auto_connect=False,
    )

    try:
        report = instrument.execute_action(
            "Script_Autosave",
            args={"Folder_path": r"C:\\tmp", "Basename": "scan001"},
        )
        assert report["applied"] is True
        assert client.calls[-1] == (
            "Script_Autosave",
            {
                "Acquire_buffer": 0,
                "Sweep_number": 1,
                "All_sweeps_to_same_file": 0,
                "Folder_path": r"C:\\tmp",
                "Basename": "scan001",
            },
        )
    finally:
        instrument.close()


def test_available_backend_commands_supports_match_filter(tmp_path: Path) -> None:
    from nspmctl.controller import NanonisController

    parameter_file = tmp_path / "default_parameters.yaml"
    _write_parameter_file(parameter_file)

    client = FakeClient()
    instrument = NanonisController(
        name="nanonis_backend_commands",
        client=client,
        parameters_file=parameter_file,
        auto_connect=False,
    )

    try:
        all_commands = instrument.available_backend_commands()
        scan_commands = instrument.available_backend_commands(match="scan")
        assert "Bias_Get" in all_commands
        assert "Scan_StatusGet" in scan_commands
        assert all("scan" in command.lower() for command in scan_commands)
    finally:
        instrument.close()


def test_include_parameters_reduces_registered_set(tmp_path: Path) -> None:
    from nspmctl.controller import NanonisController

    parameter_file = tmp_path / "default_parameters.yaml"
    _write_parameter_file(parameter_file)

    client = FakeClient()
    instrument = NanonisController(
        name="nanonis_include_subset",
        client=client,
        parameters_file=parameter_file,
        include_parameters=("bias_v", "current_a"),
        auto_connect=False,
    )

    try:
        names = tuple(spec.name for spec in instrument.parameter_specs())
        assert names == ("bias_v", "current_a")
    finally:
        instrument.close()


def test_unified_parameter_file_adds_new_parameters(tmp_path: Path) -> None:
    from nspmctl.controller import NanonisController

    default_file = tmp_path / "default_parameters.yaml"
    _write_parameter_file(default_file, include_lockin=True)

    client = FakeClient()
    instrument = NanonisController(
        name="nanonis_extra",
        client=client,
        parameters_file=default_file,
        auto_connect=False,
    )
    try:
        assert instrument.get_parameter_value("lockin_mod_enabled") == 1
    finally:
        instrument.close()


def test_extra_parameter_file_constructor_argument_is_rejected(tmp_path: Path) -> None:
    from nspmctl.controller import NanonisController

    default_file = tmp_path / "default_parameters.yaml"
    _write_parameter_file(default_file)

    client = FakeClient()
    with pytest.raises(TypeError, match="extra_parameters_file"):
        _ = NanonisController(
            name="nanonis_collision",
            client=client,
            parameters_file=default_file,
            extra_parameters_file=tmp_path / "unused_extra.yaml",
            auto_connect=False,
        )
