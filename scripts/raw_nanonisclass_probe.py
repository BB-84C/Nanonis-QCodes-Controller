from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from collections.abc import Callable
from typing import Any

from nanonis_spm.NanonisClass import Nanonis


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Raw NanonisClass command probe (no nqctl wrapper)."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Nanonis host.")
    parser.add_argument("--port", type=int, default=3364, help="Nanonis TCP port.")
    parser.add_argument("--timeout-s", type=float, default=5.0, help="Socket timeout seconds.")
    parser.add_argument(
        "--methods",
        nargs="+",
        default=["Util_VersionGet", "Bias_Get", "Current_Get", "ZCtrl_ZPosGet"],
        help="Method names to call in order.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON payload.")
    return parser.parse_args()


def _trim(value: object, *, max_len: int = 220) -> str:
    text = repr(value)
    if len(text) <= max_len:
        return text
    return f"{text[: max_len - 3]}..."


def _call_method(client: Nanonis, method_name: str) -> dict[str, Any]:
    started = time.perf_counter()
    candidate = getattr(client, method_name, None)
    if not callable(candidate):
        return {
            "method": method_name,
            "ok": False,
            "error_type": "AttributeError",
            "error": f"Method not found on NanonisClass: {method_name}",
            "duration_ms": (time.perf_counter() - started) * 1000.0,
            "result": None,
        }

    method = candidate
    assert callable(method)
    fn = method
    try:
        result = fn()
        return {
            "method": method_name,
            "ok": True,
            "error_type": None,
            "error": None,
            "duration_ms": (time.perf_counter() - started) * 1000.0,
            "result": _trim(result),
        }
    except Exception as exc:
        return {
            "method": method_name,
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "duration_ms": (time.perf_counter() - started) * 1000.0,
            "result": None,
        }


def main() -> int:
    args = _parse_args()

    payload: dict[str, Any] = {
        "host": args.host,
        "port": int(args.port),
        "timeout_s": float(args.timeout_s),
        "module": "nanonis_spm.NanonisClass",
        "class": "Nanonis",
        "results": [],
    }

    sock: socket.socket | None = None
    try:
        sock = socket.create_connection((args.host, int(args.port)), timeout=float(args.timeout_s))
        sock.settimeout(float(args.timeout_s))

        client = Nanonis(sock)
        method_results = [_call_method(client, name) for name in args.methods]
        payload["results"] = method_results
        payload["ok"] = any(item["ok"] for item in method_results)

    except Exception as exc:
        payload["ok"] = False
        payload["error_type"] = type(exc).__name__
        payload["error"] = str(exc)
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Target: {payload['host']}:{payload['port']} timeout={payload['timeout_s']}s")
        print("Class : nanonis_spm.NanonisClass.Nanonis")
        for item in payload.get("results", []):
            status = "OK" if item["ok"] else "FAIL"
            print(
                f"- {item['method']}: {status} ({item['duration_ms']:.2f} ms)"
                + (
                    f" -> {item['result']}"
                    if item["ok"]
                    else f" -> {item['error_type']}: {item['error']}"
                )
            )
        if payload.get("error"):
            print(f"Transport failure: {payload.get('error_type')}: {payload.get('error')}")

    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
