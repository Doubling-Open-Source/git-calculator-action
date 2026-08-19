# git-calculator-action

Calculate a small set of aggregated, allowlisted git-history metrics — cycle time, change failure rate, and commit volume — one row per ISO week. Writes CSV and window metadata. No file-content checkout required (history-only). The Action fails closed if anything outside the allowlisted output files appears in the output directory.

Copyright (C) 2026 Doubling.

> **Status:** public v1.0. Licensed [GPLv3](LICENSE).

## Getting Started

```yaml
on:
  schedule:
    - cron: "0 0 * * 1" # weekly

permissions:
  contents: read

jobs:
  calculate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0 # full history, no blob checkout needed

      - uses: Doubling-Open-Source/git-calculator-action@v1
        with:
          output-dir: git-calculator-output
        env:
          GIT_CALCULATOR_API_KEY: ${{ secrets.GIT_CALCULATOR_API_KEY }} # optional; empty skips the Reports API POST

      # A second uses: in the same job is supported. Give each invocation
      # its own output-dir so the gated files do not overwrite.

      - uses: actions/upload-artifact@v7
        with:
          name: git-calculator-bundle
          path: git-calculator-output
          if-no-files-found: error
```

## Inputs

| Name | Default | Description |
| --- | --- | --- |
| `output-dir` | `git-calculator-output` | Directory for the gated metrics bundle. |
| `repository-path` | `${{ github.workspace }}` | Path to the git repository under analysis. |
| `calculator-remote` | `https://github.com/Doubling-Open-Source/git_calculator` | Git remote URL for the metrics-engine source pin. |
| `calculator-ref` | (pinned commit) | Full commit SHA (or tag) of the metrics-engine pin. |
| `window-weeks` | `8` | Trailing whole ISO weeks covered by a scheduled run. The window ends at the start of the current ISO week (UTC), so the in-progress week is excluded and consecutive runs neither gap nor overlap. Ignored when an explicit window or `full-history` is set. |
| `window-start` | (none) | Explicit ISO-8601 UTC window start for a one-off override (requires `window-end`). |
| `window-end` | (none) | Explicit ISO-8601 UTC window end for a one-off override (requires `window-start`). |
| `full-history` | `false` | Analyze full available history for one dispatch instead of the default trailing window. For setup, backfill, and debugging — not the scheduled default. |
| `work-style` | `all-branches` | Which commits count. `all-branches` counts every branch. `squash-merge` counts only commits reachable from the default branch, for repositories that collapse each branch into one commit on merge. Any other value fails the step. This selects the commit population before the analysis window slices it. |
| `default-branch` | (none) | Ref whose reachable commits are the population under `work-style: squash-merge`. Leave empty to auto-detect (`refs/remotes/origin/HEAD`, then `origin/main`, `origin/master`, then `HEAD`). A named ref that does not resolve fails the step rather than falling back to a guess. Ignored under `all-branches`. |
| `write-weekly-metrics-json` | `false` | Set to `true` to write `weekly_metrics.json` even when no Reports API credential is set. When a credential is present, the file is written anyway — this input is not required. Does not POST by itself. |
| `reports-api-key` | (none) | Optional override. Prefer step env `GIT_CALCULATOR_API_KEY`. Empty input and empty env skip the POST. |

## Outputs

This Action has no GitHub Actions `outputs:` — results are files written to `output-dir`:

| File | Holds |
| --- | --- |
| `window_metadata.json` | Resolved `[window_start, window_end)`, mode, timezone, grain, and when the run happened. |
| `commit_volume.csv` | Weekly commit counts and how many of those commits were errors. |
| `cycle_time.csv` | Weekly cycle time. |
| `change_failure_rate.csv` | Weekly change failure rate. |
| `weekly_metrics.json` | Reports API payload. Written when `GIT_CALCULATOR_API_KEY` or `reports-api-key` is set, or when `write-weekly-metrics-json` is `true`. |

See [`docs/output-schema.md`](docs/output-schema.md) for field-level detail. Optional Reports API POST and job summary: [`docs/reports-api-client.md`](docs/reports-api-client.md).

## License

[GNU General Public License v3.0](LICENSE).
