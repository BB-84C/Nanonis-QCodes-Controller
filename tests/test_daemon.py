"""Smoke tests for the nspmctl daemon module + thin __main__ entry."""
from __future__ import annotations

import subprocess
import sys

import pytest


def _check_no_heavy_imports(module_under_test: str) -> list[str]:
    """Subprocess probe: import the module and report which heavy deps leaked in."""
    probe = (
        "import sys, importlib;"
        f"importlib.import_module({module_under_test!r});"
        "forbidden = ['yaml','nspmctl.cli','nspmctl.client',"
        "'nspmctl.config','nspmctl.controller','nspmctl.safety'];"
        "leaked = sorted(n for n in forbidden if n in sys.modules);"
        "print(';;'.join(leaked))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    leaked_text = result.stdout.strip()
    return leaked_text.split(";;") if leaked_text else []


def test_daemon_module_imports_only_stdlib_and_version() -> None:
    """The daemon module must stay cheap: no PyYAML, controller, or client imports.

    The whole point of the fast __main__ path is that connecting to a running
    daemon does NOT load the heavy nspmctl.cli/controller/client surface. If
    daemon.py ever starts depending on those at import time, the warm CLI path
    silently slows down. Catch the regression here.
    """
    leaked = _check_no_heavy_imports("nspmctl.daemon")
    assert leaked == [], f"nspmctl.daemon must not load heavy deps; leaked: {leaked}"


def test_main_entry_module_imports_only_stdlib() -> None:
    """The fast entry must not pull in cli/yaml/controller at import time."""
    leaked = _check_no_heavy_imports("nspmctl.__main__")
    assert leaked == [], f"nspmctl.__main__ must not load heavy deps; leaked: {leaked}"


def test_daemon_status_reports_not_running_with_no_pid_file(tmp_path, monkeypatch) -> None:
    """daemon_status() must gracefully report 'no_pid_file' when nothing is up."""
    from nspmctl import daemon

    monkeypatch.setattr(daemon, "state_dir", lambda: tmp_path / "nspmctl-state")
    status = daemon.daemon_status()
    assert status == {"running": False, "reason": "no_pid_file"}


def test_send_request_to_daemon_raises_when_no_pid_file(tmp_path, monkeypatch) -> None:
    from nspmctl import daemon

    monkeypatch.setattr(daemon, "state_dir", lambda: tmp_path / "nspmctl-state")
    with pytest.raises(ConnectionRefusedError):
        daemon.send_request_to_daemon(["get", "bias_v"])


def test_fast_entry_strips_no_daemon_flag() -> None:
    from nspmctl.__main__ import _strip_no_daemon

    cleaned, flag = _strip_no_daemon(["--no-daemon", "get", "bias_v"])
    assert cleaned == ["get", "bias_v"]
    assert flag is True

    cleaned, flag = _strip_no_daemon(["get", "bias_v"])
    assert cleaned == ["get", "bias_v"]
    assert flag is False
