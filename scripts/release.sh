#!/usr/bin/env bash
# Cut a GitHub Action release from a clean, up-to-date main.
# Usage: ./scripts/release.sh 1.0.1
# Maintainer docs: docs/developer/releasing.md
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || $# -ne 1 ]]; then
  echo "Usage: ./scripts/release.sh X.Y.Z" >&2
  echo "From a clean main that matches origin/main: annotated tag vX.Y.Z," >&2
  echo "GitHub Release, and move the major tag (v1, v2, …) so @v1 tracks the patch." >&2
  exit 2
fi

version="$1"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "version must be X.Y.Z (got ${version})" >&2
  exit 2
fi

tag="v${version}"
major_tag="v${version%%.*}"
root="$(git rev-parse --show-toplevel)"
cd "$root"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "working tree is not clean" >&2
  exit 1
fi

branch="$(git branch --show-current)"
if [[ "$branch" != "main" ]]; then
  echo "checkout main before releasing (on ${branch})" >&2
  exit 1
fi

git fetch origin
if [[ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]]; then
  echo "HEAD does not match origin/main" >&2
  exit 1
fi

if git rev-parse "$tag" >/dev/null 2>&1; then
  echo "tag ${tag} already exists locally" >&2
  exit 1
fi
if git ls-remote --exit-code --tags origin "refs/tags/${tag}" >/dev/null 2>&1; then
  echo "tag ${tag} already exists on origin" >&2
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "gh is required" >&2
  exit 1
fi

git tag -a "$tag" -m "git-calculator-action ${version}"
git push origin "$tag"

git tag -f "$major_tag" "$(git rev-parse "${tag}^{commit}")"
git push origin "+refs/tags/${major_tag}"

gh release create "$tag" --title "$version" --generate-notes --verify-tag

echo "released ${tag}; ${major_tag} now points at ${tag}"
