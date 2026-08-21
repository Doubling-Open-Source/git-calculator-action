"""Which commits count: the work-style scope filter.

The pinned calculator reads history with ``git log --all --reflog`` and takes no
ref argument, so branch scope cannot be pushed down into it. It is applied here
instead, to the commits it hands back.

Scope is not the analysis window, and the distinction decides where the filter
goes. Scope defines the *population* -- which commits are work at all under this
repository's merge convention. The window then selects a slice of that
population. Applying them in the other order would measure each author's
cycle time against predecessors that the report does not itself count.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Sequence

ALL_BRANCHES = "all-branches"
SQUASH = "squash"
SQUASH_MERGE = "squash-merge"

#: Accepted ``work-style`` values. ``squash-merge`` is kept as an alias of
#: ``squash`` so existing workflows keep working after the engine named the
#: style ``squash``. Anything else fails closed rather than falling back to a
#: default -- a typo'd style would otherwise silently report the wrong
#: population, which is the one failure mode nobody would notice.
WORK_STYLES = (ALL_BRANCHES, SQUASH)
_WORK_STYLE_ALIASES = {SQUASH_MERGE: SQUASH}

DEFAULT_WORK_STYLE = ALL_BRANCHES

#: Tried in order, after ``refs/remotes/origin/HEAD``, when no explicit
#: ``default-branch`` is given. Remote-tracking refs come first: they are what a
#: CI checkout actually has, and they are the branch as the remote sees it
#: rather than whatever a local clone happens to have checked out.
DEFAULT_BRANCH_CANDIDATES = ("origin/main", "origin/master", "main", "master", "HEAD")


def resolve_work_style(value: str | None) -> str:
    """Validate a ``work-style`` input, treating empty/unset as the default."""
    if value is None or value == "":
        return DEFAULT_WORK_STYLE
    canonical = _WORK_STYLE_ALIASES.get(value, value)
    if canonical not in WORK_STYLES:
        raise ValueError(
            f"unknown work-style {value!r}; expected one of {', '.join(WORK_STYLES)} "
            f"(or alias {SQUASH_MERGE})"
        )
    return canonical


def _git(
    repo: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run git in ``repo``, raising ``RuntimeError`` rather than leaking git's own.

    ``CalledProcessError`` would escape ``run.py``'s ``(RuntimeError, ValueError)``
    handler and surface as a traceback; a git failure here is a configuration
    problem and should read like the window-override errors do.
    """
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"git {' '.join(args)} failed in {str(repo)!r}: {detail}")
    return result


def _resolves_to_commit(repo: Path, ref: str) -> bool:
    return (
        _git(
            repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False
        ).returncode
        == 0
    )


def resolve_default_branch(repo: Path, *, explicit: str | None = None) -> str:
    """The ref whose reachable commits are the ``squash`` population.

    An explicit input is honoured or rejected -- never quietly replaced by a
    guess, because a caller who named a ref is asserting something about the
    repository and a fallback would hide their mistake behind plausible numbers.

    Otherwise the remote's own idea of its default branch wins
    (``refs/remotes/origin/HEAD``), then the conventional names. ``HEAD`` is the
    last rung so a repository with unconventional branch naming still measures
    something; it resolves in any repository that has a commit at all.
    """
    if explicit:
        if not _resolves_to_commit(repo, explicit):
            raise RuntimeError(
                f"default-branch {explicit!r} does not resolve to a commit in {str(repo)!r}; "
                "check the ref name, and that the workflow fetched it"
            )
        return explicit

    candidates: list[str] = []
    symbolic = _git(
        repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False
    )
    if symbolic.returncode == 0 and symbolic.stdout.strip():
        candidates.append(symbolic.stdout.strip())
    candidates.extend(DEFAULT_BRANCH_CANDIDATES)

    for ref in candidates:
        if _resolves_to_commit(repo, ref):
            return ref

    raise RuntimeError(
        "could not resolve a default branch for the squash work style; tried "
        f"{', '.join(candidates)}. Set the default-branch input explicitly."
    )


def reachable_shas(repo: Path, ref: str) -> set[str]:
    """Every commit SHA reachable from ``ref``.

    This is also what excludes reflog-only commits (amended, rebased away,
    dropped) that the pin's ``--reflog`` picks up: anything unreachable from the
    ref is absent by construction rather than by a second filter.
    """
    return set(_git(repo, "rev-list", ref).stdout.split())


def resolve_scope(
    repo: Path,
    *,
    work_style: str,
    default_branch: str | None = None,
) -> dict[str, Any]:
    """Turn a work style into a concrete description of the commit population.

    Separate from ``scope_commits`` so the part that can fail -- resolving a ref
    -- runs before the run writes anything. A bad ``default-branch`` should end
    the job with a stated reason, not leave a half-written output directory that
    the gate then reports on.

    The returned mapping is what the bundle records, so a reader can tell which
    population a count is over ([Output schema] ``work_style`` / ``scoped_ref``).
    """
    if work_style == ALL_BRANCHES:
        return {"work_style": ALL_BRANCHES, "scoped_ref": None}
    return {
        "work_style": SQUASH,
        "scoped_ref": resolve_default_branch(repo, explicit=default_branch),
    }


def scope_commits(
    commits: Sequence[Any], *, repo: Path, scope: dict[str, Any]
) -> list[Any]:
    """Restrict ``commits`` to the population ``scope`` describes.

    The membership test compares the commit objects themselves. They are
    ``git_obj(git_sha(str))`` built from ``%H``, so each one's string value *is*
    its full 40-character SHA and it inherits ``str.__hash__`` / ``__eq__`` --
    but ``git_sha.__str__`` is overridden to return a *truncated* display form.
    Calling ``str()`` on one here would compare a four-character prefix against
    full SHAs and match nothing, reporting an empty repository.
    """
    ref = scope["scoped_ref"]
    if ref is None:
        return list(commits)
    shas = reachable_shas(repo, ref)
    return [commit for commit in commits if commit in shas]
