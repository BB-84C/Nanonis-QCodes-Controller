"""
Ultra-thin entry point for ``nspmctl`` and ``python -m nspmctl``.

The job of this module is to keep the WARM client path as light as possible:
when an nspmctl daemon is reachable, it forwards the argv to the daemon and
prints the response without ever importing the heavy ``nspmctl.cli`` /
``nspmctl.client`` / ``nspmctl.controller`` chain (which transitively pulls
in PyYAML + a 651KB manifest parse + the nanonis-spm-aware client surface).

If the daemon is unreachable, we fall through to ``nspmctl.cli.main`` (the
slow inline path), so behavior is identical to ``--no-daemon`` mode.
"""

from __future__ import annotations

import os
import sys

# Subcommands that should NEVER go through the daemon. ``daemon`` meta
# commands manage the daemon itself; ``--help`` should print top-level help
# from the inline parser. Everything else is forwarded.
_DAEMON_SKIP_FIRST = frozenset({"-h", "--help", "-help", "daemon"})
_NO_DAEMON_FLAG = "--no-daemon"
_NO_DAEMON_ENV = "NSPMCTL_NO_DAEMON"


def _strip_no_daemon(argv: list[str]) -> tuple[list[str], bool]:
    if _NO_DAEMON_FLAG not in argv:
        return argv, False
    return [a for a in argv if a != _NO_DAEMON_FLAG], True


def _try_route_through_daemon(raw_argv: list[str]) -> int | None:
    """Forward to the daemon. Returns its exit code, or None on miss."""
    try:
        from nspmctl import daemon as _daemon  # stdlib-only deps
    except ImportError:
        return None

    status = _daemon.daemon_status()
    if not status.get("running") or not status.get("reachable"):
        return None

    try:
        response = _daemon.send_request_to_daemon(list(raw_argv))
    except (ConnectionRefusedError, ConnectionResetError, OSError, ValueError):
        return None

    stdout_text = str(response.get("stdout", ""))
    stderr_text = str(response.get("stderr", ""))
    if stdout_text:
        sys.stdout.write(stdout_text)
    if stderr_text:
        sys.stderr.write(stderr_text)
    rc = response.get("rc", 0)
    try:
        return int(rc)
    except (TypeError, ValueError):
        return 1


def _trigger_background_daemon_spawn() -> None:
    """Best-effort: launch a daemon so the NEXT call is warm."""
    try:
        from nspmctl import daemon as _daemon  # noqa: PLC0415

        _daemon.spawn_daemon_background()
    except Exception:  # noqa: BLE001 - never block the user's call
        pass


def _run_inline(raw_argv: list[str]) -> int:
    # Heavy import path: nspmctl.cli imports PyYAML, controller, client, etc.
    from nspmctl.cli import main as cli_main

    return int(cli_main(raw_argv))


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)

    use_daemon = True
    raw_argv, no_daemon_flag = _strip_no_daemon(raw_argv)
    if no_daemon_flag or os.environ.get(_NO_DAEMON_ENV):
        use_daemon = False

    first = raw_argv[0] if raw_argv else None
    if first in _DAEMON_SKIP_FIRST:
        use_daemon = False

    if use_daemon and raw_argv:
        result = _try_route_through_daemon(raw_argv)
        if result is not None:
            return result
        # Daemon was missing or unreachable: run inline now, but trigger a
        # background spawn so the next agent tool call is warm.
        _trigger_background_daemon_spawn()

    return _run_inline(raw_argv)


if __name__ == "__main__":
    raise SystemExit(main())
