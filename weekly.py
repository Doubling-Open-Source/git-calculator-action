"""ISO-week grain: the Action's one standardized measure.

Every series in the bundle is bucketed on the *same* weeks, keyed the same
way, in the same units. The pinned calculator buckets cycle time by commit
*count* (``bucket_size``) and change failure by *calendar month*, each
labelled ``YYYY-MM`` off ``datetime.fromtimestamp`` (runner-local timezone).
Those three inconsistencies -- different grain, different key, floating
timezone -- are resolved here rather than upstream, so the pin stays fixed.

Conventions, applied without exception:

- **Grain** is the ISO week: half-open ``[Monday 00:00 UTC, next Monday 00:00 UTC)``.
- **Key** is ``YYYY-Www`` (ISO year + ISO week, zero-padded), which sorts
  lexicographically in chronological order. ISO year is *not* always the
  calendar year of the Monday -- that is the point of using it.
- **Durations** are hours, everywhere, rounded to 2 decimals. Upstream emits
  minutes; charts downstream had been re-deriving days. One unit only.
- **Every week in the window gets a row**, including weeks with no commits.
  A chart that silently skips empty weeks reads as continuous when it isn't.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import stdev
from typing import Any, Callable, Iterable, Sequence

WEEK = timedelta(days=7)

#: Weeks with fewer commits than this are flagged in the bundle summary.
#: Aggregates over a handful of commits are noise and can still imply an
#: individual on a small team.
SMALL_N_COMMITS = 5

#: stdev() needs two points; p75/average need one. Recorded per week as
#: ``cycle_time_samples`` so a reader can tell "quiet week" from "no data".
MIN_STDDEV_SAMPLES = 2


def iso_week_start(moment: datetime) -> datetime:
    """Monday 00:00:00 UTC of the ISO week containing ``moment``."""
    utc = moment.astimezone(timezone.utc) if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    midnight = utc.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=midnight.weekday())


def week_key(moment: datetime) -> str:
    """``YYYY-Www`` ISO key for the week containing ``moment``."""
    iso = iso_week_start(moment).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _epoch_to_utc(when: float) -> datetime:
    return datetime.fromtimestamp(when, tz=timezone.utc)


DAYS_PER_WEEK = 7


def days_elapsed(week_start: datetime, week_end: datetime, *, now: datetime) -> float:
    """How much of a week had actually happened as of ``now``, in days.

    ``7.0`` once the week is over, ``0.0`` before it starts, fractional while it
    is in progress. This is elapsed *real time*, not window coverage: the window
    always contains all seven days of every row (bounds are week-aligned), but
    the newest row can still be a week that has not finished.
    """
    if now >= week_end:
        return float(DAYS_PER_WEEK)
    if now <= week_start:
        return 0.0
    return round((now - week_start).total_seconds() / 86400, 1)


def week_spans(*, start: datetime, end: datetime, now: datetime) -> list[dict[str, Any]]:
    """Every ISO week in the half-open window ``[start, end)``.

    Callers must pass week-aligned bounds; every window mode rounds down to a
    week start before reaching here, so each row is a whole 7-day *bucket*.
    Passing unaligned bounds is a programming error rather than something to
    paper over with a flag -- it silently makes one row span a different amount
    of time than its neighbours.

    A full bucket is not the same as a finished week, which is why ``now`` is
    required. ``full_history`` rounds its end *up* to include the week holding
    the newest commit, so its last row is normally the current, in-progress
    week; a manual override can also name an end in the future. Each span
    therefore carries ``days_elapsed`` and ``complete``, so a reader can tell
    "quiet week" from "week that has barely started" -- comparing four days of
    commits against a neighbour's seven is the mistake this prevents.
    """
    if start >= end:
        raise ValueError("week_spans requires start strictly before end")
    for label, bound in (("start", start), ("end", end)):
        if bound != iso_week_start(bound):
            raise ValueError(
                f"week_spans requires week-aligned bounds; {label}={bound.isoformat()} "
                f"is not a Monday 00:00 UTC (nearest earlier: {iso_week_start(bound).isoformat()})"
            )

    spans: list[dict[str, Any]] = []
    cursor = start
    while cursor < end:
        week_end = cursor + WEEK
        elapsed = days_elapsed(cursor, week_end, now=now)
        spans.append(
            {
                "week": week_key(cursor),
                "week_start": cursor.date().isoformat(),
                "week_end": week_end.date().isoformat(),
                "days_elapsed": elapsed,
                "complete": elapsed >= DAYS_PER_WEEK,
            }
        )
        cursor = week_end
    return spans


def bucket_by_week(items: Iterable[Any], *, when: Callable[[Any], float]) -> dict[str, list[Any]]:
    """Group ``items`` into ``YYYY-Www`` buckets by their epoch timestamp."""
    buckets: dict[str, list[Any]] = defaultdict(list)
    for item in items:
        buckets[week_key(_epoch_to_utc(when(item)))].append(item)
    return dict(buckets)


def percentile(values: Sequence[float], fraction: float) -> float:
    """Linear-interpolated percentile, matching ``numpy.percentile``'s default.

    Reimplemented in the stdlib so this module stays importable (and unit
    testable) without the calculator's own dependency tree installed. The
    interpolation rule is numpy's so a p75 here equals a p75 from the pinned
    calculator on the same inputs.
    """
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * weight)


def _hours(minutes: float) -> float:
    return round(minutes / 60.0, 2)


def cycle_time_stats(deltas_minutes: Sequence[float]) -> dict[str, Any]:
    """Cycle-time aggregates for one week, in hours.

    ``None`` means "not computable from this week's samples", never zero:
    zero hours is a real (and very good) cycle time, so conflating the two
    would put a fictional floor on the chart.
    """
    count = len(deltas_minutes)
    if count == 0:
        return {"samples": 0, "avg_hours": None, "p75_hours": None, "stddev_hours": None}
    return {
        "samples": count,
        "avg_hours": _hours(sum(deltas_minutes) / count),
        "p75_hours": _hours(percentile(deltas_minutes, 0.75)),
        "stddev_hours": _hours(stdev(deltas_minutes)) if count >= MIN_STDDEV_SAMPLES else None,
    }


def build_weekly_series(
    *,
    spans: Sequence[dict[str, Any]],
    deltas: Iterable[Sequence[float]],
    commits_by_week: dict[str, tuple[int, int]],
    rates_by_week: dict[str, float],
) -> list[dict[str, Any]]:
    """Join both metric families onto one row per week in ``spans``.

    Args:
        spans: ``week_spans`` output -- defines the row set and its order.
        deltas: ``[epoch_seconds, minutes]`` pairs from the calculator's
            ``calculate_time_deltas``, already restricted to the window.
        commits_by_week: week key -> ``(total_commits, fix_commits)``.
        rates_by_week: week key -> change failure rate percent, as returned by
            the calculator's ``calculate_change_failure_rate``.
    """
    deltas_by_week: dict[str, list[float]] = defaultdict(list)
    for when, minutes in deltas:
        deltas_by_week[week_key(_epoch_to_utc(when))].append(minutes)

    series: list[dict[str, Any]] = []
    for span in spans:
        key = span["week"]
        commits, fix_commits = commits_by_week.get(key, (0, 0))
        series.append(
            {
                **span,
                "commits": commits,
                "fix_commits": fix_commits,
                # No commits means no rate to report -- not a 0% failure rate.
                "change_failure_rate_pct": rates_by_week.get(key) if commits else None,
                "cycle_time": cycle_time_stats(deltas_by_week.get(key, [])),
                "small_n": 0 < commits < SMALL_N_COMMITS,
            }
        )
    return series


# --- CSV files -------------------------------------------------------------
# One file per metric. Shared week key columns lead every file so they join
# on sight.

#: Shared by all three CSVs. ``days_elapsed`` sits with the key columns because
#: it qualifies the row's time span, not any one metric: every number in a row
#: with fewer than 7 days covers less time than its neighbours.
WEEK_COLUMNS = ("week", "week_start", "week_end", "days_elapsed", "complete")

COMMIT_VOLUME_COLUMNS = (
    *WEEK_COLUMNS,
    "commits",
    "error",
)

CYCLE_TIME_COLUMNS = (
    *WEEK_COLUMNS,
    "commits",
    "samples",
    "avg_hours",
    "p75_hours",
    "stddev_hours",
)

CHANGE_FAILURE_COLUMNS = (
    *WEEK_COLUMNS,
    "commits",
    "fix_commits",
    "change_failure_rate_pct",
)


def _cell(value: Any) -> str:
    """Render one CSV cell; ``None`` becomes empty, which readers treat as NA."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _to_csv(columns: Sequence[str], rows: Iterable[dict[str, Any]]) -> str:
    lines = [",".join(columns)]
    lines.extend(",".join(_cell(row.get(col)) for col in columns) for row in rows)
    return "\n".join(lines) + "\n"


def commit_volume_csv(series: Sequence[dict[str, Any]]) -> str:
    """Weekly commit counts. ``error`` is how many of those commits were errors."""
    rows = [
        {
            **{k: row[k] for k in (*WEEK_COLUMNS, "commits")},
            "error": row["fix_commits"],
        }
        for row in series
    ]
    return _to_csv(COMMIT_VOLUME_COLUMNS, rows)


def cycle_time_csv(series: Sequence[dict[str, Any]]) -> str:
    """Cycle-time columns, carrying ``commits`` so a week's weight is visible here too."""
    rows = [{**{k: row[k] for k in (*WEEK_COLUMNS, "commits")}, **row["cycle_time"]} for row in series]
    return _to_csv(CYCLE_TIME_COLUMNS, rows)


def change_failure_rate_csv(series: Sequence[dict[str, Any]]) -> str:
    return _to_csv(CHANGE_FAILURE_COLUMNS, series)
