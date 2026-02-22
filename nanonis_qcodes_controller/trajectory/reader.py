from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def read_events(
    directory: str | Path,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    for file_path in _list_segment_files(directory):
        with file_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line_text = line.strip()
                if not line_text:
                    continue
                events.append(json.loads(line_text))

    if limit is None:
        return events
    if limit <= 0:
        return []
    return events[-limit:]


def follow_events(
    directory: str | Path,
    *,
    poll_interval_s: float = 1.0,
    start_at_end: bool = False,
) -> Iterator[dict[str, Any]]:
    if poll_interval_s <= 0:
        raise ValueError("poll_interval_s must be positive.")

    offsets: dict[Path, int] = {}

    if start_at_end:
        for file_path in _list_segment_files(directory):
            offsets[file_path] = file_path.stat().st_size

    while True:
        for file_path in _list_segment_files(directory):
            start_offset = offsets.get(file_path, 0)
            with file_path.open("r", encoding="utf-8") as handle:
                handle.seek(start_offset)
                for line in handle:
                    line_text = line.strip()
                    if not line_text:
                        continue
                    yield json.loads(line_text)
                offsets[file_path] = handle.tell()
        time.sleep(poll_interval_s)


def _list_segment_files(directory: str | Path) -> list[Path]:
    root = Path(directory)
    if not root.exists():
        return []
    return sorted(path for path in root.glob("trajectory-*.jsonl") if path.is_file())
