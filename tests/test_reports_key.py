from __future__ import annotations

from pathlib import Path

import pytest

from reports_key import main


def test_env_var_is_enough_when_the_input_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CALCULATOR_API_KEY", "from-env")
    dest = tmp_path / "key"
    assert main(["--input", "", "--write-file", str(dest)]) == 0
    assert dest.read_text(encoding="utf-8") == "from-env"


def test_reports_api_key_input_overrides_the_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CALCULATOR_API_KEY", "from-env")
    dest = tmp_path / "key"
    assert main(["--input", "from-input", "--write-file", str(dest)]) == 0
    assert dest.read_text(encoding="utf-8") == "from-input"


def test_missing_key_is_reported_absent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GIT_CALCULATOR_API_KEY", raising=False)
    assert main(["--input", "", "--present"]) == 0
    assert capsys.readouterr().out.strip() == "false"


def test_present_check_does_not_print_the_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GIT_CALCULATOR_API_KEY", "super-secret")
    assert main(["--present"]) == 0
    captured = capsys.readouterr().out
    assert captured.strip() == "true"
    assert "super-secret" not in captured
