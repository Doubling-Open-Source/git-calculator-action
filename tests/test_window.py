from __future__ import annotations

from datetime import datetime, timezone

import pytest

from window import resolve_window


def test_trailing_window_excludes_the_in_progress_week_and_consecutive_runs_abut() -> (
    None
):
    earlier = resolve_window(
        now=datetime(2026, 7, 15, 6, 0, 0, tzinfo=timezone.utc), weeks=1
    )
    later = resolve_window(
        now=datetime(2026, 7, 22, 18, 30, 0, tzinfo=timezone.utc), weeks=1
    )
    assert earlier["window_end"] == "2026-07-13T00:00:00Z"
    assert later["window_end"] == "2026-07-20T00:00:00Z"
    assert earlier["window_end"] == later["window_start"]


def test_explicit_override_rounds_down_and_keeps_what_was_requested() -> None:
    meta = resolve_window(
        window_start="2026-01-01T00:00:00Z",
        window_end="2026-02-01T00:00:00Z",
    )
    assert meta["mode"] == "manual_override"
    assert meta["window_start"] == "2025-12-29T00:00:00Z"
    assert meta["window_end"] == "2026-01-26T00:00:00Z"
    assert meta["requested_window_start"] == "2026-01-01T00:00:00Z"


def test_full_history_omits_window_bounds() -> None:
    meta = resolve_window(
        full_history=True, now=datetime(2026, 8, 6, tzinfo=timezone.utc)
    )
    assert meta["mode"] == "full_history"
    assert "window_start" not in meta
    assert "window_end" not in meta


def test_partial_override_fails_closed() -> None:
    with pytest.raises(ValueError):
        resolve_window(window_start="2026-01-01T00:00:00Z")
