from http.client import HTTPConnection, IncompleteRead, RemoteDisconnected
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from secrets import token_urlsafe
import socket
import sys
from pathlib import Path
from threading import Thread
import unittest
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


PREFIX = "/role/test"
MESSAGES = PREFIX + "/client/v1/messages"
EVENTS = PREFIX + "/client/v1/stream"
CHUNKED = PREFIX + "/client/v1/chunked"


class Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        if self.headers.get("Transfer-Encoding") == "chunked":
            chunks = []
            while size := int(self.rfile.readline(), 16):
                chunks.append(self.rfile.read(size))
                self.rfile.read(2)
            self.rfile.read(2)
            body = b"".join(chunks)
        else:
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.server.requests.append((self.command, self.path, self.headers, body))
        self.send_response(201)
        self.send_header("Content-Length", "7" if self.path.endswith("/incomplete") else "6")
        self.end_headers()
        self.wfile.write(b"stored")
        self.close_connection = True

    def do_GET(self):
        self.server.requests.append((self.command, self.path, self.headers, b""))
        if self.path == EVENTS:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(b"a\r\nevent: a\n\n\r\n0\r\n\r\n")
        else:
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, *args):
        pass


class ProxyTests(unittest.TestCase):
    def setUp(self):
        from acceptance.fault_proxy import FaultProxy

        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        self.upstream.requests = []
        self.thread = Thread(target=self.upstream.serve_forever, kwargs={"poll_interval": 0.02})
        self.thread.start()
        self.addCleanup(self.upstream.server_close)
        self.addCleanup(self.thread.join)
        self.addCleanup(self.upstream.shutdown)
        self.upstream_url = f"http://127.0.0.1:{self.upstream.server_port}{PREFIX}"
        self.proxy = self.enter_context(FaultProxy(self.upstream_url))

    def enter_context(self, context):
        result = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return result

    def connection(self, proxy=None):
        url = urlsplit((proxy or self.proxy).base_url)
        self.assertEqual(url.path, PREFIX)
        connection = HTTPConnection(url.hostname, url.port, timeout=3)
        self.addCleanup(connection.close)
        return connection

    def request(self, method, path, body=None, headers=None, proxy=None):
        connection = self.connection(proxy)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read()

    def test_rejects_private_and_noncanonical_paths_before_forwarding(self):
        for path in (
            "/__test/events",
            PREFIX + "/__test/events",
            PREFIX + "/client/v1",
            PREFIX + "/client/v10/messages",
            PREFIX + "/client/v1/../__test/events",
            PREFIX + "/client/v1/%2e%2e/__test/events",
            PREFIX + "/client/v1/%252e%252e/__test/events",
            PREFIX + "/client/v1/__test/events",
            PREFIX + "/client/v1//messages",
            PREFIX + "/client/v1/\\messages",
            "/" + MESSAGES,
            "http://127.0.0.1:9" + MESSAGES,
            PREFIX + "/agents/v1/meta",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)
        self.assertEqual(self.upstream.requests, [])

    def test_forward_preserves_target_and_end_to_end_headers(self):
        target = MESSAGES + "?q=a%2Fb&x=1"
        headers = {
            "Host": "unrelated.invalid:5555",
            "Authorization": f"Bearer {token_urlsafe(16)}",
            "Content-Type": "application/json",
            "X-Yuki-Entry-Binding": "synthetic-binding",
            "Connection": "keep-alive, X-Hop",
            "X-Hop": "discard",
        }
        self.assertEqual(self.request("POST", target, b"{}", headers), (201, b"stored"))
        method, path, received_headers, body = self.upstream.requests[0]
        self.assertEqual((method, path, body), ("POST", target, b"{}"))
        for name in ("Authorization", "Content-Type", "X-Yuki-Entry-Binding"):
            self.assertEqual(received_headers[name], headers[name])
        self.assertEqual(received_headers["Host"], f"127.0.0.1:{self.upstream.server_port}")
        self.assertIsNone(received_headers["X-Hop"])

    def test_agent_routes_need_explicit_gate(self):
        proxy = self.enter_context(type(self.proxy)(self.upstream_url, allow_agents=True))
        operation = PREFIX + "/agents/v1/operations/client-answer:device-1:request-1"
        self.assertEqual(self.request("GET", operation)[0], 404)
        self.assertEqual(self.request("GET", operation, proxy=proxy)[0], 200)
        self.assertEqual(self.request("GET", "/agents/v1/meta", proxy=proxy)[0], 404)
        self.assertEqual(self.request("GET", PREFIX + "/__test/events", proxy=proxy)[0], 404)
        self.assertEqual([request[1] for request in self.upstream.requests], [operation])

    def test_before_forward_drops_one_matching_request_with_zero_upstream_forwards(self):
        self.proxy.arm("POST", MESSAGES, "drop-before-forward")
        self.assertEqual(self.request("GET", MESSAGES)[0], 200)
        with self.assertRaises(RemoteDisconnected):
            self.request("POST", MESSAGES, b"first")
        self.assertEqual(self.proxy.forwardings("POST", MESSAGES), ())
        self.assertEqual(len(self.upstream.requests), 1)
        self.assertEqual(self.request("POST", MESSAGES, b"second"), (201, b"stored"))
        self.assertEqual(self.upstream.requests[-1][3], b"second")
        self.assertEqual(len(self.proxy.forwardings("POST", MESSAGES)), 1)

    def test_after_upstream_reads_full_body_before_tcp_disconnect(self):
        self.proxy.arm("POST", MESSAGES, "drop-after-upstream")
        with self.assertRaises(RemoteDisconnected):
            self.request("POST", MESSAGES, b"original")
        transfer, = self.proxy.forwardings("POST", MESSAGES)
        self.assertEqual(len(self.upstream.requests), 1)
        self.assertEqual(transfer.upstream_status, 201)
        self.assertTrue(transfer.full_body_read)
        self.assertLessEqual(transfer.forwarded_at, transfer.upstream_finished_at)
        self.assertEqual(self.request("POST", MESSAGES, b"again"), (201, b"stored"))

    def test_after_upstream_can_return_503_unknown_and_checks_framing(self):
        self.proxy.arm("POST", MESSAGES, "drop-after-upstream", response="http-503")
        status, body = self.request("POST", MESSAGES)
        self.assertEqual((status, json.loads(body)), (503, {"problem": {"code": "unknown"}}))
        transfer, = self.proxy.forwardings("POST", MESSAGES)
        self.assertEqual(transfer.upstream_status, 201)
        self.assertTrue(transfer.full_body_read)
        self.assertEqual(len(self.upstream.requests), 1)

        route = MESSAGES + "/incomplete"
        self.proxy.arm("POST", route, "drop-after-upstream", response="http-503")
        self.assertEqual(self.request("POST", route)[0], 502)
        transfer, = self.proxy.forwardings("POST", route)
        self.assertFalse(transfer.full_body_read)

    def test_rejects_conflicting_request_framing_at_proxy(self):
        self.assertEqual(
            self.request(
                "POST", MESSAGES, b"0\r\n\r\n",
                {"Transfer-Encoding": "chunked", "Content-Length": "5"},
            )[0],
            400,
        )
        self.assertEqual(self.proxy.forwardings("POST", MESSAGES), ())
        self.assertEqual(self.upstream.requests, [])

    def test_chunked_upload_is_forwarded(self):
        connection = self.connection()
        connection.request("POST", CHUNKED, [b"abc", b"def"], encode_chunked=True)
        response = connection.getresponse()
        self.assertEqual((response.status, response.read()), (201, b"stored"))
        self.assertEqual(self.upstream.requests[0][3], b"abcdef")

    def test_sse_stream_sends_chunks_and_can_disconnect_mid_event(self):
        self.assertEqual(self.request("GET", EVENTS), (200, b"event: a\n\n"))
        self.proxy.arm("GET", EVENTS, "cut-sse", byte_limit=4)
        connection = self.connection()
        connection.request("GET", EVENTS)
        response = connection.getresponse()
        self.assertEqual(response.getheader("Content-Type"), "text/event-stream")
        with self.assertRaises(IncompleteRead) as caught:
            response.read()
        self.assertEqual(caught.exception.partial, b"even")
        self.assertFalse(self.proxy.forwardings("GET", EVENTS)[-1].full_body_read)

        self.proxy.arm("GET", EVENTS, "disconnect-sse")
        with self.assertRaises(IncompleteRead) as caught:
            self.request("GET", EVENTS)
        self.assertEqual(caught.exception.partial, b"")

    def test_delay_applies_to_the_armed_request_once(self):
        self.proxy.arm("POST", MESSAGES, "delay", seconds=0.05)
        self.assertEqual(self.request("POST", MESSAGES)[0], 201)
        delayed, = self.proxy.forwardings("POST", MESSAGES)
        self.assertGreaterEqual(delayed.duration_before_forward, 0.05)
        self.assertEqual(self.request("POST", MESSAGES)[0], 201)
        self.assertLess(self.proxy.forwardings("POST", MESSAGES)[1].duration_before_forward, 0.05)

    def test_listens_only_on_ipv4_loopback(self):
        self.assertEqual(urlsplit(self.proxy.base_url).hostname, "127.0.0.1")
        with socket.create_connection(("127.0.0.1", urlsplit(self.proxy.base_url).port), timeout=3):
            pass


if __name__ == "__main__":
    unittest.main()
