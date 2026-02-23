from __future__ import annotations

import json
from pathlib import Path

from nanonis_qcodes_controller import cli
from nanonis_qcodes_controller.trajectory.sqlite_store import TrajectorySQLiteStore


def _payload_from_stdout(capsys) -> dict[str, object]:
    captured = capsys.readouterr()
    assert captured.err == ""
    return json.loads(captured.out)


def test_trajectory_action_list_returns_inserted_actions(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "trajectory.sqlite3"
    store = TrajectorySQLiteStore(db_path)
    try:
        store.initialize_schema()
        run_alpha = store.create_run(run_name="run-alpha", started_at_utc="2026-02-22T00:00:00Z")
        run_beta = store.create_run(run_name="run-beta", started_at_utc="2026-02-22T00:10:00Z")

        store.insert_action_event(
            run_id=run_alpha,
            dt_s=0.1,
            action_kind="set",
            detected_at_utc="2026-02-22T00:00:01Z",
            spec_label="Bias",
            signal_window_start_dt_s=-0.4,
            signal_window_end_dt_s=0.6,
            old_value_json={"value": 0.1},
            new_value_json={"value": 0.2},
        )
        store.insert_action_event(
            run_id=run_alpha,
            dt_s=0.3,
            action_kind="set",
            detected_at_utc="2026-02-22T00:00:02Z",
            spec_label="Bias",
            signal_window_start_dt_s=-0.2,
            signal_window_end_dt_s=0.8,
            old_value_json={"value": 0.2},
            new_value_json={"value": 0.3},
        )
        store.insert_action_event(
            run_id=run_beta,
            dt_s=0.2,
            action_kind="set",
            detected_at_utc="2026-02-22T00:10:01Z",
            spec_label="Bias",
            signal_window_start_dt_s=-0.3,
            signal_window_end_dt_s=0.7,
            old_value_json={"value": 0.9},
            new_value_json={"value": 1.0},
        )
    finally:
        store.close()

    exit_code = cli.main(
        [
            "trajectory",
            "action",
            "list",
            "--db-path",
            str(db_path),
            "--run-name",
            "run-alpha",
        ]
    )

    assert exit_code == cli.EXIT_OK
    payload = _payload_from_stdout(capsys)
    assert payload["count"] == 2
    assert payload["run_name"] == "run-alpha"
    assert [row["action_idx"] for row in payload["actions"]] == [0, 1]
    assert [row["dt_s"] for row in payload["actions"]] == [0.1, 0.3]


def test_trajectory_action_show_returns_one_action(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "trajectory.sqlite3"
    store = TrajectorySQLiteStore(db_path)
    try:
        store.initialize_schema()
        run_id = store.create_run(run_name="run-alpha", started_at_utc="2026-02-22T00:00:00Z")
        store.insert_action_event(
            run_id=run_id,
            dt_s=0.1,
            action_kind="set",
            detected_at_utc="2026-02-22T00:00:01Z",
            spec_label="Bias",
            signal_window_start_dt_s=-0.4,
            signal_window_end_dt_s=0.6,
            old_value_json={"value": 0.1},
            new_value_json={"value": 0.2},
        )
        store.insert_action_event(
            run_id=run_id,
            dt_s=0.4,
            action_kind="set",
            detected_at_utc="2026-02-22T00:00:02Z",
            spec_label="Bias",
            signal_window_start_dt_s=-0.1,
            signal_window_end_dt_s=0.9,
            old_value_json={"value": 0.2},
            new_value_json={"value": 0.3},
        )
    finally:
        store.close()

    exit_code = cli.main(
        [
            "trajectory",
            "action",
            "show",
            "--db-path",
            str(db_path),
            "--run-name",
            "run-alpha",
            "--action-idx",
            "1",
        ]
    )

    assert exit_code == cli.EXIT_OK
    payload = _payload_from_stdout(capsys)
    assert payload["run_name"] == "run-alpha"
    assert payload["action"]["action_idx"] == 1
    assert payload["action"]["dt_s"] == 0.4
    assert payload["action"]["spec_label"] == "Bias"
    assert payload["action"]["new_value_json"] == {"value": 0.3}
    assert "signal_window" not in payload


def test_trajectory_action_show_with_signal_window_returns_context_rows(
    tmp_path: Path, capsys
) -> None:
    db_path = tmp_path / "trajectory.sqlite3"
    store = TrajectorySQLiteStore(db_path)
    try:
        store.initialize_schema()
        run_id = store.create_run(run_name="run-alpha", started_at_utc="2026-02-22T00:00:00Z")
        signal_z = store.insert_signal_catalog(run_id=run_id, signal_label="Z", unit="m")
        signal_i = store.insert_signal_catalog(run_id=run_id, signal_label="Current", unit="A")

        store.insert_signal_sample(run_id=run_id, signal_id=signal_z, dt_s=0.4, values_json=[10.0])
        store.insert_signal_sample(run_id=run_id, signal_id=signal_z, dt_s=0.6, values_json=[11.0])
        store.insert_signal_sample(run_id=run_id, signal_id=signal_i, dt_s=0.7, values_json=[5.0])
        store.insert_signal_sample(run_id=run_id, signal_id=signal_i, dt_s=0.9, values_json=[6.0])

        store.insert_action_event(
            run_id=run_id,
            dt_s=0.7,
            action_kind="set",
            detected_at_utc="2026-02-22T00:00:02Z",
            spec_label="Bias",
            signal_window_start_dt_s=0.5,
            signal_window_end_dt_s=0.8,
            old_value_json={"value": 0.2},
            new_value_json={"value": 0.3},
        )
    finally:
        store.close()

    exit_code = cli.main(
        [
            "trajectory",
            "action",
            "show",
            "--db-path",
            str(db_path),
            "--run-name",
            "run-alpha",
            "--action-idx",
            "0",
            "--with-signal-window",
        ]
    )

    assert exit_code == cli.EXIT_OK
    payload = _payload_from_stdout(capsys)
    assert payload["action"]["action_idx"] == 0
    assert payload["signal_window"]["count"] == 2
    assert [row["dt_s"] for row in payload["signal_window"]["rows"]] == [0.6, 0.7]
    assert [row["signal_label"] for row in payload["signal_window"]["rows"]] == ["Z", "Current"]
    assert [row["values_json"] for row in payload["signal_window"]["rows"]] == [[11.0], [5.0]]


def test_trajectory_action_list_default_scope_roundtrips_with_show(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "trajectory.sqlite3"
    store = TrajectorySQLiteStore(db_path)
    try:
        store.initialize_schema()
        run_alpha = store.create_run(run_name="run-alpha", started_at_utc="2026-02-22T00:00:00Z")
        run_beta = store.create_run(run_name="run-beta", started_at_utc="2026-02-22T00:10:00Z")

        store.insert_action_event(
            run_id=run_alpha,
            dt_s=0.1,
            action_kind="set",
            detected_at_utc="2026-02-22T00:00:01Z",
            spec_label="Bias",
            signal_window_start_dt_s=-0.4,
            signal_window_end_dt_s=0.6,
            old_value_json={"value": 0.1},
            new_value_json={"value": 0.2},
        )
        store.insert_action_event(
            run_id=run_beta,
            dt_s=0.2,
            action_kind="set",
            detected_at_utc="2026-02-22T00:10:01Z",
            spec_label="Bias",
            signal_window_start_dt_s=-0.3,
            signal_window_end_dt_s=0.7,
            old_value_json={"value": 0.9},
            new_value_json={"value": 1.0},
        )
    finally:
        store.close()

    list_exit_code = cli.main(["trajectory", "action", "list", "--db-path", str(db_path)])
    assert list_exit_code == cli.EXIT_OK
    list_payload = _payload_from_stdout(capsys)
    assert list_payload["count"] == 1
    assert [row["action_idx"] for row in list_payload["actions"]] == [0]

    listed_action = list_payload["actions"][0]
    show_exit_code = cli.main(
        [
            "trajectory",
            "action",
            "show",
            "--db-path",
            str(db_path),
            "--action-idx",
            str(listed_action["action_idx"]),
        ]
    )
    assert show_exit_code == cli.EXIT_OK
    show_payload = _payload_from_stdout(capsys)
    assert show_payload["action"]["id"] == listed_action["id"]
    assert show_payload["action"]["run_id"] == listed_action["run_id"]


def test_trajectory_action_query_missing_db_path_returns_invalid_input(
    tmp_path: Path, capsys
) -> None:
    db_path = tmp_path / "missing.sqlite3"

    list_exit_code = cli.main(["trajectory", "action", "list", "--db-path", str(db_path)])
    assert list_exit_code == cli.EXIT_INVALID_INPUT
    list_payload = _payload_from_stdout(capsys)
    assert list_payload["error"]["type"] == "ValueError"
    assert "db" in list_payload["error"]["message"].lower()
    assert db_path.exists() is False

    show_exit_code = cli.main(
        [
            "trajectory",
            "action",
            "show",
            "--db-path",
            str(db_path),
            "--action-idx",
            "0",
        ]
    )
    assert show_exit_code == cli.EXIT_INVALID_INPUT
    show_payload = _payload_from_stdout(capsys)
    assert show_payload["error"]["type"] == "ValueError"
    assert "db" in show_payload["error"]["message"].lower()
    assert db_path.exists() is False


def test_trajectory_action_query_missing_schema_returns_invalid_input(
    tmp_path: Path, capsys
) -> None:
    db_path = tmp_path / "empty.sqlite3"
    db_path.touch()

    list_exit_code = cli.main(["trajectory", "action", "list", "--db-path", str(db_path)])
    assert list_exit_code == cli.EXIT_INVALID_INPUT
    list_payload = _payload_from_stdout(capsys)
    assert list_payload["error"]["type"] == "ValueError"
    assert "schema" in list_payload["error"]["message"].lower()

    show_exit_code = cli.main(
        [
            "trajectory",
            "action",
            "show",
            "--db-path",
            str(db_path),
            "--action-idx",
            "0",
        ]
    )
    assert show_exit_code == cli.EXIT_INVALID_INPUT
    show_payload = _payload_from_stdout(capsys)
    assert show_payload["error"]["type"] == "ValueError"
    assert "schema" in show_payload["error"]["message"].lower()
