from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scope import resolve_scope, resolve_work_style, scope_commits


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _two_branch_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "commit", "--allow-empty", "-m", "on main")
    on_main = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature")
    _git(repo, "commit", "--allow-empty", "-m", "on feature")
    on_feature = _git(repo, "rev-parse", "HEAD")
    return repo, on_main, on_feature


class _Commit(str):
    """Pin-shaped double: value is the full SHA, str() is truncated."""

    def __new__(cls, sha: str) -> "_Commit":
        return super().__new__(cls, sha)

    def __str__(self) -> str:
        return self[:4]


def test_unknown_work_style_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown work-style"):
        resolve_work_style("main-only")


def test_named_default_branch_that_does_not_resolve_fails_closed(tmp_path: Path) -> None:
    repo, _, _ = _two_branch_repo(tmp_path)
    with pytest.raises(RuntimeError, match="does not resolve to a commit"):
        resolve_scope(repo, work_style="squash-merge", default_branch="does-not-exist")


def test_all_branches_keeps_every_commit_squash_merge_drops_unreachable(tmp_path: Path) -> None:
    repo, on_main, on_feature = _two_branch_repo(tmp_path)
    commits = [_Commit(on_main), _Commit(on_feature)]
    kept_all = scope_commits(
        commits, repo=repo, scope={"work_style": "all-branches", "scoped_ref": None}
    )
    kept_squash = scope_commits(
        commits, repo=repo, scope={"work_style": "squash-merge", "scoped_ref": "main"}
    )
    assert [c[:] for c in kept_all] == [on_main, on_feature]
    assert [c[:] for c in kept_squash] == [on_main]


def test_squash_merge_matches_full_sha_not_display_form(tmp_path: Path) -> None:
    repo, on_main, on_feature = _two_branch_repo(tmp_path)
    commits = [_Commit(on_main), _Commit(on_feature)]
    kept = scope_commits(
        commits, repo=repo, scope={"work_style": "squash-merge", "scoped_ref": "main"}
    )
    assert str(kept[0]) == on_main[:4]
    assert kept[0][:] == on_main
