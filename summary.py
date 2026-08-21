"""Chart-ready JSON summary of the weekly series.

``weekly_metrics.json`` is the only surface built here -- the per-week series
plus a ``charts`` block of pre-paired ``labels``/``values``, so a downstream
consumer does not have to re-derive the grain, re-pool the aggregates, or
decide what to do about missing weeks. Whoever draws a chart from this data
should not be making statistical choices; whoever renders it into prose is a
separate, downstream concern -- this Action stops at the numbers.
"""

from __future__ import annotations

from typing import Any, Sequence

from weekly import percentile

#: 2 -- charts gained multi-plot ``plots`` (was a single ``values``) and a
#: commit-volume chart; ``partial`` left the series and ``complete_weeks`` left
#: the summary once every window mode became week-aligned.
#: 3 -- added ``generated_at`` and ``analyzed_window_*``; ``full_history`` no
#: longer reports the run date as ``window_end_civil``.
#: 4 -- rows carry ``days_elapsed`` / ``complete``, and the summary reports
#: whether the newest week had finished when the run happened.
#: 5 -- drop published ``calculator_pin``; cycle-time chart values stay in
#: hours (a renderer converts to days if it wants that scale).
#: 6 -- reorder ``charts`` to volume → cycle time → change failure.
#: 7 -- drop ``summary.md`` / rendered-markdown output; this Action now emits
#: data only. Also: ``window`` (and so ``weekly_metrics.json``) carries
#: ``work_style`` / ``scoped_ref``, naming which commits count.
SCHEMA_VERSION = 7

#: Default docs landing for the open-source metrics engine when the caller does
#: not pass a project URL. Prefer the Action's ``calculator-remote`` README.
DEFAULT_ENGINE_DOCS_URL = "https://github.com/Doubling-Open-Source/git_calculator"


def _pooled_cycle_time_p75_hours(cycle_time_minutes: Sequence[float]) -> float | None:
    """Window-level p75 over every in-window sample.

    Taken from the raw samples, not from the weekly p75s: a percentile of
    percentiles is not the percentile, and averaging weekly figures would
    weight a two-commit week the same as a forty-commit one.
    """
    if not cycle_time_minutes:
        return None
    return round(percentile(cycle_time_minutes, 0.75) / 60.0, 2)


def build_payload(
    *,
    series: Sequence[dict[str, Any]],
    window: dict[str, Any],
    cycle_time_minutes: Sequence[float] = (),
    engine_docs_url: str = DEFAULT_ENGINE_DOCS_URL,
) -> dict[str, Any]:
    """Assemble the ``weekly_metrics.json`` payload.

    Args:
        series: one row per ISO week, from ``weekly.build_weekly_series``.
        window: the resolved window metadata, recorded verbatim.
        cycle_time_minutes: every in-window cycle-time sample, used for the
            window-level p75 headline. The weekly rows carry their own
            per-week stats; this is only for the pooled figure.
        engine_docs_url: open-source calculator project URL for the payload's
            engine pointer (not a commit pin; reproducibility is Action inputs).
    """
    commits = sum(row["commits"] for row in series)
    fix_commits = sum(row["fix_commits"] for row in series)
    active = [row for row in series if row["commits"] > 0]

    return {
        "schema_version": SCHEMA_VERSION,
        "grain": "iso_week",
        "timezone": "UTC",
        # Promoted to the top level as well as living in `window`: this is the
        # answer to "when was this produced", and it must be findable without
        # knowing that the window metadata is where time lives. Do not rely on
        # file modification times for this.
        "generated_at": window.get("generated_at"),
        "units": {
            "cycle_time": "hours",
            "change_failure_rate": "percent",
        },
        "window": window,
        # Docs pointer only — never a commit SHA. Pin stays in Action inputs.
        "engine_docs_url": engine_docs_url,
        "summary": {
            "weeks": len(series),
            "weeks_with_commits": len(active),
            "commits": commits,
            "fix_commits": fix_commits,
            # Work-volume signal: a commits total means little without knowing
            # how many weeks it is spread across.
            "commits_per_active_week": round(commits / len(active), 1)
            if active
            else None,
            "busiest_week": max(active, key=lambda row: row["commits"])["week"]
            if active
            else None,
            # Pooled over the window, not the mean of weekly rates -- a
            # 40-commit week and a 2-commit week must not count equally.
            "change_failure_rate_pct": round(fix_commits / commits * 100, 1)
            if commits
            else None,
            "cycle_time_p75_hours": _pooled_cycle_time_p75_hours(cycle_time_minutes),
            "weeks_without_commits": [
                row["week"] for row in series if row["commits"] == 0
            ],
            "small_n_weeks": [row["week"] for row in series if row["small_n"]],
            # Whether the newest row is a finished week is the first thing a
            # reader needs, because it decides if the last point on every chart
            # is comparable to the one before it.
            "last_week": series[-1]["week"] if series else None,
            "last_week_complete": series[-1]["complete"] if series else None,
            "last_week_days_elapsed": series[-1]["days_elapsed"] if series else None,
            "incomplete_weeks": [row["week"] for row in series if not row["complete"]],
        },
        "series": list(series),
        "charts": [
            # Scan order for a devops reader: volume (denominator), then cycle
            # time (flow), then change failure (quality).
            _chart(
                series,
                chart_id="commit_volume",
                title="Commit volume by week",
                y_label="Commits",
                plots=[
                    {
                        "label": "Commits",
                        "kind": "bar",
                        "value": lambda row: row["commits"],
                    },
                    {
                        "label": "Fix commits",
                        "kind": "line",
                        "value": lambda row: row["fix_commits"],
                    },
                ],
            ),
            _chart(
                series,
                chart_id="cycle_time_p75_hours",
                title="P75 cycle time by week",
                y_label="Hours",
                plots=[
                    {
                        "label": "P75 cycle time",
                        "kind": "line",
                        "value": lambda row: row["cycle_time"]["p75_hours"],
                    }
                ],
            ),
            _chart(
                series,
                chart_id="change_failure_rate_pct",
                title="Change failure rate by week",
                y_label="Percent",
                plots=[
                    {
                        "label": "Change failure rate",
                        "kind": "line",
                        "value": lambda row: row["change_failure_rate_pct"],
                    }
                ],
            ),
        ],
    }


def _chart(
    series: Sequence[dict[str, Any]],
    *,
    chart_id: str,
    title: str,
    y_label: str,
    plots: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """One chart spec: a shared label axis plus one or more parallel plots.

    A week is plotted only when **every** plot on the chart has a value for it;
    otherwise it is named in ``omitted_weeks``. Omitting rather than
    zero-filling keeps a renderer from drawing a dip that never happened, and
    naming it keeps the gap visible instead of hidden behind a straight line.

    Counts are never ``None``, so the volume chart keeps every week -- there,
    zero commits is a measurement, not a missing value.
    """
    labels: list[str] = []
    values: list[list[float]] = [[] for _ in plots]
    omitted: list[str] = []
    for row in series:
        points = [plot["value"](row) for plot in plots]
        if any(point is None for point in points):
            omitted.append(row["week"])
            continue
        labels.append(row["week"])
        for column, point in zip(values, points):
            column.append(point)
    return {
        "id": chart_id,
        "title": title,
        "x_label": "ISO week",
        "y_label": y_label,
        "labels": labels,
        "plots": [
            {"label": plot["label"], "kind": plot["kind"], "values": column}
            for plot, column in zip(plots, values)
        ],
        "omitted_weeks": omitted,
    }
