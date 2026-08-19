from __future__ import annotations

import json
import ssl
import subprocess
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import pytest

from post_reports import main, post_summary


class _Handler(BaseHTTPRequestHandler):
    captured: dict = {}
    status = 200
    body = b'{"markdown":"## git-calculator \\u2014 weekly metrics\\n"}'
    extra_headers: dict[str, str] = {}

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(length)
        type(self).captured = {
            "path": self.path,
            "api_key": self.headers.get("x-api-key"),
            "authorization": self.headers.get("Authorization"),
            "body": json.loads(raw.decode("utf-8")) if raw else {},
        }
        self.send_response(self.status)
        self.send_header("content-type", "application/json")
        for key, value in self.extra_headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(self.body)


def _self_signed_cert(tmp_path: Path) -> tuple[Path, Path]:
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-nodes",
            "-subj",
            "/CN=127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


def _wrap_tls(server: ThreadingHTTPServer, cert: Path, key: Path) -> None:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
    server.socket = ctx.wrap_socket(server.socket, server_side=True)


@pytest.fixture
def https_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ssl, "_create_default_https_context", ssl._create_unverified_context)
    cert, key = _self_signed_cert(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    _wrap_tls(server, cert, key)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join(timeout=2)


def _https_url(server: ThreadingHTTPServer) -> str:
    return f"https://127.0.0.1:{server.server_address[1]}"


def test_http_url_fails_closed_without_posting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _Handler.captured = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    key_file = tmp_path / "key"
    key_file.write_text("super-secret-key", encoding="utf-8")
    metrics_file = tmp_path / "weekly_metrics.json"
    metrics_file.write_text("{}\n", encoding="utf-8")
    try:
        code = main(
            [
                "--url",
                f"http://127.0.0.1:{server.server_address[1]}",
                "--key-file",
                str(key_file),
                "--repo",
                "acme/widgets",
                "--metrics-file",
                str(metrics_file),
            ]
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
    captured = capsys.readouterr()
    assert code == 1
    assert "https" in captured.err
    assert "super-secret-key" not in captured.err
    assert _Handler.captured == {}


def test_https_url_without_a_host_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key_file = tmp_path / "key"
    key_file.write_text("super-secret-key", encoding="utf-8")
    metrics_file = tmp_path / "weekly_metrics.json"
    metrics_file.write_text("{}\n", encoding="utf-8")
    code = main(
        [
            "--url",
            "https://:443",
            "--key-file",
            str(key_file),
            "--repo",
            "acme/widgets",
            "--metrics-file",
            str(metrics_file),
        ]
    )
    captured = capsys.readouterr()
    assert code == 1
    assert "https" in captured.err
    assert "super-secret-key" not in captured.err


def test_post_sends_a_summary_report(https_server) -> None:
    _Handler.status = 200
    _Handler.extra_headers = {}
    _Handler.body = b'{"markdown":"## git-calculator\\n"}'
    metrics = {"schema_version": 7}
    status, _, parsed = post_summary(
        url=_https_url(https_server),
        api_key="secret-key",
        repo="acme/widgets",
        metrics=metrics,
    )
    assert status == 200
    assert _Handler.captured["path"] == "/v1/reports"
    assert _Handler.captured["api_key"] == "secret-key"
    assert _Handler.captured["authorization"] is None
    assert _Handler.captured["body"]["reportType"] == "summary"
    assert _Handler.captured["body"]["repo"] == "acme/widgets"
    assert _Handler.captured["body"]["data"] == metrics
    assert isinstance(parsed, dict)
    assert "markdown" in parsed


def test_api_key_is_not_written_to_stdout(
    https_server, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _Handler.status = 200
    _Handler.extra_headers = {}
    _Handler.body = b'{"markdown":"## git-calculator\\n"}'
    key_file = tmp_path / "key"
    key_file.write_text("super-secret-key", encoding="utf-8")
    metrics_file = tmp_path / "weekly_metrics.json"
    metrics_file.write_text(json.dumps({"schema_version": 7}), encoding="utf-8")
    out_file = tmp_path / "summary.md"
    code = main(
        [
            "--url",
            _https_url(https_server),
            "--key-file",
            str(key_file),
            "--repo",
            "acme/widgets",
            "--metrics-file",
            str(metrics_file),
            "--out-file",
            str(out_file),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "super-secret-key" not in captured.out
    assert "super-secret-key" not in captured.err
    assert out_file.read_text(encoding="utf-8").startswith("## git-calculator")


def test_rate_limit_surfaces_retry_after(
    https_server, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _Handler.status = 429
    _Handler.extra_headers = {"Retry-After": "12"}
    _Handler.body = b'{"error":"rate limit exceeded"}'
    key_file = tmp_path / "key"
    key_file.write_text("super-secret-key", encoding="utf-8")
    metrics_file = tmp_path / "weekly_metrics.json"
    metrics_file.write_text("{}\n", encoding="utf-8")
    code = main(
        [
            "--url",
            _https_url(https_server),
            "--key-file",
            str(key_file),
            "--repo",
            "acme/widgets",
            "--metrics-file",
            str(metrics_file),
        ]
    )
    captured = capsys.readouterr()
    assert code == 1
    assert "Retry-After: 12" in captured.err
    assert "super-secret-key" not in captured.err


def test_response_markdown_does_not_write_the_api_key_to_outputs(
    https_server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api_key = "super-secret-key"
    _Handler.status = 200
    _Handler.extra_headers = {}
    _Handler.body = json.dumps({"markdown": f"hello {api_key}\n"}).encode("utf-8")
    key_file = tmp_path / "key"
    key_file.write_text(api_key, encoding="utf-8")
    metrics_file = tmp_path / "weekly_metrics.json"
    metrics_file.write_text("{}\n", encoding="utf-8")
    out_file = tmp_path / "summary.md"
    step_summary = tmp_path / "step-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))
    code = main(
        [
            "--url",
            _https_url(https_server),
            "--key-file",
            str(key_file),
            "--repo",
            "acme/widgets",
            "--metrics-file",
            str(metrics_file),
            "--out-file",
            str(out_file),
        ]
    )
    assert code == 0
    for text in (out_file.read_text(encoding="utf-8"), step_summary.read_text(encoding="utf-8")):
        assert api_key not in text
        assert "[redacted]" in text


@contextmanager
def _cross_origin_redirect(tmp_path: Path) -> Iterator[tuple[str, list[dict[str, str | None]]]]:
    second_hits: list[dict[str, str | None]] = []
    cert, key = _self_signed_cert(tmp_path)

    class SecondHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("content-length", "0"))
            if length:
                self.rfile.read(length)
            second_hits.append(
                {
                    "api_key": self.headers.get("x-api-key"),
                    "authorization": self.headers.get("Authorization"),
                }
            )
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"markdown":"should-not-write"}')

        do_GET = do_POST

    class FirstHandler(BaseHTTPRequestHandler):
        location = ""

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("content-length", "0"))
            if length:
                self.rfile.read(length)
            self.send_response(302)
            self.send_header("Location", type(self).location)
            self.end_headers()

    first = ThreadingHTTPServer(("127.0.0.1", 0), FirstHandler)
    second = ThreadingHTTPServer(("127.0.0.1", 0), SecondHandler)
    _wrap_tls(first, cert, key)
    _wrap_tls(second, cert, key)
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True)
        for server in (first, second)
    ]
    for thread in threads:
        thread.start()
    FirstHandler.location = f"https://127.0.0.1:{second.server_address[1]}/v1/reports"
    try:
        yield f"https://127.0.0.1:{first.server_address[1]}", second_hits
    finally:
        for server in (first, second):
            server.shutdown()
        for thread in threads:
            thread.join(timeout=2)


def test_credentialed_post_does_not_follow_redirects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ssl, "_create_default_https_context", ssl._create_unverified_context)
    key_file = tmp_path / "key"
    key_file.write_text("super-secret-key", encoding="utf-8")
    metrics_file = tmp_path / "weekly_metrics.json"
    metrics_file.write_text("{}\n", encoding="utf-8")
    with _cross_origin_redirect(tmp_path) as (url, second_hits):
        code = main(
            [
                "--url",
                url,
                "--key-file",
                str(key_file),
                "--repo",
                "acme/widgets",
                "--metrics-file",
                str(metrics_file),
            ]
        )
    assert code != 0
    assert second_hits == []
