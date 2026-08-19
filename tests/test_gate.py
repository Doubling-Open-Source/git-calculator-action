from __future__ import annotations

from pathlib import Path

import pytest

from gate import gate_output_dir

ALLOWLIST = Path(__file__).resolve().parents[1] / "allowlist.json"
BUNDLE = (
    "window_metadata.json",
    "commit_volume.csv",
    "cycle_time.csv",
    "change_failure_rate.csv",
)


def _bundle(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name in BUNDLE:
        (out / name).write_text("{}\n", encoding="utf-8")


def test_gate_passes_on_the_standardized_bundle(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _bundle(out)
    gate_output_dir(out, ALLOWLIST)


def test_gate_fails_closed_on_an_extra_author_csv(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _bundle(out)
    (out / "commit_alice_commits.csv").write_text("x\n", encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        gate_output_dir(out, ALLOWLIST)
    assert ei.value.code == 1


def test_gate_fails_closed_on_a_nested_extra(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _bundle(out)
    nested = out / "subdir"
    nested.mkdir()
    (nested / "secret.txt").write_text("nope", encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        gate_output_dir(out, ALLOWLIST)
    assert ei.value.code == 1


def test_gate_fails_closed_when_output_dir_is_missing(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as ei:
        gate_output_dir(tmp_path / "missing", ALLOWLIST)
    assert ei.value.code == 1
