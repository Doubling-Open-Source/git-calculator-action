from __future__ import annotations

import csv
import json
import sqlite3
import subprocess
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from gate import gate_output_dir

ALLOWLIST = Path(__file__).resolve().parents[1] / "allowlist.json"

_THREE_WEEK_WINDOW = {
    "timezone": "UTC",
    "mode": "trailing_weeks_utc",
    "grain": "iso_week",
    "weeks": 3,
    "generated_at": "2026-07-22T15:04:05Z",
    "window_start": "2026-06-29T00:00:00Z",
    "window_end": "2026-07-20T00:00:00Z",
    "window_start_civil": "2026-06-29",
    "window_end_civil": "2026-07-20",
}


class _Commit(str):
    _next = 0

    def __new__(cls, when: float, message: str = "chore: work", sha: str | None = None) -> "_Commit":
        if sha is None:
            _Commit._next += 1
            sha = f"{_Commit._next:040x}"
        obj = super().__new__(cls, sha)
        obj._when = when
        obj.message = message
        return obj

    def __str__(self) -> str:
        return self[:4]


def _epoch(*args: int) -> float:
    return datetime(*args, tzinfo=timezone.utc).timestamp()


def _mock_calculator(monkeypatch, *, commits, deltas) -> dict[str, Any]:
    calls: dict[str, Any] = {}

    def git_log():
        return list(commits)

    class FakeLake:
        def __init__(self, path: str | None = None) -> None:
            self.conn = sqlite3.connect(":memory:")
            self.conn.execute(
                "CREATE TABLE commits ("
                "sha TEXT, author_email TEXT, committed_date INTEGER, "
                "_raw_data_params TEXT, message TEXT, log_ordinal INTEGER)"
            )

        def load_logs(self, logs, repo_id: str) -> int:
            calls["delta_logs"] = list(logs)
            self.conn.execute("DELETE FROM commits")
            for ordinal, commit in enumerate(logs):
                self.conn.execute(
                    "INSERT INTO commits VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        commit[:],
                        "",
                        commit._when,
                        repo_id,
                        getattr(commit, "message", "") or None,
                        ordinal,
                    ),
                )
            self.conn.commit()
            return len(logs)

        def calculate_time_deltas_sql(self, repo_id: str | None = None):
            return [list(delta) for delta in deltas]

        def close(self) -> None:
            self.conn.close()

    git_ir = types.ModuleType("git_calculator.git_ir")
    git_ir.git_log = git_log
    lake_mod = types.ModuleType("git_calculator.calculators.sqlite_lake")
    lake_mod.SqliteLake = FakeLake
    util = types.ModuleType("git_calculator.util.git_util")
    util.get_repo_id = lambda: "local:test"
    keywords = types.ModuleType(
        "git_calculator.calculators.sqlite_lake.commits_export_keywords"
    )
    keywords.text_has_change_failure_keyword = lambda text: "fix" in text.lower()
    work_style = types.ModuleType("git_calculator.work_style")
    work_style.SQUASH = "squash"
    rates = types.ModuleType("git_calculator.calculators.change_failure_calculator")
    rates.calculate_change_failure_rate = lambda data: {
        key: round(fixes / total * 100, 1) if total else 0
        for key, (total, fixes) in data.items()
    }
    monkeypatch.setitem(sys.modules, "git_calculator", types.ModuleType("git_calculator"))
    monkeypatch.setitem(sys.modules, "git_calculator.git_ir", git_ir)
    monkeypatch.setitem(sys.modules, "git_calculator.util", types.ModuleType("git_calculator.util"))
    monkeypatch.setitem(sys.modules, "git_calculator.util.git_util", util)
    monkeypatch.setitem(sys.modules, "git_calculator.calculators", types.ModuleType("git_calculator.calculators"))
    monkeypatch.setitem(sys.modules, "git_calculator.calculators.sqlite_lake", lake_mod)
    monkeypatch.setitem(
        sys.modules,
        "git_calculator.calculators.sqlite_lake.commits_export_keywords",
        keywords,
    )
    monkeypatch.setitem(sys.modules, "git_calculator.work_style", work_style)
    monkeypatch.setitem(
        sys.modules, "git_calculator.calculators.change_failure_calculator", rates
    )
    return calls


def _run(
    monkeypatch,
    tmp_path: Path,
    *,
    commits,
    deltas,
    window,
    scope=None,
    repo=None,
    write_weekly_metrics_json: bool = False,
):
    import run as run_mod

    calls = _mock_calculator(monkeypatch, commits=commits, deltas=deltas)
    for directory in ("git_calc", "out", "repo"):
        (tmp_path / directory).mkdir(exist_ok=True)
    run_mod.default_calculator_runner(
        repo_path=repo or tmp_path / "repo",
        output_dir=tmp_path / "out",
        window=window,
        calculator_src=tmp_path / "git_calc",
        engine_docs_url="https://example.invalid/git_calculator",
        scope=scope,
        write_weekly_metrics_json=write_weekly_metrics_json,
    )
    return tmp_path / "out", calls


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _two_branch_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "commit", "--allow-empty", "-m", "on main")
    on_main = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature")
    _git(repo, "commit", "--allow-empty", "-m", "on feature")
    on_feature = _git(repo, "rev-parse", "HEAD")
    return repo, on_main, on_feature


def test_runner_writes_the_standardized_bundle_that_passes_the_gate(monkeypatch, tmp_path: Path) -> None:
    commits = [
        _Commit(_epoch(2026, 6, 30, 10, 0), "fix: patch"),
        _Commit(_epoch(2026, 7, 15, 12, 0), "feat: later"),
    ]
    out, _ = _run(
        monkeypatch,
        tmp_path,
        commits=commits,
        deltas=[[_epoch(2026, 6, 30, 10, 0), 60.0]],
        window=_THREE_WEEK_WINDOW,
    )
    for name in (
        "commit_volume.csv",
        "cycle_time.csv",
        "change_failure_rate.csv",
        "window_metadata.json",
    ):
        assert (out / name).is_file(), name
    assert not (out / "weekly_metrics.json").exists()
    assert not (out / "calculator_pin.json").exists()
    gate_output_dir(out, ALLOWLIST)


def test_key_free_run_removes_stale_weekly_metrics_json(monkeypatch, tmp_path: Path) -> None:
    stale = tmp_path / "out" / "weekly_metrics.json"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("{}\n", encoding="utf-8")
    out, _ = _run(
        monkeypatch,
        tmp_path,
        commits=[_Commit(_epoch(2026, 6, 30, 10, 0), "feat: work")],
        deltas=[],
        window=_THREE_WEEK_WINDOW,
    )
    assert not (out / "weekly_metrics.json").exists()
    gate_output_dir(out, ALLOWLIST)


def test_commit_volume_error_is_the_error_commit_count(monkeypatch, tmp_path: Path) -> None:
    # Nine commits, one error: Poisson sqrt(9) is 3, so the two meanings
    # disagree. The CSV must emit the error-commit count.
    commits = [_Commit(_epoch(2026, 6, 30, 10, minute), "feat: work") for minute in range(8)]
    commits.append(_Commit(_epoch(2026, 6, 30, 11, 0), "fix: patch"))
    out, _ = _run(
        monkeypatch,
        tmp_path,
        commits=commits,
        deltas=[],
        window=_THREE_WEEK_WINDOW,
    )
    with (out / "commit_volume.csv").open(newline="", encoding="utf-8") as handle:
        busy = next(row for row in csv.DictReader(handle) if row["commits"] == "9")
    assert busy["error"] == "1"


def test_runner_excludes_commits_outside_the_window(monkeypatch, tmp_path: Path) -> None:
    commits = [
        _Commit(_epoch(2026, 6, 30, 10, 0), "fix: in"),
        _Commit(_epoch(2026, 8, 1, 12, 0), "feat: after"),
    ]
    out, _ = _run(
        monkeypatch,
        tmp_path,
        commits=commits,
        deltas=[
            [_epoch(2026, 6, 30, 10, 0), 60.0],
            [_epoch(2026, 8, 1, 12, 0), 90.0],
        ],
        window=_THREE_WEEK_WINDOW,
        write_weekly_metrics_json=True,
    )
    payload = json.loads((out / "weekly_metrics.json").read_text(encoding="utf-8"))
    assert payload["summary"]["commits"] == 1
    assert sum(row["cycle_time"]["samples"] for row in payload["series"]) == 1


def test_squash_merge_is_applied_before_cycle_time_deltas(monkeypatch, tmp_path: Path) -> None:
    repo, on_main, on_feature = _two_branch_repo(tmp_path)
    when = _epoch(2026, 7, 1, 11, 0)
    commits = [
        _Commit(when, "feat: main", on_main),
        _Commit(_epoch(2026, 7, 2, 11, 0), "feat: branch", on_feature),
    ]
    _, calls = _run(
        monkeypatch,
        tmp_path,
        commits=commits,
        deltas=[],
        window=_THREE_WEEK_WINDOW,
        scope={"work_style": "squash", "scoped_ref": "main"},
        repo=repo,
    )
    assert [sha[:] for sha in calls["delta_logs"]] == [on_main]


def test_squash_does_not_count_a_body_only_fix_keyword(monkeypatch, tmp_path: Path) -> None:
    commits = [
        _Commit(
            _epoch(2026, 6, 30, 10, 0),
            "feat: login\n\nfix leftover from stacked commits",
        )
    ]
    out, _ = _run(
        monkeypatch,
        tmp_path,
        commits=commits,
        deltas=[],
        window=_THREE_WEEK_WINDOW,
        scope={"work_style": "squash", "scoped_ref": None},
    )
    with (out / "commit_volume.csv").open(newline="", encoding="utf-8") as handle:
        busy = next(row for row in csv.DictReader(handle) if row["commits"] == "1")
    assert busy["error"] == "0"


def test_all_branches_counts_a_body_fix_keyword(monkeypatch, tmp_path: Path) -> None:
    commits = [
        _Commit(
            _epoch(2026, 6, 30, 10, 0),
            "feat: login\n\nfix leftover from stacked commits",
        )
    ]
    out, _ = _run(
        monkeypatch,
        tmp_path,
        commits=commits,
        deltas=[],
        window=_THREE_WEEK_WINDOW,
        scope={"work_style": "all-branches", "scoped_ref": None},
    )
    with (out / "commit_volume.csv").open(newline="", encoding="utf-8") as handle:
        busy = next(row for row in csv.DictReader(handle) if row["commits"] == "1")
    assert busy["error"] == "1"


def test_silent_week_does_not_invent_a_change_failure_rate(monkeypatch, tmp_path: Path) -> None:
    commits = [
        _Commit(_epoch(2026, 6, 30, 10, 0), "feat: first week"),
        _Commit(_epoch(2026, 7, 15, 12, 0), "feat: third week"),
    ]
    out, _ = _run(
        monkeypatch,
        tmp_path,
        commits=commits,
        deltas=[],
        window=_THREE_WEEK_WINDOW,
        write_weekly_metrics_json=True,
    )
    payload = json.loads((out / "weekly_metrics.json").read_text(encoding="utf-8"))
    quiet = next(row for row in payload["series"] if row["commits"] == 0)
    assert quiet["change_failure_rate_pct"] is None
    assert quiet["cycle_time"]["avg_hours"] is None


def test_one_commit_week_is_flagged_small_n(monkeypatch, tmp_path: Path) -> None:
    commits = [_Commit(_epoch(2026, 6, 30, 10, 0), "feat: only")]
    out, _ = _run(
        monkeypatch,
        tmp_path,
        commits=commits,
        deltas=[],
        window=_THREE_WEEK_WINDOW,
        write_weekly_metrics_json=True,
    )
    payload = json.loads((out / "weekly_metrics.json").read_text(encoding="utf-8"))
    thin = next(row for row in payload["series"] if row["commits"] == 1)
    assert thin["small_n"] is True


def test_payload_declares_grain_and_does_not_embed_the_pin(monkeypatch, tmp_path: Path) -> None:
    commits = [_Commit(_epoch(2026, 6, 30, 10, 0), "feat: work")]
    out, _ = _run(
        monkeypatch,
        tmp_path,
        commits=commits,
        deltas=[],
        window=_THREE_WEEK_WINDOW,
        write_weekly_metrics_json=True,
    )
    payload = json.loads((out / "weekly_metrics.json").read_text(encoding="utf-8"))
    assert payload["grain"] == "iso_week"
    assert payload["units"]["cycle_time"] == "hours"
    assert "calculator_pin" not in payload


def test_full_history_with_no_commits_fails_closed(monkeypatch, tmp_path: Path) -> None:
    window = {"timezone": "UTC", "mode": "full_history", "grain": "iso_week", "generated_at": "2026-08-06T00:00:00Z"}
    with pytest.raises(RuntimeError):
        _run(monkeypatch, tmp_path, commits=[], deltas=[], window=window)
