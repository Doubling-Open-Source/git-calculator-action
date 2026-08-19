"""Second-level CI check: CSV commit sum vs an independent rev-list.

Compares ``sum(commit_volume.csv commits)`` to:

    git rev-list --count <refs> --since="$window_start" --until="$window_end"

using the half-open analysis window recorded in ``window_metadata.json``.
Exit 0 when the totals match; exit 1 on mismatch or missing inputs.

``<refs>`` is not fixed: it follows the ``work_style`` the bundle records, so the
check counts the same population the bundle reports. Counting all of origin
against a ``squash`` bundle would report a mismatch on every run and train
readers to ignore the one check that is supposed to catch a real drift.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from scope import ALL_BRANCHES, SQUASH


def load_window_bounds(metadata: dict[str, Any]) -> tuple[str, str]:
    """Return ``(window_start, window_end)`` instants from metadata.

    Prefers the requested/resolved ``window_*`` keys; falls back to
    ``analyzed_window_*`` (e.g. full-history runs that omit fixed bounds).
    """
    start = metadata.get("window_start") or metadata.get("analyzed_window_start")
    end = metadata.get("window_end") or metadata.get("analyzed_window_end")
    if not start or not end:
        raise ValueError(
            "window_metadata.json missing window_start/window_end "
            "(and analyzed_window_start/analyzed_window_end)"
        )
    return str(start), str(end)


def sum_weekly_commits(csv_path: Path) -> int:
    """Sum the ``commits`` column across every row in ``commit_volume.csv``."""
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "commits" not in reader.fieldnames:
            raise ValueError(f"{csv_path} has no commits column")
        return sum(int(row["commits"] or 0) for row in reader)


def sum_weekly_commits_by_week(csv_path: Path) -> list[tuple[str, int]]:
    """Per-week commit counts for optional diagnostic output."""
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "commits" not in reader.fieldnames:
            raise ValueError(f"{csv_path} has no commits column")
        week_key = "week" if "week" in reader.fieldnames else None
        rows: list[tuple[str, int]] = []
        for index, row in enumerate(reader):
            label = row[week_key] if week_key else f"row-{index}"
            rows.append((label, int(row["commits"] or 0)))
        return rows


def scope_selector(metadata: dict[str, Any]) -> list[str]:
    """The ``git rev-list`` ref arguments matching the bundle's work style.

    Fails closed on a style this script does not know: a bundle written by a newer
    Action could scope its counts some way this check cannot reproduce, and
    guessing ``--remotes=origin`` would turn that into a wrong "MISMATCH" rather
    than an honest "cannot check".
    """
    style = metadata.get("work_style", ALL_BRANCHES)
    if style == ALL_BRANCHES:
        return ["--remotes=origin"]
    if style == SQUASH:
        ref = metadata.get("scoped_ref")
        if not ref:
            raise ValueError(
                "window_metadata.json records work_style 'squash' without a scoped_ref, "
                "so the population it counted cannot be reproduced"
            )
        return [str(ref)]
    raise ValueError(
        f"window_metadata.json records unknown work_style {style!r}; "
        "this check cannot reproduce that population"
    )


def count_commits(*, repo: Path, since: str, until: str, selector: list[str]) -> int:
    """Count commits reachable from ``selector`` in ``[since, until)`` via git."""
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "rev-list",
            "--count",
            *selector,
            f"--since={since}",
            f"--until={until}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.strip())


def compare_commit_totals(csv_total: int, remote_total: int) -> bool:
    """True when the CSV sum equals the remote rev-list count."""
    return csv_total == remote_total


def verify_output_dir(output_dir: Path, *, repo: Path) -> tuple[int, int, bool]:
    """Load the gated bundle and compare CSV sum to an independent rev-list.

    Returns ``(csv_total, remote_total, matched)``.
    """
    metadata_path = output_dir / "window_metadata.json"
    csv_path = output_dir / "commit_volume.csv"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"missing {metadata_path}")
    if not csv_path.is_file():
        raise FileNotFoundError(f"missing {csv_path}")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    since, until = load_window_bounds(metadata)
    csv_total = sum_weekly_commits(csv_path)
    remote_total = count_commits(
        repo=repo, since=since, until=until, selector=scope_selector(metadata)
    )
    return csv_total, remote_total, compare_commit_totals(csv_total, remote_total)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify sum(commit_volume.csv commits) equals git rev-list --count "
            "over the ref set the bundle's work_style names, for the analysis window."
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Gated bundle directory (window_metadata.json + commit_volume.csv)",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="Git repository to count (default: cwd)",
    )
    parser.add_argument(
        "--per-week",
        action="store_true",
        help="Print per-week commit counts from the CSV",
    )
    args = parser.parse_args(argv)

    try:
        metadata = json.loads((args.output_dir / "window_metadata.json").read_text(encoding="utf-8"))
        since, until = load_window_bounds(metadata)
        csv_path = args.output_dir / "commit_volume.csv"
        csv_total = sum_weekly_commits(csv_path)
        if args.per_week:
            for week, count in sum_weekly_commits_by_week(csv_path):
                print(f"  {week}: {count}")
        selector = scope_selector(metadata)
        remote_total = count_commits(
            repo=args.repo, since=since, until=until, selector=selector
        )
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"verify-remote-counts: error: {exc}", file=sys.stderr)
        return 1

    counted = " ".join(selector)
    print(f"window: [{since}, {until})")
    print(f"work style: {metadata.get('work_style', ALL_BRANCHES)} (counting {counted})")
    print(f"commit_volume.csv commits sum: {csv_total}")
    print(f"git rev-list {counted} count: {remote_total}")

    if compare_commit_totals(csv_total, remote_total):
        print(f"verify-remote-counts: MATCH — {counted} count equals CSV sum")
        return 0

    print(
        f"verify-remote-counts: MISMATCH — CSV sum {csv_total} != {counted} {remote_total}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
