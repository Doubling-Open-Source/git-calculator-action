# Releasing

Maintainer path. Consumers of the Action do not need this.

From a clean `main` that matches `origin/main`:

```bash
./scripts/release.sh 1.0.1
```

That publishes GitHub Release `v1.0.1` and moves the `v1` tag so `Doubling-Open-Source/git-calculator-action@v1` picks up the patch.

Requires `git` and `gh`. The version argument is `X.Y.Z` (no leading `v`).
