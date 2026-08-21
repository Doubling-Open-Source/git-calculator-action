# AGENTS.md

## Cursor Cloud specific instructions

This repo is a **Python composite GitHub Action** (`action.yml`) that computes allowlisted,
aggregated git-history metrics (commit volume, cycle time, change failure rate) one row per ISO
week. The Action orchestrator lives in `run.py`; supporting modules are `gate.py`, `scope.py`,
`summary.py`, `weekly.py`, `window.py`, `post_reports.py`, `reports_key.py`. Tests are in `tests/`.
There is **no separate lint tool** configured — the only quality gate is `pytest` (see
`.github/workflows/unit.yml`).

### Python toolchain / virtualenv (important)

The base image only ships `python3` (there is **no `python` or `pip` command**). CI relies on
`actions/setup-python`, which provides both. Two tests (`tests/test_action_yml.py::test_post_step_*`)
and the pipeline's engine step shell out to `python`/`pip`, so they only pass under an interpreter
whose `bin/` contains a `python` symlink — i.e. a virtualenv.

The startup update script provisions this automatically at `/.venv` in the repo root using the
pure-pip `virtualenv` tool (the distro `python3-venv` package is not installed and is not needed).
Use that venv for everything:

- Run tests: `PYTHONPATH=. .venv/bin/pytest tests -q` (expect **60 passed**).
- Run any module: `.venv/bin/python ...`.
- If `.venv` is ever missing, recreate it: `python3 -m pip install --break-system-packages virtualenv && python3 -m virtualenv .venv && .venv/bin/pip install -r requirements-dev.txt`.

Running `python3 -m pytest` against the *system* interpreter will fail those 2 subprocess tests
(58/60) solely because `python` is absent — this is an environment artifact, not a code bug. Always
use the venv.

### Running the application (the metrics pipeline)

The Action clones and `pip install`s a **pinned `git_calculator` engine** at runtime; that engine is
NOT part of this repo and is NOT installed by the startup script (it is an external network clone of
a pinned SHA, kept out of startup to keep pods reliable). To run `run.py` locally, install the pin
into the venv first (pinned ref is `action.yml` input `calculator-ref`, currently
`fb8c70756ab5d55f5fb3b91962685b00b5975ea3` = git_calculator v2.1.0):

```bash
src=$(mktemp -d /tmp/git_calculator_src.XXXXXX)
git clone --depth 1 https://github.com/Doubling-Open-Source/git_calculator "$src"
git -C "$src" fetch --depth 1 origin fb8c70756ab5d55f5fb3b91962685b00b5975ea3
git -C "$src" checkout fb8c70756ab5d55f5fb3b91962685b00b5975ea3
.venv/bin/pip install "$src"
```

Then run against any local git repo (this repo works as its own sample):

```bash
PYTHONPATH=/workspace .venv/bin/python run.py \
  --repo /workspace --output-dir /tmp/gc-output \
  --calculator-src "$src" \
  --calculator-remote https://github.com/Doubling-Open-Source/git_calculator \
  --calculator-ref fb8c70756ab5d55f5fb3b91962685b00b5975ea3 \
  --allowlist /workspace/allowlist.json --full-history
```

Notes:
- `--full-history` is the reliable way to always produce output locally (the scheduled default
  `--window-weeks 8` trailing window can be empty if there are no recent commits). Add
  `--write-weekly-metrics-json` to also emit the Reports API payload `weekly_metrics.json`.
- The pipeline **fails closed** (exit 1) if any file outside `allowlist.json` appears in the output
  dir; a config error (e.g. partial window override) exits 2. A clean run exits 0 and writes
  `window_metadata.json` + the three CSVs.
- The Reports API POST (`post_reports.py`) is skipped unless `GIT_CALCULATOR_API_KEY` (or the
  `reports-api-key` input) is set; it is not needed for local dev.
