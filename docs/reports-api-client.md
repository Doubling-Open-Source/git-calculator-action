# Optional Reports API client

This is the maintainer path for testing a live POST. Consumers of the
Action do not need it: with no key the Action writes the three CSVs and
`window_metadata.json` only.

When a key is set, the Action also writes `weekly_metrics.json` and POSTs
it.

GitHub will not inject a repository secret unless the workflow maps it.
Map it on the Action step (not the job): job-level `env` leaks the secret
to checkout, artifact upload, and every other step. Getting Started and
dogfood set:

```yaml
- uses: Doubling-Open-Source/git-calculator-action@v1
  with:
    output-dir: git-calculator-output
  env:
    GIT_CALCULATOR_API_KEY: ${{ secrets.GIT_CALCULATOR_API_KEY }}
```

Create secret `GIT_CALCULATOR_API_KEY`. That value is the Reports API
identity: it is sent as `x-api-key`. There is no second token. An unset
secret is empty and skips the POST. Sibling steps do not inherit step
`env`. Inside the Action, the key is detected without being written to
disk. `setup-python`, clone, `pip install`, and the calculator do not
receive it (step-local empty env). The POST step inherits the caller
mapping, materializes the key for that step only, and removes the temp
file when it exits. The Action does not wipe the secret via `$GITHUB_ENV`
(that would persist for the rest of the caller's job). The Action POSTs
to `https://gitcalculator.doubling.io`. Override `reports-api-url` only
when that host does not fit. The override must be `https` with a host;
any other value fails the step, including when no key is set and the
POST is skipped.

## Request a key

Open an [issue](https://github.com/Doubling-Open-Source/git-calculator-action/issues/new)
on this repository and ask for a Reports API key. Include the GitHub
repo (`owner/name`) the key should cover.

After Doubling replies with a key, store it as `GIT_CALCULATOR_API_KEY`
(repo secret) and map it with step `env` as above. The next scheduled run
POSTs to `https://gitcalculator.doubling.io`. To use a different key name
or host, either point `GIT_CALCULATOR_API_KEY` at that secret or pass
`reports-api-key` (input wins when both are set):

```yaml
- uses: Doubling-Open-Source/git-calculator-action@v1
  with:
    output-dir: git-calculator-output
    reports-api-key: ${{ secrets.OTHER_NAME }}
    reports-api-url: https://other.example
```

## Inputs

| Name | Default | Description |
| --- | --- | --- |
| `reports-api-key` | (none) | Optional override of the same identity as step env `GIT_CALCULATOR_API_KEY`. When both are empty, skip the POST. Never logged. |
| `reports-api-url` | `https://gitcalculator.doubling.io` | Reports API base URL. Override must be `https` with a host (local emulator included). Any other value fails the step, including when the POST is skipped. |
| `reports-api-repo` | `github.repository` | Repo string posted to the API. Override for local runs whose `github.repository` does not match the key. |

## Contract

When the key is set, the Action POSTs
`{ reportType: "summary", repo, data }` to `/v1/reports` with `x-api-key`.
`repo` defaults to `github.repository`. The response `markdown` is
appended to `$GITHUB_STEP_SUMMARY`. The Action does not render markdown
itself.

You can invoke the Action more than once in the same job (for example
two `work-style` values). Each invocation clones the pinned calculator
into its own directory under `RUNNER_TEMP`, so a second `uses:` does
not fail on an existing dest. Give each invocation a distinct
`output-dir`. Two POSTs append two markdown blocks to
`$GITHUB_STEP_SUMMARY`.

The default base URL is `https://gitcalculator.doubling.io`. Pass
`reports-api-url` to use another `https` host. `http`, other schemes,
and `https` URLs with no host are rejected before a request is sent,
including when no key is present.

Do not log the key. If response markdown contains it, it is replaced
with `[redacted]` before writing `--out-file` or `$GITHUB_STEP_SUMMARY`.
HTTP redirects are not followed; a `3xx` response fails the step.

On HTTP `429`, the step fails and prints `Retry-After`.

## Local tests

```bash
pip install -r requirements-dev.txt
PYTHONPATH=. pytest tests -q
```

That includes the Reports API client tests. They stand up a local HTTPS
server; they do not call a remote Reports API. CI
(`.github/workflows/unit.yml`) writes `junit.xml` so the Checks tab
lists every case.

To POST a real `weekly_metrics.json` from a laptop:

```bash
python post_reports.py \
  --url "$REPORTS_API_URL" \
  --key-file "$REPORTS_API_KEY_FILE" \
  --repo owner/name \
  --metrics-file weekly_metrics.json \
  --out-file summary.md
```
