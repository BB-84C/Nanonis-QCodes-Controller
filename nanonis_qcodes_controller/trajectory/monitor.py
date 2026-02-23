from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from .sqlite_store import TrajectorySQLiteStore


class TrajectoryMonitorRunner:
    def __init__(
        self,
        *,
        store: TrajectorySQLiteStore,
        run_id: int,
        run_start_utc: str,
        interval_s: float,
        rotate_entries: int,
        poll_signals: Callable[[], dict[str, object]],
        poll_specs: Callable[[], dict[str, object]],
        action_window_s: float = 2.5,
        monotonic_time_s: Callable[[], float] | None = None,
        sleep_s: Callable[[float], None] | None = None,
    ) -> None:
        if interval_s <= 0:
            raise ValueError("interval_s must be positive.")
        if rotate_entries < 1:
            raise ValueError("rotate_entries must be at least 1.")
        if action_window_s < 0:
            raise ValueError("action_window_s must be non-negative.")

        self._store = store
        self._run_id = run_id
        self._run_start_utc = run_start_utc
        self._interval_s = interval_s
        self._rotate_entries = rotate_entries
        self._poll_signals = poll_signals
        self._poll_specs = poll_specs
        self._action_window_s = action_window_s
        self._monotonic_time_s = monotonic_time_s or time.monotonic
        self._sleep_s = sleep_s or time.sleep

        self._sample_idx = 0
        self._run_start_monotonic_s: float | None = None
        self._signal_catalog_ids: dict[int, int] = {}
        self._spec_catalog_ids: dict[int, int] = {}
        self._previous_specs_by_label: dict[str, object] = {}
        self._has_previous_specs_snapshot = False

    @property
    def sample_idx(self) -> int:
        return self._sample_idx

    def run_iterations(self, count: int) -> int:
        if count < 0:
            raise ValueError("count must be non-negative.")

        completed = 0
        for _ in range(count):
            sample_idx = self._sample_idx
            self._wait_until_scheduled_sample_time(sample_idx)
            segment_id = sample_idx // self._rotate_entries
            signal_id = self._signal_catalog_id_for_segment(segment_id)
            spec_id = self._spec_catalog_id_for_segment(segment_id)
            dt_s = self._elapsed_seconds()
            signal_values = self._poll_signals()
            spec_values = self._poll_specs()

            self._store.insert_sample_pair(
                run_id=self._run_id,
                signal_id=signal_id,
                spec_id=spec_id,
                dt_s=dt_s,
                signal_values_json=signal_values,
                spec_vals_json=spec_values,
            )
            self._record_spec_change_events(dt_s=dt_s, spec_values=spec_values)
            self._sample_idx = sample_idx + 1
            completed += 1

        return completed

    def _record_spec_change_events(self, *, dt_s: float, spec_values: dict[str, object]) -> None:
        current_specs_by_label = dict(spec_values)
        if not self._has_previous_specs_snapshot:
            self._previous_specs_by_label = current_specs_by_label
            self._has_previous_specs_snapshot = True
            return

        previous_specs_by_label = self._previous_specs_by_label
        changed_labels = sorted(set(previous_specs_by_label) | set(current_specs_by_label))
        detected_at_utc = _iso_utc_now()
        signal_window_start_dt_s = dt_s - self._action_window_s
        signal_window_end_dt_s = dt_s + self._action_window_s

        for spec_label in changed_labels:
            old_value = previous_specs_by_label.get(spec_label)
            new_value = current_specs_by_label.get(spec_label)
            if old_value == new_value:
                continue
            delta_value = _compute_delta_value(old_value=old_value, new_value=new_value)

            self._store.insert_action_event(
                run_id=self._run_id,
                dt_s=dt_s,
                action_kind="spec-change",
                detected_at_utc=detected_at_utc,
                spec_label=spec_label,
                signal_window_start_dt_s=signal_window_start_dt_s,
                signal_window_end_dt_s=signal_window_end_dt_s,
                delta_value=delta_value,
                old_value_json=old_value,
                new_value_json=new_value,
            )

        self._previous_specs_by_label = current_specs_by_label

    def _wait_until_scheduled_sample_time(self, sample_idx: int) -> None:
        if self._run_start_monotonic_s is None:
            self._run_start_monotonic_s = self._monotonic_time_s()
        scheduled_time_s = self._run_start_monotonic_s + (sample_idx * self._interval_s)
        sleep_duration_s = scheduled_time_s - self._monotonic_time_s()
        if sleep_duration_s > 0.0:
            self._sleep_s(sleep_duration_s)

    def _elapsed_seconds(self) -> float:
        now_s = self._monotonic_time_s()
        if self._run_start_monotonic_s is None:
            self._run_start_monotonic_s = now_s
        elapsed_s = now_s - self._run_start_monotonic_s
        return max(0.0, elapsed_s)

    def _signal_catalog_id_for_segment(self, segment_id: int) -> int:
        catalog_id = self._signal_catalog_ids.get(segment_id)
        if catalog_id is not None:
            return catalog_id
        created_id = self._store.insert_signal_catalog(
            run_id=self._run_id,
            signal_label=f"segment-{segment_id}",
            metadata_json=self._segment_metadata(segment_id),
        )
        self._signal_catalog_ids[segment_id] = created_id
        return created_id

    def _spec_catalog_id_for_segment(self, segment_id: int) -> int:
        catalog_id = self._spec_catalog_ids.get(segment_id)
        if catalog_id is not None:
            return catalog_id
        created_id = self._store.insert_spec_catalog(
            run_id=self._run_id,
            spec_label=f"segment-{segment_id}",
            metadata_json=self._segment_metadata(segment_id),
        )
        self._spec_catalog_ids[segment_id] = created_id
        return created_id

    def _segment_metadata(self, segment_id: int) -> dict[str, Any]:
        return {
            "segment_id": segment_id,
            "run_start_utc": self._run_start_utc,
            "interval_s": self._interval_s,
        }


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _compute_delta_value(*, old_value: object, new_value: object) -> float | None:
    if isinstance(old_value, bool) or isinstance(new_value, bool):
        return None
    if isinstance(old_value, (int, float)) and isinstance(new_value, (int, float)):
        return float(new_value) - float(old_value)
    return None
