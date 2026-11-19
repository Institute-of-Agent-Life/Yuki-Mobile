from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from http.client import HTTPConnection, HTTPException, IncompleteRead
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
import socket
from threading import Event, Lock, Thread
from time import monotonic

from acceptance.backend import LOOPBACK, canonical_path, loopback_endpoint


_IO_TIMEOUT = 30
_BUFFER_SIZE = 65536
_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection",
    "te", "trailer", "transfer-encoding", "upgrade",
}
_METHODS = {"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"}


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class _Rule:
    fault: str
    response: str
    seconds: float
    byte_limit: int


@dataclass
class Transfer:
    """Timestamps and completion state observed by the proxy, without request or response data."""

    forwarded_at: str
    duration_before_forward: float
    upstream_status: int = None
    upstream_finished_at: str = None
    duration_upstream: float = None
    full_body_read: bool = False


class FaultProxy:
    def __init__(self, backend_base_url, *, port=0, allow_agents=False):
        self._upstream_port, self._prefix = loopback_endpoint(backend_base_url)
        self._port = port
        self._namespaces = ("/client/v1/", "/agents/v1/") if allow_agents else ("/client/v1/",)
        self._rules = {}
        self._transfers = defaultdict(list)
        self._lock = Lock()
        self._closed = Event()

    def __enter__(self):
        if self._closed.is_set():
            raise RuntimeError("Create a new proxy to listen again.")
        self._listener = _ProxyServer((LOOPBACK, self._port), _Handler)
        self._listener.proxy = self
        self.base_url = f"http://{LOOPBACK}:{self._listener.server_port}{self._prefix}"
        self._thread = Thread(
            target=self._listener.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._closed.set()
        self._listener.shutdown()
        self._listener.server_close()
        self._thread.join()

    def _route(self, target):
        path = target.partition("?")[0]
        if (
            "#" in target
            or any(not 33 <= ord(char) <= 126 for char in target)
            or not canonical_path(path)
            or "__test" in path.split("/")
            or not any(path.startswith(self._prefix + namespace) for namespace in self._namespaces)
        ):
            return None
        return path

    def arm(self, method, path, fault, *, response="disconnect", seconds=0, byte_limit=0):
        """Consume a rule on the next exact method and path match, irrespective of query."""
        if method not in _METHODS or self._route(path) != path:
            raise ValueError("Fault rule needs an allowed method and path.")
        if (
            fault not in {"drop-before-forward", "drop-after-upstream", "delay", "cut-sse", "disconnect-sse"}
            or response not in {"disconnect", "http-503"}
            or (response == "http-503" and fault != "drop-after-upstream")
            or not (0 <= seconds <= 10)
            or not (0 <= byte_limit <= _BUFFER_SIZE)
            or (fault == "cut-sse" and byte_limit == 0)
        ):
            raise ValueError("Invalid fault rule.")
        with self._lock:
            if (method, path) in self._rules:
                raise ValueError("A fault rule is already armed for this route.")
            self._rules[method, path] = _Rule(fault, response, seconds, byte_limit)

    def forwardings(self, method, path):
        with self._lock:
            return tuple(replace(item) for item in self._transfers.get((method, path), ()))


class _ProxyServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # Request paths and exception messages can contain fixture data.
        pass


class _BadRequest(Exception):
    pass


def _header_length(headers):
    lengths = headers.get_all("Content-Length", [])
    if not lengths:
        return None
    if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,20}", lengths[0]):
        raise _BadRequest
    return int(lengths[0])


def _end_to_end(headers):
    connection_tokens = {
        token.strip().lower()
        for header in headers.get_all("Connection", [])
        for token in header.split(",")
    }
    excluded = _HOP_HEADERS | connection_tokens | {"content-length"}
    for name, value in headers.items():
        if name.lower() not in excluded:
            yield name, value


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        self.request.settimeout(_IO_TIMEOUT)
        super().setup()

    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        # BaseHTTPRequestHandler may pass request data as the error description.
        if code == 501:
            code = 405 if self._route() else 404
        self._reply(code)

    def _reply(self, status, body=b""):
        self.close_connection = True
        self.send_response_only(status)
        self.send_header("Connection", "close")
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _disconnect(self):
        self.close_connection = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _target(self):
        # The base handler normalizes leading double slashes before storing self.path.
        fields = self.requestline.split()
        return fields[1] if len(fields) >= 2 else ""

    def _route(self):
        return self.server.proxy._route(self._target())

    def handle_expect_100(self):
        if self._route():
            return super().handle_expect_100()
        self._reply(404)
        return False

    def _read_exact(self, size):
        data = self.rfile.read(size)
        if len(data) != size:
            raise _BadRequest
        return data

    def _request_body(self):
        length = _header_length(self.headers)
        encodings = self.headers.get_all("Transfer-Encoding", [])
        if encodings and (length is not None or [item.lower() for item in encodings] != ["chunked"]):
            raise _BadRequest
        return length, bool(encodings)

    def _chunked_body(self):
        while True:
            line = self.rfile.readline(8193)
            size_field = line.removesuffix(b"\r\n").partition(b";")[0]
            if not line.endswith(b"\r\n") or not re.fullmatch(rb"[0-9A-Fa-f]{1,16}", size_field):
                raise _BadRequest
            size = int(size_field, 16)
            if not size:
                remaining = _BUFFER_SIZE
                while remaining > 0:
                    trailer = self.rfile.readline(min(remaining, 8193))
                    if trailer == b"\r\n":
                        return
                    if not trailer.endswith(b"\r\n"):
                        break
                    remaining -= len(trailer)
                raise _BadRequest
            while size:
                chunk = self._read_exact(min(size, _BUFFER_SIZE))
                size -= len(chunk)
                yield chunk
            if self._read_exact(2) != b"\r\n":
                raise _BadRequest

    def _send_upstream(self, upstream, path, started, length, chunked):
        upstream.connect()
        upstream.putrequest(self.command, self._target(), skip_accept_encoding=True)
        for name, value in _end_to_end(self.headers):
            lower = name.lower()
            if lower not in {"host", "expect", "forwarded"} and not lower.startswith("x-forwarded-"):
                upstream.putheader(name, value)
        upstream.putheader("Connection", "close")
        if chunked:
            upstream.putheader("Transfer-Encoding", "chunked")
        elif length is not None:
            upstream.putheader("Content-Length", str(length))
        upstream.endheaders()
        forwarded = monotonic()
        transfer = Transfer(_utc_now(), forwarded - started)
        with self.server.proxy._lock:
            self.server.proxy._transfers[self.command, path].append(transfer)
        if chunked:
            for chunk in self._chunked_body():
                upstream.send(f"{len(chunk):x}\r\n".encode("ascii") + chunk + b"\r\n")
            upstream.send(b"0\r\n\r\n")
        else:
            while length:
                chunk = self._read_exact(min(length, _BUFFER_SIZE))
                upstream.send(chunk)
                length -= len(chunk)
        return transfer, forwarded

    def _response(self, upstream, transfer, forwarded, rule):
        with self.server.proxy._lock:
            transfer.upstream_status = upstream.status
        try:
            advertised_length = _header_length(upstream.headers)
        except _BadRequest:
            raise HTTPException("Invalid upstream framing.") from None
        encodings = upstream.headers.get_all("Transfer-Encoding", [])
        if (
            upstream.status < 200
            or (encodings and (advertised_length is not None or not upstream.chunked))
        ):
            raise HTTPException("Invalid upstream framing.")

        fault = rule.fault if rule else None
        discard = fault == "drop-after-upstream"
        cut = fault in {"cut-sse", "disconnect-sse"}
        content_type = upstream.getheader("Content-Type", "").partition(";")[0]
        if cut and content_type.strip().lower() != "text/event-stream":
            raise HTTPException("SSE fault requires an event stream.")
        has_body = self.command != "HEAD" and upstream.status not in {204, 304}
        chunked = has_body and (upstream.length is None or cut)
        if not discard:
            self._response_started = True
            self.send_response_only(upstream.status)
            self.send_header("Connection", "close")
            for name, value in _end_to_end(upstream.headers):
                self.send_header(name, value)
            if chunked:
                self.send_header("Transfer-Encoding", "chunked")
            elif advertised_length is not None:
                self.send_header("Content-Length", str(advertised_length))
            self.end_headers()

        remaining = rule.byte_limit if cut else None
        body_read_at = None
        while has_body and remaining != 0:
            data = upstream.read1(_BUFFER_SIZE if remaining is None else remaining)
            if not data:
                break
            if upstream.length == 0:
                body_read_at = _utc_now(), monotonic()
            if not discard:
                if chunked:
                    self.wfile.write(f"{len(data):x}\r\n".encode("ascii") + data + b"\r\n")
                else:
                    self.wfile.write(data)
            if remaining is not None:
                remaining -= len(data)
        if cut:
            self._disconnect()
            return
        if upstream.length:
            raise IncompleteRead(b"", upstream.length)
        finished_at, finished = body_read_at or (_utc_now(), monotonic())
        with self.server.proxy._lock:
            transfer.full_body_read = True
            transfer.upstream_finished_at = finished_at
            transfer.duration_upstream = finished - forwarded

        if discard and rule.response == "http-503":
            self._reply(503, b'{"problem":{"code":"unknown"}}')
        elif discard:
            self._disconnect()
        elif chunked:
            self.wfile.write(b"0\r\n\r\n")

    def _serve(self):
        self.close_connection = True
        self._response_started = False
        started = monotonic()
        path = self._route()
        if path is None:
            self._reply(404)
            return
        try:
            length, chunked = self._request_body()
        except _BadRequest:
            self._reply(400)
            return
        proxy = self.server.proxy
        with proxy._lock:
            rule = proxy._rules.pop((self.command, path), None)
        if rule and rule.fault == "drop-before-forward":
            self._disconnect()
            return
        if rule and rule.fault == "delay" and proxy._closed.wait(rule.seconds):
            return
        upstream = HTTPConnection(LOOPBACK, proxy._upstream_port, timeout=_IO_TIMEOUT)
        try:
            transfer, forwarded = self._send_upstream(upstream, path, started, length, chunked)
            with upstream.getresponse() as response:
                self._response(response, transfer, forwarded, rule)
        except (_BadRequest, HTTPException, OSError) as error:
            if not self._response_started:
                self._reply(400 if isinstance(error, _BadRequest) else 502)
            else:
                self._disconnect()
        finally:
            upstream.close()

    do_DELETE = do_GET = do_HEAD = do_OPTIONS = do_PATCH = do_POST = do_PUT = _serve
