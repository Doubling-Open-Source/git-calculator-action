from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gate import gate_output_dir
from scope import (
    ALL_BRANCHES,
    DEFAULT_WORK_STYLE,
    WORK_STYLES,
    resolve_scope,
    resolve_work_style,
    scope_commits,
)
from summary import build_payload
from weekly import (
    WEEK,
    bucket_by_week,
    build_weekly_series,
    change_failure_rate_csv,
    commit_volume_csv,
    cycle_time_csv,
    iso_week_start,
    week_spans,
)
from window import DEFAULT_WINDOW_WEEKS, analyzed_window, resolve_window, write_window_metadata

CalculatorRunner = Callable[..., None]


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def resolve_span_bounds(window: dict[str, Any], *, timestamps: list[float]) -> tuple[datetime, datetime]:
    """The ``[start, end)`` the weekly rows should cover.

    A bounded window (scheduled default or manual override) states its own
    bounds, already rounded down to week starts by ``resolve_window``.

    Full history has no bounds by design -- ``resolve_window`` omits the instant
    keys precisely so this branch is reachable -- so the range comes from the
    first and last commit present, widened to whole weeks. Note the end rounds
    *up* here, unlike every other mode: full history means "everything", and
    rounding the end down would silently drop the newest week's commits. That is
    also why full history can include an in-progress week when the scheduled
    default never does.
    """
    if "window_start" in window and "window_end" in window:
        return _instant(window["window_start"]), _instant(window["window_end"])

    if not timestamps:
        raise RuntimeError("full history requested but the repository has no commits to analyze")

    earliest = iso_week_start(datetime.fromtimestamp(min(timestamps), tz=timezone.utc))
    latest = iso_week_start(datetime.fromtimestamp(max(timestamps), tz=timezone.utc)) + WEEK
    return earliest, latest


def weekly_change_failure_counts(logs_by_week: dict[str, list[Any]], *, extract_commit_data: Any) -> dict[str, tuple[int, int]]:
    """Per-week ``(total_commits, fix_commits)`` using the calculator's own classifier.

    ``extract_commit_data`` buckets internally by calendar month and returns a
    month-keyed mapping, so it is called once per *week* of commits and its
    month buckets summed back together. That keeps the fix-keyword heuristic --
    the part carrying real domain meaning, and the part we must not fork --
    exactly as pinned, while the grain decision stays here.
    """
    counts: dict[str, tuple[int, int]] = {}
    for week, commits in logs_by_week.items():
        by_month = extract_commit_data(commits)
        total = sum(total for total, _ in by_month.values())
        fixes = sum(fixes for _, fixes in by_month.values())
        counts[week] = (total, fixes)
    return counts


def default_calculator_runner(
    *,
    repo_path: Path,
    output_dir: Path,
    window: dict[str, Any],
    calculator_src: Path,
    engine_docs_url: str,
    scope: dict[str, Any] | None = None,
    write_weekly_metrics_json: bool = False,
) -> None:
    """Emit one row per ISO week for every metric. Aggregates only.

    Must never import or call author-level analyzers.

    Uses the pinned calculator for the two things that encode its domain
    judgement -- the per-author consecutive-commit cycle-time definition and
    the fix-commit keyword classifier -- and does the bucketing, unit
    normalisation, and summarisation here. The pin only knows how to bucket by
    calendar month or by commit count, and re-pinning is not the place to fix a
    reporting-grain decision.

    Branch scope is applied here for the same reason: the pin reads history with
    ``git log --all --reflog`` and takes no ref argument, so the work style
    filters what it returns. ``scope`` arrives already resolved (see
    ``scope.resolve_scope``) so a ref that does not exist has already ended the
    run before this point.
    """
    # Imported here, not at module scope: this is the only function that runs
    # the calculator, and keeping them local leaves the rest of the module (and
    # its unit tests) free of the pinned tree's side effects.
    import logging
    import os

    src_path = str(calculator_src.resolve())
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    original_cwd = os.getcwd()
    os.chdir(repo_path)
    try:
        from src.git_ir import git_log
        from src.calculators.cycle_time_by_commits_calculator import calculate_time_deltas
        from src.calculators.change_failure_calculator import extract_commit_data, calculate_change_failure_rate

        # Those modules call logging.basicConfig(level=DEBUG) at import time and
        # log every commit object they touch. On a client-sized repo that buries
        # the run's actual output in tens of MB of commit dumps, so turn it down
        # once the handler exists. Warnings and errors still come through.
        logging.getLogger().setLevel(logging.WARNING)

        every_commit = git_log()

        # Scope before deltas, window after. The work style decides which
        # commits are work at all under this repo's merge convention, so it
        # defines the population every later step measures; the window then
        # slices that population by time. Scoping after the deltas were computed
        # would measure an author's cycle time against a predecessor the report
        # does not count -- on a squash-merge repo, against the scratch commits
        # the squash superseded, which is the reading this input exists to fix.
        resolved_scope = scope or {"work_style": ALL_BRANCHES, "scoped_ref": None}
        logs = scope_commits(every_commit, repo=repo_path, scope=resolved_scope)

        # Deltas come from *full* (in-scope) history, then get filtered by their
        # own timestamp. Filtering commits by time first would silently drop each
        # author's earliest in-window delta (it has no predecessor left to
        # measure against), which biases the first week of every window.
        all_deltas = calculate_time_deltas(logs)
        start, end = resolve_span_bounds(window, timestamps=[log._when for log in logs])
        start_ts, end_ts = start.timestamp(), end.timestamp()

        deltas = [delta for delta in all_deltas if start_ts <= delta[0] < end_ts]
        commits_in_window = [log for log in logs if start_ts <= log._when < end_ts]
        scope_note = (
            f"work style {resolved_scope['work_style']}: "
            f"{len(logs)}/{len(every_commit)} commits in scope"
            + (
                f" (reachable from {resolved_scope['scoped_ref']})"
                if resolved_scope["scoped_ref"]
                else ""
            )
        )
        print(
            f"{scope_note}. weekly grain: {len(commits_in_window)}/{len(logs)} commits and "
            f"{len(deltas)}/{len(all_deltas)} cycle-time samples inside "
            f"[{start.isoformat()}, {end.isoformat()}).",
            file=sys.stderr,
        )

        counts = weekly_change_failure_counts(
            bucket_by_week(commits_in_window, when=lambda commit: commit._when),
            extract_commit_data=extract_commit_data,
        )
        # calculate_change_failure_rate is key-agnostic -- it divides fixes by
        # total per key -- so week keys pass through it unchanged.
        rates = calculate_change_failure_rate(counts)

        series = build_weekly_series(
            # Elapsed-days come from generated_at, not from the window end: the
            # window can legitimately extend past now (full history rounds up to
            # the week holding the newest commit), and it is real time that
            # decides whether a week has finished.
            spans=week_spans(start=start, end=end, now=_instant(window["generated_at"])),
            deltas=deltas,
            commits_by_week=counts,
            rates_by_week=rates,
        )
    finally:
        os.chdir(original_cwd)

    write_weekly_outputs(
        output_dir,
        series=series,
        window=window,
        engine_docs_url=engine_docs_url,
        cycle_time_minutes=[minutes for _, minutes in deltas],
        write_weekly_metrics_json=write_weekly_metrics_json,
    )


def write_weekly_outputs(
    output_dir: Path,
    *,
    series: list[dict[str, Any]],
    window: dict[str, Any],
    engine_docs_url: str,
    cycle_time_minutes: list[float],
    write_weekly_metrics_json: bool = False,
) -> None:
    """Write the standardized bundle: three CSVs on one grain, plus window metadata.

    ``weekly_metrics.json`` is only written when a Reports API key is set -- it
    is the POST body, not the default artifact. All three CSVs lead with the
    same ``week,week_start,week_end`` columns, so they join without a lookup
    table. Every file here must have a matching pattern in ``allowlist.json``
    -- the gate runs after this and fails closed on anything it does not
    recognise.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "commit_volume.csv").write_text(commit_volume_csv(series), encoding="utf-8")
    (output_dir / "cycle_time.csv").write_text(cycle_time_csv(series), encoding="utf-8")
    (output_dir / "change_failure_rate.csv").write_text(change_failure_rate_csv(series), encoding="utf-8")

    # The rows exist now, so the bundle can state its true coverage. Rewritten
    # into window_metadata.json rather than left to the reader to infer, and
    # folded into the payload so both files agree when the JSON is written.
    window = {**window, **analyzed_window(series)}
    write_window_metadata(output_dir, window)

    payload_path = output_dir / "weekly_metrics.json"
    if not write_weekly_metrics_json:
        # A reused output-dir can still hold a keyed run's payload; it is
        # allowlisted, so the gate would accept it and the key-free bundle
        # would be wrong.
        payload_path.unlink(missing_ok=True)
        return
    payload = build_payload(
        series=series,
        window=window,
        engine_docs_url=engine_docs_url,
        cycle_time_minutes=cycle_time_minutes,
    )
    payload_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def run_pipeline(
    *,
    repo_path: Path,
    output_dir: Path,
    calculator_src: Path,
    calculator_remote: str,
    _calculator_ref: str,
    allowlist_path: Path,
    now: datetime | None = None,
    calculator_runner: CalculatorRunner | None = None,
    window_start: str | None = None,
    window_end: str | None = None,
    full_history: bool = False,
    weeks: int = DEFAULT_WINDOW_WEEKS,
    work_style: str = DEFAULT_WORK_STYLE,
    default_branch: str | None = None,
    write_weekly_metrics_json: bool = False,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    window = resolve_window(
        now=now,
        window_start=window_start,
        window_end=window_end,
        full_history=full_history,
        weeks=weeks,
    )
    # Resolved before anything is written, alongside the window: both are
    # configuration that can fail closed, and both belong in the metadata from
    # its first write so a failed run's working directory still says what was
    # asked for.
    scope = resolve_scope(
        repo_path,
        work_style=resolve_work_style(work_style),
        default_branch=default_branch,
    )
    window = {**window, **scope}
    write_window_metadata(output_dir, window)
    # Pin (remote/ref) is Action input only — never written into the gated bundle.
    engine_docs_url = calculator_remote

    runner = calculator_runner or default_calculator_runner
    if calculator_runner is None:
        runner(
            repo_path=repo_path,
            output_dir=output_dir,
            window=window,
            calculator_src=calculator_src,
            engine_docs_url=engine_docs_url,
            scope=scope,
            write_weekly_metrics_json=write_weekly_metrics_json,
        )
    else:
        runner(repo_path=repo_path, output_dir=output_dir, window=window)

    gate_output_dir(output_dir, allowlist_path)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run git calculator + allowlist gate")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--calculator-src", type=Path, required=True)
    parser.add_argument("--calculator-remote", required=True)
    parser.add_argument("--calculator-ref", required=True)
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=Path(__file__).resolve().parent / "allowlist.json",
    )
    parser.add_argument(
        "--window-weeks",
        type=int,
        default=DEFAULT_WINDOW_WEEKS,
        help=f"Trailing whole ISO weeks for the scheduled default window (default {DEFAULT_WINDOW_WEEKS}).",
    )
    parser.add_argument(
        "--window-start",
        default=None,
        help="Explicit ISO-8601 UTC window start (requires --window-end); dispatch override only.",
    )
    parser.add_argument(
        "--window-end",
        default=None,
        help="Explicit ISO-8601 UTC window end (requires --window-start); dispatch override only.",
    )
    parser.add_argument(
        "--full-history",
        action="store_true",
        help="Analyze full available history instead of the default trailing window.",
    )
    parser.add_argument(
        "--work-style",
        default=DEFAULT_WORK_STYLE,
        help=(
            "Which commits count: "
            f"{' | '.join(WORK_STYLES)} (default {DEFAULT_WORK_STYLE}). "
            "squash-merge counts only commits reachable from the default branch."
        ),
    )
    parser.add_argument(
        "--default-branch",
        default=None,
        help=(
            "Ref whose reachable commits are the population under squash-merge; "
            "omit to detect it. A ref that does not resolve fails the run."
        ),
    )
    parser.add_argument(
        "--write-weekly-metrics-json",
        action="store_true",
        help="Write weekly_metrics.json (Reports API POST body). Off unless a key is set.",
    )
    args = parser.parse_args(argv)
    run_pipeline(
        repo_path=args.repo,
        output_dir=args.output_dir,
        calculator_src=args.calculator_src,
        calculator_remote=args.calculator_remote,
        _calculator_ref=args.calculator_ref,
        allowlist_path=args.allowlist,
        now=datetime.now(timezone.utc),
        calculator_runner=None,
        window_start=args.window_start,
        window_end=args.window_end,
        full_history=args.full_history,
        weeks=args.window_weeks,
        # Validated in run_pipeline rather than by argparse `choices`: the
        # message names the accepted styles without a page of usage text, and it
        # lands on the same exit-2 path as the window-override errors.
        work_style=args.work_style,
        default_branch=args.default_branch,
        write_weekly_metrics_json=args.write_weekly_metrics_json,
    )


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as exc:
        # Bad or ambiguous inputs (partial window override, weeks < 1) are a
        # configuration error, not a crash: exit 2 with the reason, so the
        # Action step fails readably instead of dumping a traceback.
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
