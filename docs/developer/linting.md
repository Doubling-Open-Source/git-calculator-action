# Linting

Maintainer path. Consumers of the Action do not need this.

Install hooks once, then they run on each commit:

```bash
pip install -r requirements-dev.txt
pre-commit install
pre-commit run --all-files
```

CI runs the same `pre-commit run --all-files` check on non-draft pull requests.
