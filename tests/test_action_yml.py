from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_ACTION_YML = Path(__file__).resolve().parents[1] / "action.yml"

_REPORTS_API_EMPTY_DEFAULTS = (
    "reports-api-key",
    "reports-api-repo",
)

_DEFAULT_REPORTS_API_URL = "https://gitcalculator.doubling.io"

_INPUT_NAME = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")
_FIELD = re.compile(r"^    ([A-Za-z0-9_-]+):\s*(.*)$")
_TOP_LEVEL = re.compile(r"^[A-Za-z0-9_-]+:")


def _parse_action_inputs(text: str) -> dict[str, dict[str, str]]:
    lines = text.splitlines()
    try:
        start = lines.index("inputs:") + 1
    except ValueError as exc:
        raise AssertionError("action.yml has no inputs: mapping") from exc

    parsed: dict[str, dict[str, str]] = {}
    name: str | None = None
    fields: dict[str, str] = {}
    for line in lines[start:]:
        if _TOP_LEVEL.match(line):
            break
        name_match = _INPUT_NAME.match(line)
        if name_match:
            if name is not None:
                parsed[name] = fields
            name = name_match.group(1)
            fields = {}
            continue
        field_match = _FIELD.match(line)
        if field_match and name is not None:
            fields[field_match.group(1)] = field_match.group(2).strip()
    if name is not None:
        parsed[name] = fields
    return parsed


@pytest.fixture(scope="module")
def action_inputs() -> dict[str, dict[str, str]]:
    return _parse_action_inputs(_ACTION_YML.read_text(encoding="utf-8"))


@pytest.mark.parametrize("input_name", _REPORTS_API_EMPTY_DEFAULTS)
def test_action_yml_keeps_reports_api_inputs_optional(
    action_inputs: dict[str, dict[str, str]], input_name: str
) -> None:
    assert input_name in action_inputs, f"action.yml is missing input {input_name!r}"
    spec = action_inputs[input_name]
    assert spec.get("required") == "false", (
        f"{input_name} must be optional (required: false), got {spec.get('required')!r}"
    )
    assert "default" in spec, f"{input_name} must declare an explicit default"
    assert spec["default"] in {'""', "''"}, (
        f"{input_name} default must be an empty string on this branch, got {spec['default']!r}"
    )


def test_action_yml_defaults_reports_api_url_to_gitcalculator_host(
    action_inputs: dict[str, dict[str, str]],
) -> None:
    spec = action_inputs["reports-api-url"]
    assert spec.get("required") == "false"
    assert spec["default"] == _DEFAULT_REPORTS_API_URL


def _step_bodies(text: str) -> dict[str, str]:
    steps: dict[str, str] = {}
    current: str | None = None
    chunks: list[str] = []
    for line in text.splitlines():
        if line.startswith("    - name: "):
            if current is not None:
                steps[current] = "\n".join(chunks)
            current = line.split("    - name: ", 1)[1]
            chunks = [line]
        elif current is not None:
            chunks.append(line)
    if current is not None:
        steps[current] = "\n".join(chunks)
    return steps


def test_action_yml_has_no_separate_identity_token_input(
    action_inputs: dict[str, dict[str, str]],
) -> None:
    assert "reports-api-identity-token" not in action_inputs


def test_action_yml_defaults_write_weekly_metrics_json_to_false(
    action_inputs: dict[str, dict[str, str]],
) -> None:
    spec = action_inputs["write-weekly-metrics-json"]
    assert spec.get("required") == "false"
    assert spec["default"] == '"false"'


def test_action_yml_does_not_skip_post_when_only_the_env_var_is_set() -> None:
    text = _ACTION_YML.read_text(encoding="utf-8")
    assert "if: ${{ inputs.reports-api-key != '' }}" not in text
    assert "WRITE_WEEKLY_METRICS_JSON: ${{ inputs.reports-api-key != '' }}" not in text
    assert "WRITE_WEEKLY_METRICS_JSON: ${{ steps.reports_key.outputs.present }}" not in text


def test_action_yml_does_not_persist_a_secret_wipe_via_github_env() -> None:
    text = _ACTION_YML.read_text(encoding="utf-8")
    assert "GITHUB_ENV" not in text


def test_action_yml_detects_key_presence_before_pip_without_writing_the_key() -> None:
    text = _ACTION_YML.read_text(encoding="utf-8")
    steps = _step_bodies(text)
    detect = steps["Detect Reports API key"]
    pip = steps["Obtain pinned git_calculator"]
    post = steps["Optionally post to Reports API"]
    assert "--present" in detect
    assert "GITHUB_OUTPUT" in detect
    assert "--write-file" not in detect
    assert "--write-file" not in pip
    assert "--write-file" in post
    assert text.index("pip install") < text.index("--write-file")


def test_action_yml_withholds_secrets_from_untrusted_steps() -> None:
    steps = _step_bodies(_ACTION_YML.read_text(encoding="utf-8"))
    blanks = (
        'GIT_CALCULATOR_API_KEY: ""',
        'INPUT_REPORTS_API_KEY: ""',
    )
    for name in (
        "Set up Python",
        "Obtain pinned git_calculator",
        "Run calculator + allowlist gate",
    ):
        body = steps[name]
        for blank in blanks:
            assert blank in body, f"{name} must withhold {blank}"


def test_action_yml_post_inherits_the_caller_key_and_writes_weekly_metrics_from_presence() -> None:
    text = _ACTION_YML.read_text(encoding="utf-8")
    steps = _step_bodies(text)
    calculate = steps["Run calculator + allowlist gate"]
    post = steps["Optionally post to Reports API"]
    assert "WRITE_WEEKLY_METRICS_JSON: ${{ inputs.write-weekly-metrics-json }}" in calculate
    assert "HAS_REPORTS_API_KEY: ${{ steps.reports_key.outputs.present }}" in calculate
    assert "git-calculator-reports-api-key" not in calculate
    assert 'GIT_CALCULATOR_API_KEY: ""' not in post
    assert "REPORTS_API_KEY_INPUT: ${{ inputs.reports-api-key }}" in post
    assert "mktemp" in post


def test_run_step_writes_weekly_metrics_json_when_input_or_key_is_present() -> None:
    run_script = _composite_step_run("Run calculator + allowlist gate")
    assert (
        '[[ "${WRITE_WEEKLY_METRICS_JSON}" == "true" || "${HAS_REPORTS_API_KEY}" == "true" ]]'
        in run_script
    )


def _composite_step_run(step_name: str) -> str:
    text = _ACTION_YML.read_text(encoding="utf-8")
    marker = f"    - name: {step_name}\n"
    start = text.index(marker)
    chunk = text[start:]
    run_at = chunk.index("      run: |\n")
    body = chunk[run_at + len("      run: |\n") :]
    lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("    - "):
            break
        if line.startswith("        "):
            lines.append(line[8:])
            continue
        if line == "":
            lines.append("")
            continue
        break
    script = "\n".join(lines).strip() + "\n"
    assert script.strip(), f"no run script for step {step_name!r}"
    return script


def test_obtain_step_can_run_twice_in_one_job(tmp_path: Path) -> None:
    """RUNNER_TEMP is shared for the job. A second uses: must not clone into
    a dest that the first invocation already filled."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(remote), str(work)], check=True)
    (work / "README").write_text("x\n", encoding="utf-8")
    git_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(["git", "-C", str(work), "add", "README"], check=True, env=git_env)
    subprocess.run(
        ["git", "-C", str(work), "commit", "-m", "init"], check=True, env=git_env
    )
    subprocess.run(["git", "-C", str(work), "push", "origin", "HEAD"], check=True)
    sha = subprocess.check_output(
        ["git", "-C", str(work), "rev-parse", "HEAD"], text=True
    ).strip()

    runner_temp = tmp_path / "runner_temp"
    runner_temp.mkdir()
    script = _composite_step_run("Obtain pinned git_calculator")
    srcs: list[Path] = []
    env = {
        **os.environ,
        "RUNNER_TEMP": str(runner_temp),
        "CALC_REMOTE": str(remote),
        "CALC_REF": sha,
    }
    for i in range(2):
        github_output = tmp_path / f"github_output_{i}"
        github_output.write_text("", encoding="utf-8")
        env["GITHUB_OUTPUT"] = str(github_output)
        completed = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, (
            f"obtain step {i + 1} failed:\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
        output_text = github_output.read_text(encoding="utf-8")
        match = re.search(r"^src=(.+)$", output_text, re.MULTILINE)
        assert match, f"obtain step {i + 1} did not write src= to GITHUB_OUTPUT:\n{output_text}"
        srcs.append(Path(match.group(1)))
    assert srcs[0] != srcs[1]
    assert srcs[0].is_dir() and srcs[1].is_dir()


def _run_post_step(*, has_key: str, url: str) -> subprocess.CompletedProcess[str]:
    script = _composite_step_run("Optionally post to Reports API")
    env = {
        **os.environ,
        "HAS_REPORTS_API_KEY": has_key,
        "REPORTS_API_URL": url,
        "REPORTS_API_KEY_INPUT": "",
        "REPORTS_API_REPO": "",
        "OUTPUT_DIR": "unused",
        "GITHUB_ACTION_PATH": str(Path(__file__).resolve().parents[1]),
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
    }
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)


def test_post_step_rejects_http_url_even_without_a_key() -> None:
    completed = _run_post_step(has_key="false", url="http://127.0.0.1:1")
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "https" in completed.stderr
    assert "skipping POST" not in completed.stdout


def test_post_step_skips_post_when_https_url_and_no_key() -> None:
    completed = _run_post_step(has_key="false", url="https://gitcalculator.doubling.io")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "skipping POST" in completed.stdout


def test_run_step_uses_obtain_output_src() -> None:
    text = _ACTION_YML.read_text(encoding="utf-8")
    assert "id: obtain-calculator" in text
    assert "steps.obtain-calculator.outputs.src" in text
    run_script = _composite_step_run("Run calculator + allowlist gate")
    assert '--calculator-src "${CALC_SRC}"' in run_script
