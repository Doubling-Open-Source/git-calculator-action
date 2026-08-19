from __future__ import annotations

import argparse
import json
import sys
from fnmatch import fnmatch
from pathlib import Path


def load_allowlist(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    patterns = data.get("patterns")
    if not isinstance(patterns, list) or not patterns:
        raise ValueError(f"allowlist must contain non-empty patterns list: {path}")
    return [str(p) for p in patterns]


def collect_relative_paths(output_dir: Path) -> set[str]:
    paths: set[str] = set()
    for p in output_dir.rglob("*"):
        if p.is_file():
            paths.add(p.relative_to(output_dir).as_posix())
    return paths


def is_allowed(rel_path: str, patterns: list[str]) -> bool:
    return any(fnmatch(rel_path, pat) for pat in patterns)


def gate_output_dir(output_dir: Path, allowlist_path: Path) -> None:
    if not output_dir.is_dir():
        print(f"gate: output dir missing or not a directory: {output_dir}", file=sys.stderr)
        raise SystemExit(1)
    patterns = load_allowlist(allowlist_path)
    rels = collect_relative_paths(output_dir)
    extras = sorted(r for r in rels if not is_allowed(r, patterns))
    if extras:
        print("gate: non-allowlisted paths:", file=sys.stderr)
        for e in extras:
            print(f"  - {e}", file=sys.stderr)
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Fail closed if output dir has non-allowlisted files")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=Path(__file__).resolve().parent / "allowlist.json",
    )
    args = parser.parse_args(argv)
    gate_output_dir(args.output_dir, args.allowlist)


if __name__ == "__main__":
    main()
