from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from weekly import WEEK, iso_week_start

#: Trailing whole ISO weeks covered by a scheduled run. Eight weeks is
#: "a couple of months" at the weekly reporting grain -- long enough for a
#: trend line to mean something, short enough that a shifting team shape does
#: not average away. Overridable via the Action's ``window-weeks`` input.
DEFAULT_WINDOW_WEEKS = 8


#: Every instant in the bundle is written in this form: RFC3339, UTC, with an
#: explicit ``Z``. Never a bare "YYYY-MM-DD HH:MM" -- an instant without an
#: offset is not a fact, and a reader who guesses their own timezone gets a
#: different moment than the one recorded. Consumers convert to local as they
#: display; the record itself stays unambiguous.
INSTANT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _instant_str(moment: datetime) -> str:
    return _normalize_utc(moment).strftime(INSTANT_FORMAT)


def _normalize_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = moment.astimezone(timezone.utc)
    return moment.replace(microsecond=0)


def _parse_instant(value: str, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"{label} is not a valid ISO-8601 timestamp: {value!r}"
        ) from exc
    return _normalize_utc(parsed)


def trailing_weeks_window(
    *, now: datetime | None = None, weeks: int = DEFAULT_WINDOW_WEEKS
) -> dict[str, Any]:
    """The scheduled default: ``weeks`` whole ISO weeks, ending at this week's Monday.

    This settles the alignment question that ``analysis-windows.md`` left open
    (run instant vs. start of the current civil period) in favour of **start of
    the current ISO week, UTC**. Three properties follow, and all three are the
    reason for choosing it:

    - **Gap-free and overlap-free.** Run N's ``window_end`` is a Monday
      midnight; run N+1's ``window_start`` is a Monday midnight ``weeks``
      earlier on the same lattice. Successive runs abut exactly, with no
      dependence on what time of day the cron happened to fire.
    - **DST-proof.** Every boundary is a UTC instant on a 7-day lattice, so
      there is no civil-calendar arithmetic to spring forward or fall back
      over. Note this is a property of pinning to UTC, not of weeks as such.
    - **No partial trailing week.** The in-progress week is excluded, so the
      last point on a chart is a finished week rather than a mid-week dip that
      always looks like a slowdown.

    The cost is latency: metrics for the week just ended appear on the first
    run after its Monday, which is exactly the weekly cadence the product
    documents anyway (``client/weekly-cadence.md``).
    """
    if weeks < 1:
        raise ValueError(f"weeks must be at least 1: {weeks!r}")
    # Resolve the reference instant once. Reading the clock twice could straddle
    # a second and put generated_at in a different week than the lattice.
    reference = _normalize_utc(now or datetime.now(timezone.utc))
    end = iso_week_start(reference)
    start = end - weeks * WEEK
    return {
        "timezone": "UTC",
        "mode": "trailing_weeks_utc",
        "grain": "iso_week",
        "weeks": weeks,
        "generated_at": _instant_str(reference),
        "window_start": _instant_str(start),
        "window_end": _instant_str(end),
        "window_start_civil": start.date().isoformat(),
        "window_end_civil": end.date().isoformat(),
    }


def resolve_window(
    *,
    now: datetime | None = None,
    window_start: str | None = None,
    window_end: str | None = None,
    full_history: bool = False,
    weeks: int = DEFAULT_WINDOW_WEEKS,
) -> dict[str, Any]:
    """Resolve the analysis window for one Action run.

    Fail-closed on ambiguous or partial overrides, mirroring gate.py's style:
    rather than guessing intent, raise so the caller must fix the inputs.

    - No override args: the scheduled default, ``trailing_weeks_window``.
    - ``full_history=True``: mode ``full_history``. Deliberately omits the
      ``window_start``/``window_end`` instant keys -- their absence is what
      tells ``default_calculator_runner`` to skip window filtering and derive
      its week span from the first and last commit instead. Only a civil
      ``window_end`` label is kept, for a human-readable record of when the
      run happened.
    - Explicit ``window_start``/``window_end``: both required together (fail
      closed if only one is set); ``window_start`` must be strictly before
      ``window_end``. Returns mode ``manual_override`` with the same instant
      + civil shape as ``trailing_weeks_window``. Both bounds **round down**
      to their ISO week start, so every mode measures whole 7-day buckets and
      no row is a part-week; the originally requested instants are kept under
      ``requested_window_*``.
    - ``full_history=True`` combined with either explicit bound is ambiguous
      and fails closed rather than silently picking one.
    """
    has_start = window_start is not None
    has_end = window_end is not None

    if full_history and (has_start or has_end):
        raise ValueError(
            "full_history cannot be combined with an explicit window_start/window_end override"
        )

    if full_history:
        # Only ``generated_at`` -- deliberately no ``window_end_civil``. That key
        # used to hold the run date here, which made the one date in a
        # full-history bundle a run timestamp wearing a window bound's name, and
        # it contradicted the rows (a run on the 6th reported coverage "through
        # 2026-08-06" while the last row ran to the 10th). What this mode
        # actually covers is derived from the commits and recorded as
        # ``analyzed_window_*`` once the rows exist.
        return {
            "timezone": "UTC",
            "mode": "full_history",
            "grain": "iso_week",
            "generated_at": _instant_str(now or datetime.now(timezone.utc)),
        }

    if has_start or has_end:
        if not (has_start and has_end):
            raise ValueError(
                "window_start and window_end must both be provided together"
            )
        requested_start = _parse_instant(window_start, label="window_start")
        requested_end = _parse_instant(window_end, label="window_end")
        if requested_start >= requested_end:
            raise ValueError("window_start must be strictly before window_end")

        # Both bounds round *down* to their week start, so an override covers a
        # whole number of 7-day buckets exactly like a scheduled run does. A
        # Wednesday reads as that week's Monday 00:00Z -- equivalently, the end
        # of the preceding Sunday. Without this an override produced two
        # part-weeks at the edges whose totals were not comparable to the rows
        # between them, which is the one way a "row per week" table can still
        # be measuring different-sized things.
        start = iso_week_start(requested_start)
        end = iso_week_start(requested_end)
        if start >= end:
            raise ValueError(
                "window_start and window_end round down to the same week "
                f"({start.date().isoformat()}); an override must span at least one whole ISO week"
            )
        return {
            "timezone": "UTC",
            "mode": "manual_override",
            "grain": "iso_week",
            "generated_at": _instant_str(now or datetime.now(timezone.utc)),
            "window_start": _instant_str(start),
            "window_end": _instant_str(end),
            "window_start_civil": start.date().isoformat(),
            "window_end_civil": end.date().isoformat(),
            # What was asked for, kept alongside what was measured, so a
            # backfill's rounding is visible in the bundle rather than inferred.
            "requested_window_start": _instant_str(requested_start),
            "requested_window_end": _instant_str(requested_end),
        }

    return trailing_weeks_window(now=now, weeks=weeks)


def analyzed_window(series: Sequence[dict[str, Any]]) -> dict[str, str]:
    """The range the emitted rows actually cover, taken from the rows themselves.

    Derived from the first and last row rather than from the requested bounds, so
    a bundle's stated coverage cannot contradict its own data. That contradiction
    was real in ``full_history``, where the only recorded date was the run date
    and it fell short of the final row's end.

    Week boundaries are always midnight UTC, so the civil dates on the rows
    promote to instants unambiguously.
    """
    if not series:
        return {}
    return {
        "analyzed_window_start": f"{series[0]['week_start']}T00:00:00Z",
        "analyzed_window_end": f"{series[-1]['week_end']}T00:00:00Z",
    }


def write_window_metadata(output_dir: Path, metadata: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "window_metadata.json"
    path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path
