from __future__ import annotations

import dataclasses
import threading
import unittest
import urllib.request

from revival_stub.eventlog import EventLogger
from revival_stub.http_contract import (
    HttpContract,
    parse_enter_like_client,
    parse_server_list_like_client,
    versions_after_current_like_client,
)
from revival_stub.http_stub import RevivalHTTPServer
from revival_stub.sim_client import run_simulated_login
from revival_stub.tcp_stub import RevivalTCPServer


class FullMilestoneFlowTests(unittest.TestCase):
    def test_bootstrap_version_server_list_login_no_role(self) -> None:
        events: list[dict] = []
        logger = EventLogger(stream=None, sink=events.append)
        tcp_server = RevivalTCPServer(("127.0.0.1", 0), logger)
        tcp_thread = threading.Thread(target=tcp_server.serve_forever, daemon=True)
        tcp_thread.start()

        contract = HttpContract(
            http_port=0,
            tcp_host="127.0.0.1",
            tcp_port=tcp_server.server_address[1],
        )
        http_server = RevivalHTTPServer(("127.0.0.1", 0), contract, logger)
        contract = dataclasses.replace(contract, http_port=http_server.server_address[1])
        http_server.contract = contract
        http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        http_thread.start()

        try:
            entry_url = (
                f"http://127.0.0.1:{http_server.server_address[1]}"
                "/enter.txt?s=1&t=1700000000"
            )
            with urllib.request.urlopen(entry_url, timeout=2) as response:
                entries = parse_enter_like_client(response.read().decode("utf-8"))
            self.assertEqual(len(entries), 13)

            version_url = f"http://{entries[0]}/android/version.txt"
            head_request = urllib.request.Request(
                f"{version_url}?s=2&t=1700000001",
                method="HEAD",
            )
            with urllib.request.urlopen(head_request, timeout=2) as response:
                version_length = int(response.headers["Content-Length"])
            get_request = urllib.request.Request(
                f"{version_url}?s=3&t=1700000002",
                headers={"Range": f"bytes=0-{version_length}"},
            )
            with urllib.request.urlopen(get_request, timeout=2) as response:
                version_text = response.read().decode("utf-8")
            self.assertEqual(versions_after_current_like_client(version_text), [])

            with urllib.request.urlopen(
                f"http://{entries[1]}?s=4&t=1700000003", timeout=2
            ) as response:
                server_list = parse_server_list_like_client(
                    response.read().decode("utf-8")
                )
            selected = server_list["servers"][0]
            result = run_simulated_login(selected["host"], selected["port"])

            # Cross-check the native-recovered wire facts, not merely symmetry
            # between this project's encoder and decoder.
            self.assertEqual(result.login_wire[:3], bytes.fromhex("93 71 93"))
            self.assertEqual(result.response_wire, bytes.fromhex("96 71 93"))
            self.assertEqual(result.response_protocol_name, "noRole")
            self.assertEqual(
                [event["endpoint"] for event in events if event["event"] == "http_request"],
                ["enter", "version", "version", "server_list"],
            )
            http_events = [
                event for event in events if event["event"] == "http_request"
            ]
            self.assertEqual(
                [event["method"] for event in http_events],
                ["GET", "HEAD", "GET", "GET"],
            )
            self.assertEqual(
                http_events[2]["range_header"],
                f"bytes=0-{version_length}",
            )
        finally:
            http_server.shutdown()
            http_server.server_close()
            tcp_server.shutdown()
            tcp_server.server_close()
            http_thread.join(timeout=2)
            tcp_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

