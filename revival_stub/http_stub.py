"""Loopback HTTP server for bootstrap, version, and server-list documents."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .eventlog import EventLogger
from .http_contract import HttpContract, PRODUCTION_SHAPED_ENTRY_RE


class RevivalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, contract: HttpContract, event_logger: EventLogger):
        self.contract = contract
        self.event_logger = event_logger
        super().__init__(address, RevivalHTTPRequestHandler)

    def get_request(self):
        request, client_address = super().get_request()
        self.event_logger.emit(
            "http_connection_accepted",
            remote_endpoint=f"{client_address[0]}:{client_address[1]}",
        )
        return request, client_address


class RevivalHTTPRequestHandler(BaseHTTPRequestHandler):
    server: RevivalHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return

    def handle(self) -> None:
        self.server.event_logger.emit(
            "http_handler_started",
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
        )
        try:
            super().handle()
        finally:
            self.server.event_logger.emit(
                "http_handler_finished",
                remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
                close_connection=self.close_connection,
            )

    def _route_get(self, path: str) -> tuple[HTTPStatus, str, str, str]:
        contract = self.server.contract
        if path == "/":
            return (
                HTTPStatus.OK,
                contract.render_server_list(),
                "text/plain; charset=utf-8",
                "server_list_root",
            )
        if path == "/enter.txt" or PRODUCTION_SHAPED_ENTRY_RE.match(path):
            return HTTPStatus.OK, contract.render_enter(), "text/plain; charset=utf-8", "enter"
        if path == "/patch/android/version.txt":
            return HTTPStatus.OK, contract.render_version(), "text/plain; charset=utf-8", "version"
        if path == "/server-list.txt":
            return HTTPStatus.OK, contract.render_server_list(), "text/plain; charset=utf-8", "server_list"
        if path == "/entire-version.txt":
            return HTTPStatus.OK, contract.render_entire_version(), "text/plain; charset=utf-8", "entire_version"
        if path.startswith("/noop/"):
            return HTTPStatus.OK, "{}\n", "text/plain; charset=utf-8", "safe_noop"
        return HTTPStatus.NOT_FOUND, "not found\n", "text/plain; charset=utf-8", "not_found"

    def _send(
        self,
        status: HTTPStatus,
        body: str,
        content_type: str,
        *,
        send_body: bool = True,
    ) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if send_body:
            self.wfile.write(encoded)

    def _log_http_request(
        self,
        *,
        method: str,
        path: str,
        query: dict[str, list[str]],
        endpoint: str,
        status: HTTPStatus,
    ) -> None:
        self.server.event_logger.emit(
            "http_request",
            remote_endpoint=f"{self.client_address[0]}:{self.client_address[1]}",
            method=method,
            path=path,
            query=query,
            range_header=self.headers.get("Range"),
            endpoint=endpoint,
            status=status.value,
        )

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        status, body, content_type, endpoint = self._route_get(parsed.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        self._log_http_request(
            method="GET",
            path=parsed.path,
            query=query,
            endpoint=endpoint,
            status=status,
        )
        self._send(status, body, content_type)

    def do_HEAD(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        status, body, content_type, endpoint = self._route_get(parsed.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        self._log_http_request(
            method="HEAD",
            path=parsed.path,
            query=query,
            endpoint=endpoint,
            status=status,
        )
        self._send(status, body, content_type, send_body=False)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        is_noop = parsed.path.startswith("/noop/")
        status = HTTPStatus.OK if is_noop else HTTPStatus.NOT_FOUND
        self._log_http_request(
            method="POST",
            path=parsed.path,
            query=parse_qs(parsed.query, keep_blank_values=True),
            endpoint="safe_noop" if is_noop else "not_found",
            status=status,
        )
        self._send(status, "{}\n" if is_noop else "not found\n", "text/plain; charset=utf-8")

