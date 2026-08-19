"""Resolve the Reports API key from an optional input or GIT_CALCULATOR_API_KEY."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

ENV_NAME = "GIT_CALCULATOR_API_KEY"


def resolve_reports_api_key(
    input_key: str = "",
    environ: Mapping[str, str] | None = None,
) -> str:
    env = os.environ if environ is None else environ
    return (input_key or "").strip() or (env.get(ENV_NAME) or "").strip()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve the Reports API key without printing it"
    )
    parser.add_argument("--input", default="")
    parser.add_argument(
        "--present",
        action="store_true",
        help="Print true/false; never print the key",
    )
    parser.add_argument("--write-file", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    key = resolve_reports_api_key(args.input)
    if args.present:
        print("true" if key else "false")
        return 0
    if args.write_file is not None:
        args.write_file.write_text(key, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
