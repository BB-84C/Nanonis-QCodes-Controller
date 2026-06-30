"""
nspmctl persistent daemon.

Holds one warm ``NanonisController`` instance (with the TCP socket to the
controller already open and the nanonis-spm Python package already
imported) and serves CLI requests over a localhost TCP loopback channel.

Protocol (line/length-delimited JSON):
    Request:  4-byte big-endian length prefix, then UTF-8 JSON:
              {"argv": ["get", "bias_v"], "cwd": "/path/to/dir"}
    Response: 4-byte big-endian length prefix, then UTF-8 JSON:
              {"stdout": "...", "stderr": "...", "rc": 0}

The daemon binds to ``127.0.0.1`` on an OS-picked free port, writes a PID
file at ``<state_dir>/daemon.json`` with the chosen port + pid + start
time, then accepts a single in-flight connection at a time (writes are
serialized to the Nanonis socket). After ``DAEMON_IDLE_TIMEOUT_S`` of
inactivity the daemon exits cleanly.

PID file layout::

    {
      "pid": 12345,
      "host": "127.0.0.1",
      "port": 50321,
      "started_at_utc": "2026-06-30T10:00:00Z",
      "version": "0.2.0",
      "parameters_file": "config/parameters.yaml"
    }
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nspmctl.version import __version__

# --- Constants ---------------------------------------------------------------

DAEMON_IDLE_TIMEOUT_S = 30 * 60  # 30 minutes per user spec
DAEMON_LOOPBACK_HOST = "127.0.0.1"
DAEMON_ACCEPT_BACKLOG = 8
DAEMON_RECV_CHUNK = 65536
DAEMON_MAX_PAYLOAD_BYTES = 16 * 1024 * 1024  # 16 MiB safety cap
DAEMON_SPAWN_READY_TIMEOUT_S = 8.0
DAEMON_SPAWN_POLL_INTERVAL_S = 0.05
DAEMON_CONNECT_TIMEOUT_S = 30.0
DAEMON_PROTOCOL_VERSION = 1

# --- State directory + PID file ---------------------------------------------


def state_dir() -> Path:
    """Return the per-user state directory. Created lazily by callers."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return Path(base) / "nspmctl"
    # Linux / macOS XDG default
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "nspmctl"


def pid_file_path() -> Path:
    return state_dir() / "daemon.json"


def log_file_path() -> Path:
    return state_dir() / "daemon.log"


def _ensure_state_dir() -> Path:
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


# --- PID file management -----------------------------------------------------


def _write_pid_file(info: dict[str, Any]) -> None:
    target = pid_file_path()
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, target)


def _read_pid_file() -> dict[str, Any] | None:
    p = pid_file_path()
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _remove_pid_file() -> None:
    with contextlib.suppress(OSError):
        pid_file_path().unlink()


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # OpenProcess via ctypes; cheap and avoids depending on psutil.
        import ctypes  # noqa: PLC0415

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong(0)
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            if not ok:
                return False
            # 259 == STILL_ACTIVE on Windows.
            return exit_code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


def daemon_status() -> dict[str, Any]:
    """Inspect the PID file and report whether a daemon is alive."""
    info = _read_pid_file()
    if info is None:
        return {"running": False, "reason": "no_pid_file"}

    pid = int(info.get("pid", 0))
    if not _is_pid_alive(pid):
        return {
            "running": False,
            "reason": "stale_pid_file",
            "pid": pid,
            "pid_file": str(pid_file_path()),
        }

    # Best-effort probe to make sure the port still answers.
    host = str(info.get("host", DAEMON_LOOPBACK_HOST))
    port = int(info.get("port", 0))
    reachable = False
    if port > 0:
        try:
            with socket.create_connection((host, port), timeout=0.5) as s:
                s.close()
                reachable = True
        except OSError:
            reachable = False

    return {
        "running": True,
        "reachable": reachable,
        "pid": pid,
        "host": host,
        "port": port,
        "started_at_utc": info.get("started_at_utc"),
        "version": info.get("version"),
        "pid_file": str(pid_file_path()),
        "log_file": str(log_file_path()),
    }


# --- Client side: connect + send to daemon -----------------------------------


def _send_framed(sock: socket.socket, payload: bytes) -> None:
    sock.sendall(struct.pack(">I", len(payload)) + payload)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(DAEMON_RECV_CHUNK, n - len(buf)))
        if not chunk:
            raise ConnectionResetError("Daemon closed the connection mid-frame.")
        buf.extend(chunk)
    return bytes(buf)


def _recv_framed(sock: socket.socket) -> bytes:
    header = _recv_exact(sock, 4)
    (length,) = struct.unpack(">I", header)
    if length > DAEMON_MAX_PAYLOAD_BYTES:
        raise ValueError(f"Daemon response too large ({length} bytes).")
    return _recv_exact(sock, length)


def send_request_to_daemon(
    argv: list[str], *, cwd: str | None = None, timeout_s: float = DAEMON_CONNECT_TIMEOUT_S
) -> dict[str, Any]:
    """Forward an argv to the running daemon and return its decoded response."""
    info = _read_pid_file()
    if info is None:
        raise ConnectionRefusedError("Daemon is not running (no PID file).")

    host = str(info.get("host", DAEMON_LOOPBACK_HOST))
    port = int(info.get("port", 0))
    if port <= 0:
        raise ConnectionRefusedError("Daemon PID file does not contain a port.")

    payload = json.dumps(
        {
            "protocol": DAEMON_PROTOCOL_VERSION,
            "argv": list(argv),
            "cwd": cwd or os.getcwd(),
        },
        separators=(",", ":"),
    ).encode("utf-8")

    with socket.create_connection((host, port), timeout=timeout_s) as sock:
        sock.settimeout(timeout_s)
        _send_framed(sock, payload)
        raw = _recv_framed(sock)

    response = json.loads(raw.decode("utf-8"))
    if not isinstance(response, dict):
        raise ValueError("Malformed daemon response (not a JSON object).")
    return response


# --- Background spawn (used by CLI when no daemon is running) ----------------


def spawn_daemon_background(*, parameters_file: str | None = None) -> int:
    """Spawn a fully detached daemon process and return its PID.

    Does NOT wait for readiness; callers should poll ``daemon_status`` if they
    need readiness confirmation.
    """
    _ensure_state_dir()
    log_path = log_file_path()
    log_handle = open(log_path, "ab", buffering=0)

    cmd: list[str] = [sys.executable, "-m", "nspmctl.daemon", "--serve"]
    if parameters_file is not None:
        cmd += ["--parameters-file", parameters_file]

    creationflags = 0
    start_new_session = False
    if sys.platform == "win32":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        creationflags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    else:
        start_new_session = True

    proc = subprocess.Popen(  # noqa: S603 - intentional self-spawn
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=log_handle,
        close_fds=True,
        creationflags=creationflags,
        start_new_session=start_new_session,
    )
    return proc.pid


def wait_for_daemon_ready(timeout_s: float = DAEMON_SPAWN_READY_TIMEOUT_S) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = daemon_status()
        if status.get("running") and status.get("reachable"):
            return True
        time.sleep(DAEMON_SPAWN_POLL_INTERVAL_S)
    return False


# --- Server (daemon process) -------------------------------------------------


class _DaemonServer:
    def __init__(self, *, parameters_file: str | None) -> None:
        self._parameters_file = parameters_file
        self._lock = threading.Lock()  # serialise instrument access
        self._sock: socket.socket | None = None
        self._instrument: Any = None
        self._idle_event = threading.Event()
        self._stop = threading.Event()
        self._last_activity = time.monotonic()

    def _log(self, message: str) -> None:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        print(f"[{ts}] {message}", flush=True)

    def _build_instrument(self) -> Any:
        # Lazy imports inside the daemon process so the heavy ones happen here,
        # not in the client invocation.
        from nspmctl.controller import NanonisController  # noqa: PLC0415

        params = self._parameters_file
        kwargs: dict[str, Any] = {"name": "nspmctld", "auto_connect": True}
        if params:
            kwargs["parameters_file"] = params
        return NanonisController(**kwargs)

    def _ensure_instrument(self) -> Any:
        if self._instrument is None:
            self._log("instantiating NanonisController (warm)...")
            self._instrument = self._build_instrument()
            self._log("controller ready")
        return self._instrument

    def _bind(self) -> int:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((DAEMON_LOOPBACK_HOST, 0))
        sock.listen(DAEMON_ACCEPT_BACKLOG)
        sock.settimeout(1.0)
        self._sock = sock
        return int(sock.getsockname()[1])

    def _publish_pid_file(self, port: int) -> None:
        info = {
            "pid": os.getpid(),
            "host": DAEMON_LOOPBACK_HOST,
            "port": port,
            "started_at_utc": datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "version": __version__,
            "parameters_file": self._parameters_file,
        }
        _write_pid_file(info)

    def _handle_request(self, raw: bytes) -> bytes:
        try:
            request = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            return _encode_response("", f"Malformed request JSON: {exc}\n", 3)

        if not isinstance(request, dict):
            return _encode_response("", "Request must be a JSON object.\n", 3)

        argv = request.get("argv", [])
        if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
            return _encode_response("", "Request.argv must be a list of strings.\n", 3)

        cwd_request = request.get("cwd")
        original_cwd = os.getcwd()
        if cwd_request and isinstance(cwd_request, str):
            try:
                os.chdir(cwd_request)
            except OSError:
                pass  # tolerate; daemon CWD just stays the same

        from nspmctl import cli as _cli  # noqa: PLC0415

        with self._lock:
            self._ensure_instrument()
            _cli._DAEMON_SHARED_INSTRUMENT = self._instrument
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            try:
                with (
                    contextlib.redirect_stdout(stdout_buf),
                    contextlib.redirect_stderr(stderr_buf),
                ):
                    try:
                        rc = _cli.main(list(argv))
                    except SystemExit as exc:
                        rc = int(exc.code) if isinstance(exc.code, int) else 1
                    except BaseException as exc:  # noqa: BLE001 - daemon must keep running
                        rc = 1
                        print(
                            f"daemon: unhandled exception: {type(exc).__name__}: {exc}",
                            file=stderr_buf,
                        )
            finally:
                _cli._DAEMON_SHARED_INSTRUMENT = None
                if cwd_request:
                    with contextlib.suppress(OSError):
                        os.chdir(original_cwd)

        return _encode_response(stdout_buf.getvalue(), stderr_buf.getvalue(), int(rc))

    def serve(self) -> int:
        port = self._bind()
        # Eagerly warm the controller BEFORE we advertise as reachable, so the
        # first client request lands on a fully-warm instrument rather than
        # paying the import + connect cost (~600 ms) inline.
        try:
            self._ensure_instrument()
        except Exception as exc:  # noqa: BLE001 - daemon must surface the error and exit
            self._log(f"failed to instantiate controller: {type(exc).__name__}: {exc}")
            if self._sock is not None:
                with contextlib.suppress(OSError):
                    self._sock.close()
                self._sock = None
            return 1
        self._publish_pid_file(port)
        self._log(
            f"nspmctld listening on {DAEMON_LOOPBACK_HOST}:{port} (pid={os.getpid()},"
            f" idle_timeout={DAEMON_IDLE_TIMEOUT_S}s)"
        )

        try:
            while not self._stop.is_set():
                assert self._sock is not None
                try:
                    conn, _ = self._sock.accept()
                except TimeoutError:
                    if time.monotonic() - self._last_activity >= DAEMON_IDLE_TIMEOUT_S:
                        self._log("idle timeout reached; exiting")
                        break
                    continue
                except OSError as exc:
                    self._log(f"accept error: {exc}")
                    break

                self._last_activity = time.monotonic()
                try:
                    conn.settimeout(DAEMON_CONNECT_TIMEOUT_S)
                    header = _recv_exact(conn, 4)
                    (length,) = struct.unpack(">I", header)
                    if length > DAEMON_MAX_PAYLOAD_BYTES:
                        self._log(f"rejecting oversized request ({length} bytes)")
                        conn.close()
                        continue
                    raw = _recv_exact(conn, length)
                    response = self._handle_request(raw)
                    _send_framed(conn, response)
                except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                    pass
                except Exception as exc:  # noqa: BLE001 - keep daemon alive
                    self._log(f"request error: {type(exc).__name__}: {exc}")
                finally:
                    with contextlib.suppress(OSError):
                        conn.close()
        finally:
            self._cleanup()
        return 0

    def _cleanup(self) -> None:
        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._sock.close()
            self._sock = None
        if self._instrument is not None:
            with contextlib.suppress(Exception):
                self._instrument.close()
            self._instrument = None
        _remove_pid_file()
        self._log("daemon stopped")


def _encode_response(stdout: str, stderr: str, rc: int) -> bytes:
    return json.dumps(
        {"stdout": stdout, "stderr": stderr, "rc": int(rc)},
        ensure_ascii=False,
    ).encode("utf-8")


# --- Process entry point -----------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nspmctld", description="nspmctl persistent daemon.")
    parser.add_argument("--serve", action="store_true", help="Run the daemon loop in this process.")
    parser.add_argument(
        "--parameters-file",
        default=None,
        help="Override parameter manifest path used by the warm controller instance.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print daemon status and exit.",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop any running daemon and exit.",
    )
    args = parser.parse_args(argv)

    if args.status:
        print(json.dumps(daemon_status(), indent=2, sort_keys=True))
        return 0

    if args.stop:
        info = _read_pid_file()
        if info is None:
            print(json.dumps({"stopped": False, "reason": "no_pid_file"}, indent=2))
            return 0
        pid = int(info.get("pid", 0))
        if pid > 0 and _is_pid_alive(pid):
            _terminate_pid(pid)
        _remove_pid_file()
        print(json.dumps({"stopped": True, "pid": pid}, indent=2))
        return 0

    if args.serve:
        existing = daemon_status()
        if existing.get("running") and existing.get("reachable"):
            print(json.dumps({"error": "daemon_already_running", "status": existing}, indent=2))
            return 1
        # If the PID file is stale, clear it before binding.
        if existing.get("running") and not existing.get("reachable"):
            _remove_pid_file()
        elif existing.get("reason") == "stale_pid_file":
            _remove_pid_file()
        server = _DaemonServer(parameters_file=args.parameters_file)
        return server.serve()

    parser.print_help()
    return 0


def _terminate_pid(pid: int) -> None:
    if sys.platform == "win32":
        import ctypes  # noqa: PLC0415

        PROCESS_TERMINATE = 0x0001
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if handle:
            try:
                kernel32.TerminateProcess(handle, 0)
            finally:
                kernel32.CloseHandle(handle)
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(pid, 15)  # SIGTERM


if __name__ == "__main__":
    raise SystemExit(main())
