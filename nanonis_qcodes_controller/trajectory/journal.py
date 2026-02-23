from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TrajectoryStats:
    submitted: int
    written: int
    dropped: int
    last_error: str | None
    active_file: str | None
    segment_index: int


class TrajectoryJournal:
    def __init__(
        self,
        *,
        directory: str | Path,
        queue_size: int = 2048,
        max_events_per_file: int = 5000,
        writer_delay_s: float = 0.0,
    ) -> None:
        if queue_size <= 0:
            raise ValueError("queue_size must be positive.")
        if max_events_per_file <= 0:
            raise ValueError("max_events_per_file must be positive.")
        if writer_delay_s < 0:
            raise ValueError("writer_delay_s must be non-negative.")

        self._directory = Path(directory)
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=queue_size)
        self._max_events_per_file = max_events_per_file
        self._writer_delay_s = writer_delay_s

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._run_id = str(int(time.time() * 1000))

        self._submitted = 0
        self._written = 0
        self._dropped = 0
        self._last_error: str | None = None

        self._segment_index = 0
        self._segment_count = 0
        self._active_file_path: Path | None = None
        self._active_file_handle: Any = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return

            self._directory.mkdir(parents=True, exist_ok=True)
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="trajectory-journal-writer",
                daemon=True,
            )
            self._thread.start()

        _ = self.emit(
            "journal_started",
            {
                "directory": str(self._directory),
                "queue_size": self._queue.maxsize,
                "max_events_per_file": self._max_events_per_file,
            },
        )

    def close(self) -> None:
        _ = self.emit("journal_stopping", {})
        self._stop_event.set()

        thread = self._thread
        if thread is not None:
            thread.join(timeout=10.0)

        with self._lock:
            self._close_active_file()
            self._thread = None

    def emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        timestamp_utc: str | None = None,
    ) -> bool:
        event = {
            "event_id": uuid.uuid4().hex,
            "timestamp_utc": _now_utc_iso() if timestamp_utc is None else str(timestamp_utc),
            "event_type": event_type,
            "payload": payload,
        }

        with self._lock:
            self._submitted += 1

        try:
            self._queue.put_nowait(event)
            return True
        except queue.Full:
            with self._lock:
                self._dropped += 1
            return False

    def stats(self) -> TrajectoryStats:
        with self._lock:
            return TrajectoryStats(
                submitted=self._submitted,
                written=self._written,
                dropped=self._dropped,
                last_error=self._last_error,
                active_file=None if self._active_file_path is None else str(self._active_file_path),
                segment_index=self._segment_index,
            )

    def _run(self) -> None:
        while True:
            if self._stop_event.is_set() and self._queue.empty():
                break

            try:
                event = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            try:
                self._write_event(event)
            except (
                Exception
            ) as exc:  # pragma: no cover - filesystem failures are environment dependent
                with self._lock:
                    self._last_error = f"{type(exc).__name__}: {exc}"

            if self._writer_delay_s > 0:
                time.sleep(self._writer_delay_s)

    def _write_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            if self._active_file_handle is None or self._segment_count >= self._max_events_per_file:
                self._rotate_file()

            assert self._active_file_handle is not None
            line = json.dumps(event, ensure_ascii=True)
            self._active_file_handle.write(line)
            self._active_file_handle.write("\n")
            self._active_file_handle.flush()

            self._segment_count += 1
            self._written += 1

    def _rotate_file(self) -> None:
        self._close_active_file()
        self._segment_index += 1
        self._segment_count = 0
        file_name = f"trajectory-{self._run_id}-{self._segment_index:05d}.jsonl"
        self._active_file_path = self._directory / file_name
        self._active_file_handle = self._active_file_path.open("a", encoding="utf-8")

    def _close_active_file(self) -> None:
        if self._active_file_handle is not None:
            self._active_file_handle.close()
            self._active_file_handle = None


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
