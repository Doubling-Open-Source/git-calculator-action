"""POST weekly_metrics.json to the Reports API. Never print secrets."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def _read_secret_file(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\n", "")


def _redact(text: str, secrets: tuple[str, ...]) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Do not follow 3xx; credentialed POSTs fail closed instead of hopping hosts."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def require_https_url(url: str) -> None:
    """Fail closed unless the Reports API base URL is https."""
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("reports-api-url must be an https URL")


def post_summary(
    *,
    url: str,
    api_key: str,
    repo: str,
    metrics: dict[str, Any],
) -> tuple[int, dict[str, str], dict[str, Any] | str]:
    """Return (status, response headers, parsed JSON or raw text)."""
    require_https_url(url)
    endpoint = f"{url.rstrip('/')}/v1/reports"
    payload = json.dumps({"reportType": "summary", "repo": repo, "data": metrics}).encode("utf-8")
    headers = {
        "content-type": "application/json",
        "x-api-key": api_key,
    }
    request = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
    opener = urllib.request.build_opener(_NoRedirectHandler)
    try:
        with opener.open(request, timeout=60) as response:
            body = response.read().decode("utf-8")
            header_map = {k.lower(): v for k, v in response.headers.items()}
            try:
                parsed: dict[str, Any] | str = json.loads(body)
            except json.JSONDecodeError:
                parsed = body
            return response.status, header_map, parsed
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8") if err.fp else ""
        header_map = {k.lower(): v for k, v in err.headers.items()} if err.headers else {}
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = body
        return err.code, header_map, parsed


def write_markdown(markdown: str, *, out_file: Path | None, step_summary: Path | None) -> None:
    if out_file is not None:
        out_file.write_text(markdown, encoding="utf-8")
    if step_summary is not None:
        with step_summary.open("a", encoding="utf-8") as handle:
            handle.write(markdown)
            if not markdown.endswith("\n"):
                handle.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="POST weekly_metrics.json to the Reports API")
    parser.add_argument("--url", required=True, help="Reports API base URL")
    parser.add_argument(
        "--check-url",
        action="store_true",
        help="Validate --url is https with a host, then exit",
    )
    parser.add_argument("--key-file", type=Path, help="File containing the API key")
    parser.add_argument("--repo", help="owner/name posted as the request repo")
    parser.add_argument("--metrics-file", type=Path, help="weekly_metrics.json path")
    parser.add_argument("--out-file", type=Path, help="Write returned markdown here")
    args = parser.parse_args(argv)

    try:
        require_https_url(args.url)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.check_url:
        return 0
    if args.key_file is None or args.repo is None or args.metrics_file is None:
        print("error: --key-file, --repo, and --metrics-file are required", file=sys.stderr)
        return 1

    api_key = _read_secret_file(args.key_file)
    if not api_key:
        print("error: API key file is empty", file=sys.stderr)
        return 1
    secrets = (api_key,)
    metrics = json.loads(args.metrics_file.read_text(encoding="utf-8"))

    status, headers, parsed = post_summary(
        url=args.url,
        api_key=api_key,
        repo=args.repo,
        metrics=metrics,
    )

    if status == 429:
        retry_after = headers.get("retry-after", "")
        print(
            f"Reports API rate-limited (HTTP 429). Retry-After: {retry_after or 'missing'}",
            file=sys.stderr,
        )
        return 1
    if status != 200:
        detail = parsed if isinstance(parsed, str) else json.dumps(parsed)
        print(
            f"Reports API request failed (HTTP {status}): {_redact(detail, secrets)}",
            file=sys.stderr,
        )
        return 1
    if not isinstance(parsed, dict) or not isinstance(parsed.get("markdown"), str):
        print("error: Reports API 200 response missing markdown", file=sys.stderr)
        return 1

    step_summary = Path(os.environ["GITHUB_STEP_SUMMARY"]) if os.environ.get("GITHUB_STEP_SUMMARY") else None
    write_markdown(_redact(parsed["markdown"], secrets), out_file=args.out_file, step_summary=step_summary)
    print("Posted weekly_metrics.json; wrote Reports API markdown to the job summary")
    return 0


if __name__ == "__main__":
    sys.exit(main())
