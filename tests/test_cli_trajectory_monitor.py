from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from nanonis_qcodes_controller import cli
from nanonis_qcodes_controller.trajectory.monitor_config import (
    MonitorConfig,
    default_monitor_config,
    load_staged_monitor_config,
    save_staged_monitor_config,
)


def _payload_from_stdout(capsys) -> dict[str, object]:
    captured = capsys.readouterr()
    assert captured.err == ""
    return json.loads(captured.out)


def test_monitor_run_fails_when_staged_run_name_missing(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli, "load_staged_monitor_config", lambda path=None: default_monitor_config(run_name="")
    )

    exit_code = cli.main(["trajectory", "monitor", "run", "--iterations", "1"])

    assert exit_code == cli.EXIT_INVALID_INPUT
    payload = _payload_from_stdout(capsys)
    assert payload["error"]["type"] == "ValueError"
    assert "run_name" in payload["error"]["message"]


def test_monitor_config_set_persists_run_name(tmp_path: Path, monkeypatch, capsys) -> None:
    staged_path = tmp_path / "monitor-config.json"
    monkeypatch.setattr(cli, "default_staged_config_path", lambda: staged_path)

    exit_code = cli.main(["trajectory", "monitor", "config", "set", "--run-name", "run-123"])

    assert exit_code == cli.EXIT_OK
    _payload_from_stdout(capsys)
    loaded = load_staged_monitor_config(path=staged_path)
    assert loaded.run_name == "run-123"


def test_monitor_run_clears_staged_run_name_after_completion(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    staged_path = tmp_path / "monitor-config.json"
    save_staged_monitor_config(
        MonitorConfig(
            run_name="run-abc",
            interval_s=0.2,
            rotate_entries=5,
            action_window_s=3.0,
            db_directory=str(tmp_path),
            db_name="trajectory.sqlite3",
            signal_labels=("Signal A",),
            spec_labels=("Spec A",),
        ),
        path=staged_path,
    )
    monkeypatch.setattr(cli, "default_staged_config_path", lambda: staged_path)

    class FakeStore:
        instances: list[FakeStore] = []

        def __init__(self, db_path: Path | str) -> None:
            self.db_path = Path(db_path)
            self.created_run_name = ""
            FakeStore.instances.append(self)

        def initialize_schema(self) -> None:
            return None

        def create_run(self, *, run_name: str, started_at_utc: str) -> int:
            self.created_run_name = run_name
            return 41

        def close(self) -> None:
            return None

    class FakeRunner:
        instances: list[FakeRunner] = []

        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.iterations: list[int] = []
            FakeRunner.instances.append(self)

        def run_iterations(self, count: int) -> int:
            self.iterations.append(count)
            return count

    class FakeInstrument:
        def parameter_specs(self) -> tuple[SimpleNamespace, ...]:
            return (
                SimpleNamespace(name="signal_a", label="Signal A", readable=True, vals=None),
                SimpleNamespace(
                    name="spec_a",
                    label="Spec A",
                    readable=True,
                    vals=SimpleNamespace(kind="numbers", min_value=0.0, max_value=1.0, choices=()),
                ),
            )

        def get_parameter_value(self, name: str) -> object:
            return {"signal_a": 1.23, "spec_a": 0.55}[name]

    @contextmanager
    def fake_instrument_context(*_args, **_kwargs):
        yield FakeInstrument(), None

    monkeypatch.setattr(cli, "TrajectorySQLiteStore", FakeStore)
    monkeypatch.setattr(cli, "TrajectoryMonitorRunner", FakeRunner)
    monkeypatch.setattr(cli, "_instrument_context", fake_instrument_context)

    exit_code = cli.main(["trajectory", "monitor", "run", "--iterations", "2"])

    assert exit_code == cli.EXIT_OK
    payload = _payload_from_stdout(capsys)
    assert payload["run_name"] == "run-abc"
    assert payload["iterations"] == 2
    assert FakeStore.instances[0].created_run_name == "run-abc"
    assert FakeRunner.instances[0].iterations == [2]

    loaded = load_staged_monitor_config(path=staged_path)
    assert loaded.run_name == ""
    assert loaded.db_name == "trajectory.sqlite3"


def test_monitor_list_signals_and_specs_structure(tmp_path: Path, capsys) -> None:
    parameters_file = tmp_path / "parameters.yaml"
    parameters_file.write_text(
        """
version: 1
parameters:
  signal_param:
    label: Signal Label
    unit: V
    type: float
    get_cmd:
      command: Signal_Get
      payload_index: 0
      args: {}
    set_cmd: false
    vals:
      kind: numbers
  spec_param:
    label: Spec Label
    unit: A
    type: float
    get_cmd:
      command: Spec_Get
      payload_index: 0
      args: {}
    set_cmd: false
    vals:
      kind: numbers
      min: 0.0
      max: 10.0
""".strip()
        + "\n",
        encoding="utf-8",
    )

    exit_signals = cli.main(
        [
            "trajectory",
            "monitor",
            "list-signals",
            "--parameters-file",
            str(parameters_file),
        ]
    )
    assert exit_signals == cli.EXIT_OK
    signals_payload = _payload_from_stdout(capsys)
    assert signals_payload["count"] == 2
    assert signals_payload["signals"][0]["label"] == "Signal Label"
    assert signals_payload["signals"][0]["name"] == "signal_param"

    exit_specs = cli.main(
        [
            "trajectory",
            "monitor",
            "list-specs",
            "--parameters-file",
            str(parameters_file),
        ]
    )
    assert exit_specs == cli.EXIT_OK
    specs_payload = _payload_from_stdout(capsys)
    assert specs_payload["count"] == 2
    assert specs_payload["specs"][1]["label"] == "Spec Label"
    assert "vals" not in specs_payload["specs"][1]
    assert specs_payload["specs"][1]["get_cmd"]["command"] == "Spec_Get"


def test_monitor_list_specs_includes_default_extra_i_gain(capsys) -> None:
    exit_specs = cli.main(["trajectory", "monitor", "list-specs"])
    assert exit_specs == cli.EXIT_OK
    specs_payload = _payload_from_stdout(capsys)
    labels = {item["label"] for item in specs_payload["specs"]}
    assert "Z Controller I Gain" in labels
    assert "Z Setpoint" in labels


def test_monitor_run_failed_startup_does_not_create_run_row(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    staged_path = tmp_path / "monitor-config.json"
    db_path = tmp_path / "trajectory.sqlite3"
    save_staged_monitor_config(
        replace(
            default_monitor_config(run_name="run-startup-fail"),
            db_directory=str(tmp_path),
            db_name=db_path.name,
            signal_labels=("Missing Signal",),
            spec_labels=("Missing Spec",),
        ),
        path=staged_path,
    )
    monkeypatch.setattr(cli, "default_staged_config_path", lambda: staged_path)

    class FakeInstrument:
        def parameter_specs(self) -> tuple[SimpleNamespace, ...]:
            return (
                SimpleNamespace(name="present", label="Present Label", readable=True, vals=None),
            )

    @contextmanager
    def fake_instrument_context(*_args, **_kwargs):
        yield FakeInstrument(), None

    monkeypatch.setattr(cli, "_instrument_context", fake_instrument_context)

    exit_code = cli.main(["trajectory", "monitor", "run", "--iterations", "1"])

    assert exit_code == cli.EXIT_INVALID_INPUT
    payload = _payload_from_stdout(capsys)
    assert payload["error"]["type"] == "ValueError"
    assert "Unknown monitor signals label" in payload["error"]["message"]

    with sqlite3.connect(db_path) as connection:
        runs_count = connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    assert runs_count == 0


def test_monitor_run_keyboard_interrupt_reports_completed_iterations(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    staged_path = tmp_path / "monitor-config.json"
    save_staged_monitor_config(
        replace(
            default_monitor_config(run_name="run-interrupted"),
            db_directory=str(tmp_path),
            db_name="trajectory.sqlite3",
            signal_labels=("Signal A",),
            spec_labels=("Spec A",),
        ),
        path=staged_path,
    )
    monkeypatch.setattr(cli, "default_staged_config_path", lambda: staged_path)

    class FakeStore:
        def __init__(self, db_path: Path | str) -> None:
            self.db_path = Path(db_path)

        def initialize_schema(self) -> None:
            return None

        def create_run(self, *, run_name: str, started_at_utc: str) -> int:
            return 7

        def close(self) -> None:
            return None

    class FakeRunner:
        def __init__(self, **_kwargs) -> None:
            self.sample_idx = 0

        def run_iterations(self, count: int) -> int:
            assert count == 5
            self.sample_idx = 2
            raise KeyboardInterrupt

    class FakeInstrument:
        def parameter_specs(self) -> tuple[SimpleNamespace, ...]:
            return (
                SimpleNamespace(name="signal_a", label="Signal A", readable=True, vals=None),
                SimpleNamespace(name="spec_a", label="Spec A", readable=True, vals=None),
            )

        def get_parameter_value(self, _name: str) -> float:
            return 1.0

    @contextmanager
    def fake_instrument_context(*_args, **_kwargs):
        yield FakeInstrument(), None

    monkeypatch.setattr(cli, "TrajectorySQLiteStore", FakeStore)
    monkeypatch.setattr(cli, "TrajectoryMonitorRunner", FakeRunner)
    monkeypatch.setattr(cli, "_instrument_context", fake_instrument_context)

    exit_code = cli.main(["trajectory", "monitor", "run", "--iterations", "5"])

    assert exit_code == cli.EXIT_OK
    payload = _payload_from_stdout(capsys)
    assert payload["interrupted"] is True
    assert payload["iterations"] == 2


def test_monitor_run_without_iterations_emits_start_hint(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    staged_path = tmp_path / "monitor-config.json"
    save_staged_monitor_config(
        replace(
            default_monitor_config(run_name="run-live"),
            db_directory=str(tmp_path),
            db_name="trajectory.sqlite3",
            signal_labels=("Signal A",),
            spec_labels=("Spec A",),
        ),
        path=staged_path,
    )
    monkeypatch.setattr(cli, "default_staged_config_path", lambda: staged_path)

    class FakeStore:
        def __init__(self, db_path: Path | str) -> None:
            self.db_path = Path(db_path)

        def initialize_schema(self) -> None:
            return None

        def create_run(self, *, run_name: str, started_at_utc: str) -> int:
            return 11

        def close(self) -> None:
            return None

    class FakeRunner:
        def __init__(self, **_kwargs) -> None:
            self.sample_idx = 0

        def run_iterations(self, count: int) -> int:
            assert count == 1
            self.sample_idx = 1
            raise KeyboardInterrupt

    class FakeInstrument:
        def parameter_specs(self) -> tuple[SimpleNamespace, ...]:
            return (
                SimpleNamespace(name="signal_a", label="Signal A", readable=True, vals=None),
                SimpleNamespace(name="spec_a", label="Spec A", readable=True, vals=None),
            )

        def get_parameter_value(self, _name: str) -> float:
            return 1.0

    @contextmanager
    def fake_instrument_context(*_args, **_kwargs):
        yield FakeInstrument(), None

    monkeypatch.setattr(cli, "TrajectorySQLiteStore", FakeStore)
    monkeypatch.setattr(cli, "TrajectoryMonitorRunner", FakeRunner)
    monkeypatch.setattr(cli, "_instrument_context", fake_instrument_context)

    exit_code = cli.main(["trajectory", "monitor", "run"])

    assert exit_code == cli.EXIT_OK
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["interrupted"] is True
    assert payload["iterations"] == 1
    assert "Press Ctrl+C to stop" in captured.err
