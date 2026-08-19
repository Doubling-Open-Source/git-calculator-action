from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from run import run_pipeline

ALLOWLIST = Path(__file__).resolve().parents[1] / "allowlist.json"
REMOTE = "https://github.com/Doubling-Open-Source/git_calculator"


def _stub(*, repo_path: Path, output_dir: Path, window: dict) -> None:
    assert repo_path.exists()
    (output_dir / "commit_volume.csv").write_text("week\n", encoding="utf-8")
    (output_dir / "cycle_time.csv").write_text("week\n", encoding="utf-8")
    (output_dir / "change_failure_rate.csv").write_text("week\n", encoding="utf-8")


def _stub_with_leak(*, repo_path: Path, output_dir: Path, window: dict) -> None:
    _stub(repo_path=repo_path, output_dir=output_dir, window=window)
    (output_dir / "commit_bob_commits.csv").write_text("bad\n", encoding="utf-8")


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "on main"], cwd=repo, check=True)
    return repo


def _run(tmp_path: Path, *, repo: Path | None = None, **kwargs):
    if repo is None:
        repo = tmp_path / "repo"
        repo.mkdir()
    kwargs.setdefault("calculator_runner", _stub)
    return run_pipeline(
        repo_path=repo,
        output_dir=tmp_path / "out",
        calculator_src=tmp_path / "unused-calc",
        calculator_remote=REMOTE,
        _calculator_ref="abc123deadbeef",
        allowlist_path=ALLOWLIST,
        now=datetime(2026, 7, 22, 15, 0, 0, tzinfo=timezone.utc),
        **kwargs,
    )


def test_pipeline_passes_with_the_standardized_bundle(tmp_path: Path) -> None:
    _run(tmp_path)
    out = tmp_path / "out"
    assert (out / "window_metadata.json").is_file()
    assert not (out / "weekly_metrics.json").exists()
    assert not (out / "calculator_pin.json").exists()


def test_pipeline_fails_closed_on_an_extra_file(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as ei:
        _run(tmp_path, calculator_runner=_stub_with_leak)
    assert ei.value.code == 1


def test_pipeline_rejects_a_partial_window_override(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _run(tmp_path, window_start="2026-01-01T00:00:00Z")


def test_pipeline_rejects_full_history_with_explicit_bounds(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _run(
            tmp_path,
            full_history=True,
            window_start="2026-01-01T00:00:00Z",
            window_end="2026-02-01T00:00:00Z",
        )


@pytest.mark.parametrize("work_style", ["squash", "squash-merge"])
def test_pipeline_records_canonical_squash_for_both_work_style_inputs(
    tmp_path: Path, work_style: str
) -> None:
    _run(tmp_path, repo=_git_repo(tmp_path), work_style=work_style)
    metadata = json.loads((tmp_path / "out" / "window_metadata.json").read_text(encoding="utf-8"))
    assert metadata["work_style"] == "squash"
    assert metadata["scoped_ref"]
