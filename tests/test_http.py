from __future__ import annotations

import dataclasses
import socket
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from revival_stub.eventlog import EventLogger
from revival_stub.http_contract import (
    CURRENT_VERSION,
    HttpContract,
    inspect_patch_residue,
    parse_enter_like_client,
    parse_server_list_like_client,
    versions_after_current_like_client,
)
from revival_stub.http_stub import RevivalHTTPServer


class HttpContractTests(unittest.TestCase):
    def test_enter_has_exact_required_positions(self) -> None:
        contract = HttpContract(http_port=18080, tcp_port=16001)
        entries = parse_enter_like_client(contract.render_enter())
        self.assertEqual(len(entries), 13)
        self.assertEqual(entries[0], "127.0.0.1:18080/patch")
        self.assertEqual(entries[1], "127.0.0.1:18080/server-list.txt")
        self.assertTrue(all(entries[index] for index in range(2, 10)))
        self.assertEqual(entries[10], "")
        self.assertTrue(entries[11])
        self.assertEqual(entries[12], "P10441")

    def test_version_has_no_entries_after_current(self) -> None:
        contract = HttpContract()
        self.assertEqual(versions_after_current_like_client(contract.render_version()), [])
        self.assertIn(CURRENT_VERSION, contract.render_version())

    def test_version_parser_detects_future_patch(self) -> None:
        content = "{0.1.20.1#aaa#0}\n{0.1.20.2#bbb#123}\n"
        result = versions_after_current_like_client(content)
        self.assertEqual(result, [{"version": "0.1.20.2", "md5": "bbb", "size": 123}])

    def test_server_list_uses_full_recovered_grammar(self) -> None:
        contract = HttpContract(tcp_host="127.0.0.1", tcp_port=16001)
        parsed = parse_server_list_like_client(contract.render_server_list())
        self.assertEqual(parsed["recommended"], [1])
        self.assertEqual(parsed["voice"], {"$1": "127.0.0.1"})
        self.assertEqual(len(parsed["servers"]), 1)
        server = parsed["servers"][0]
        self.assertEqual(server["name"], "幻世单机")
        self.assertEqual((server["host"], server["port"]), ("127.0.0.1", 16001))
        self.assertEqual(server["status"], 1)

    def test_patch_residue_check_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / f"{CURRENT_VERSION}.zip"
            candidate.write_bytes(b"do-not-delete")
            status = inspect_patch_residue(Path(directory))
            self.assertIs(status["exists"], True)
            self.assertEqual(candidate.read_bytes(), b"do-not-delete")

    def test_http_entry_accepts_stamp_and_time_query(self) -> None:
        events: list[dict] = []
        logger = EventLogger(stream=None, sink=events.append)
        contract = HttpContract(http_port=0, tcp_port=16001)
        server = RevivalHTTPServer(("127.0.0.1", 0), contract, logger)
        contract = dataclasses.replace(contract, http_port=server.server_address[1])
        server.contract = contract
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/enter.txt?s=1&t=1700000000"
            with urllib.request.urlopen(url, timeout=2) as response:
                body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertEqual(len(parse_enter_like_client(body)), 13)
            request_event = next(event for event in events if event["event"] == "http_request")
            self.assertEqual(request_event["query"], {"s": ["1"], "t": ["1700000000"]})
            event_names = [event["event"] for event in events]
            self.assertLess(
                event_names.index("http_connection_accepted"),
                event_names.index("http_request"),
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_version_head_matches_get_content_length(self) -> None:
        events: list[dict] = []
        logger = EventLogger(stream=None, sink=events.append)
        contract = HttpContract(http_port=0, tcp_port=16001)
        server = RevivalHTTPServer(("127.0.0.1", 0), contract, logger)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = (
                f"http://127.0.0.1:{server.server_address[1]}"
                "/patch/android/version.txt?s=2&t=1700000001"
            )
            request = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(request, timeout=2) as response:
                body = response.read()
                content_length = int(response.headers["Content-Length"])
            self.assertEqual(response.status, 200)
            self.assertEqual(body, b"")
            self.assertEqual(
                content_length,
                len(contract.render_version().encode("utf-8")),
            )
            request_event = next(
                event
                for event in events
                if event["event"] == "http_request"
            )
            self.assertEqual(request_event["method"], "HEAD")
            self.assertEqual(request_event["endpoint"], "version")
            self.assertEqual(request_event["status"], 200)
            self.assertEqual(
                request_event["query"],
                {"s": ["2"], "t": ["1700000001"]},
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_http_accept_is_logged_when_client_closes_before_request(self) -> None:
        events: list[dict] = []
        logger = EventLogger(stream=None, sink=events.append)
        server = RevivalHTTPServer(
            ("127.0.0.1", 0), HttpContract(http_port=0), logger
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with socket.create_connection(server.server_address, timeout=2):
                pass
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if any(event["event"] == "http_handler_finished" for event in events):
                    break
                time.sleep(0.01)
            event_names = [event["event"] for event in events]
            self.assertIn("http_connection_accepted", event_names)
            self.assertIn("http_handler_started", event_names)
            self.assertIn("http_handler_finished", event_names)
            self.assertNotIn("http_request", event_names)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

