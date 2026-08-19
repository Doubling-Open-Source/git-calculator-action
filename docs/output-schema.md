# Output schema

One row per ISO week, one unit per quantity, one key across every file.

## Grain and units

- **Grain:** ISO week, `[Monday 00:00 UTC, next Monday 00:00 UTC)`.
- **Key:** `week` as `YYYY-Www` (sorts lexicographically in chronological order).
- **Durations:** hours, to two decimals.
- **Rates:** percent.
- **Timezone:** UTC throughout.

Every instant is RFC3339 UTC with an explicit `Z` (e.g. `2026-08-06T16:53:04Z`).

| Field | Answers |
| --- | --- |
| `generated_at` | When this ran. |
| `analyzed_window_start` / `analyzed_window_end` | What period the rows actually cover, derived from the emitted rows. |
| `work_style` | Which commits count: `all-branches` or `squash-merge` (see `work-style` input). |
| `scoped_ref` | Under `squash-merge`, the resolved ref whose reachable commits were counted. `null` under `all-branches`. |

## Every week gets a row

Weeks with no commits are emitted with zero counts and empty metric cells. Missing values are empty in CSV and `null` in JSON — never `0`. `cycle_time.samples` distinguishes a quiet week from an unmeasurable one.

## Row completeness

| Field | Meaning |
| --- | --- |
| `days_elapsed` | How much of the week had elapsed as of `generated_at`: `7.0` once finished, fractional while in progress. |
| `complete` | `days_elapsed` has reached `7` — comparable to neighboring rows without qualification. |
| `small_n` | Fewer than five commits in the row; aggregates over a small sample are noise. |

## CSVs

Three files, one per metric, same `week` key: `commit_volume.csv` (`commits`, `error` as the count of error commits), `cycle_time.csv`, `change_failure_rate.csv`. Always written.

## `weekly_metrics.json`

Written only when a Reports API key is set (the POST body). Not part of the default artifact.

```jsonc
{
  "schema_version": 7,
  "grain": "iso_week",
  "timezone": "UTC",
  "generated_at": "...",
  "units": { "cycle_time": "hours", "change_failure_rate": "percent" },
  "window": { /* resolved window metadata */ },
  "engine_docs_url": "...",
  "summary": { /* pooled window-level figures */ },
  "series": [ /* one row per ISO week */ ],
  "charts": [ /* labels + plot values for commit volume, cycle time, and change failure rate */ ]
}
```

`summary` figures are pooled over the window's raw samples, not averaged from the weekly rows — a percentile of percentiles is not a percentile, and a forty-commit week must not count the same as a two-commit week.

`charts` gives each chart a shared `labels` axis and one or more `plots` (`label`, `kind`, `values`), plus `omitted_weeks` naming weeks with no value for that chart.

## Versioning

`schema_version` (currently `7`) bumps when a field changes meaning or disappears; adding a field does not require a bump.
