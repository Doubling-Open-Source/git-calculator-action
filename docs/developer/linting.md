# Linting

Maintainer path. Consumers of the Action do not need this.

Install hooks once, then they run on staged files at commit:

```bash
pip install -r requirements-dev.txt
pre-commit install
```

CI (the unit workflow) runs format, lint, and tests in one job:

```bash
ruff format --check .
ruff check .
PYTHONPATH=. pytest tests -q
```

To apply the same checks locally without committing:

```bash
ruff format .
ruff check --fix .
PYTHONPATH=. pytest tests -q
```
