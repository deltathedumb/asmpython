"""http.server: HTTP server classes (accessed as http.server or http_server).

Provides BaseHTTPRequestHandler, SimpleHTTPRequestHandler, and
HTTPServer built on top of socketserver.

Usage::

    import http_server as http_server

    class MyHandler(http_server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile("Hello, World!")

    server = http_server.HTTPServer(("", 8080), MyHandler)
    server.serve_forever()
"""
from __future__ import annotations

import socketserver as _socketserver
import socket as _socket
import mimetypes as _mimetypes
import os as _os
import time as _time

DEFAULT_ERROR_MESSAGE: str = """<!DOCTYPE html>
<html>
<head><title>Error {code}</title></head>
<body><h1>{code} {message}</h1><p>{explain}</p></body>
</html>
"""

DEFAULT_ERROR_CONTENT_TYPE: str = "text/html;charset=utf-8"

_HTTP_RESPONSES: dict = {}


def _init_responses() -> None:
    _HTTP_RESPONSES["100"] = ["Continue", "Request received, please continue"]
    _HTTP_RESPONSES["101"] = ["Switching Protocols", "Switching to new protocol"]
    _HTTP_RESPONSES["200"] = ["OK", "Request fulfilled, document follows"]
    _HTTP_RESPONSES["201"] = ["Created", "Document created, URL follows"]
    _HTTP_RESPONSES["204"] = ["No Content", "Request fulfilled, nothing follows"]
    _HTTP_RESPONSES["206"] = ["Partial Content", "Partial content follows"]
    _HTTP_RESPONSES["301"] = ["Moved Permanently", "Object moved permanently"]
    _HTTP_RESPONSES["302"] = ["Found", "Object moved temporarily"]
    _HTTP_RESPONSES["303"] = ["See Other", "Object moved, see Method and URL list"]
    _HTTP_RESPONSES["304"] = ["Not Modified", "Document has not changed since last request"]
    _HTTP_RESPONSES["400"] = ["Bad Request", "Bad request syntax or unsupported method"]
    _HTTP_RESPONSES["401"] = ["Unauthorized", "No permission -- see authorization schemes"]
    _HTTP_RESPONSES["403"] = ["Forbidden", "Request forbidden -- authorization will not help"]
    _HTTP_RESPONSES["404"] = ["Not Found", "Nothing matches the given URI"]
    _HTTP_RESPONSES["405"] = ["Method Not Allowed", "Specified method is invalid for this resource"]
    _HTTP_RESPONSES["408"] = ["Request Timeout", "Request timed out; try again later"]
    _HTTP_RESPONSES["409"] = ["Conflict", "Request conflict"]
    _HTTP_RESPONSES["410"] = ["Gone", "URI no longer exists and has been permanently removed"]
    _HTTP_RESPONSES["411"] = ["Length Required", "Client must specify Content-Length"]
    _HTTP_RESPONSES["413"] = ["Request Entity Too Large", "Entity is too large"]
    _HTTP_RESPONSES["414"] = ["Request-URI Too Long", "URI is too long"]
    _HTTP_RESPONSES["415"] = ["Unsupported Media Type", "Entity body in unsupported format"]
    _HTTP_RESPONSES["416"] = ["Requested Range Not Satisfiable", "Cannot satisfy request range"]
    _HTTP_RESPONSES["500"] = ["Internal Server Error", "Server got itself in trouble"]
    _HTTP_RESPONSES["501"] = ["Not Implemented", "Server does not support this operation"]
    _HTTP_RESPONSES["502"] = ["Bad Gateway", "Invalid responses from another server/proxy"]
    _HTTP_RESPONSES["503"] = ["Service Unavailable", "The server cannot process the request due to a high load"]
    _HTTP_RESPONSES["505"] = ["HTTP Version Not Supported", "Cannot fulfill request"]


_init_responses()


# ---------------------------------------------------------------------------
# HTTPServer
# ---------------------------------------------------------------------------

class HTTPServer(_socketserver.TCPServer):
    """HTTP server that handles one request at a time."""

    allow_reuse_address: int = 1
    server_name: str = ""
    server_port: int = 0

    def server_bind(self) -> None:
        super().server_bind()
        host: str = self.server_address[0]
        port: int = self.server_address[1]
        self.server_name = host if host else "localhost"
        self.server_port = port


class ThreadingHTTPServer(_socketserver.ThreadingMixIn, HTTPServer):
    """HTTP server that handles each request in a new thread."""

    daemon_threads: int = 1
    block_on_close: int = 1


# ---------------------------------------------------------------------------
# BaseHTTPRequestHandler
# ---------------------------------------------------------------------------

class BaseHTTPRequestHandler(_socketserver.StreamRequestHandler):
    """Handler for HTTP/1.0 and HTTP/1.1 requests."""

    server_version: str = "BaseHTTP/0.6"
    http_version: str = "HTTP/1.1"
    error_message_format: str = DEFAULT_ERROR_MESSAGE
    error_content_type: str = DEFAULT_ERROR_CONTENT_TYPE
    protocol_version: str = "HTTP/1.1"

    def __init__(self, request: object, client_address: list,
                 server: _socketserver.BaseServer) -> None:
        self._sock: _socket.socket = request
        self.client_address: list = client_address
        self.server: _socketserver.BaseServer = server
        self.command: str = ""
        self.path: str = ""
        self.request_version: str = ""
        self.headers: dict = {}
        self._headers_sent: int = 0
        self._response_code: int = 0
        self._recvbuf: str = ""
        self._sendbuf: str = ""
        self.setup()
        self.handle()
        self.finish()

    def setup(self) -> None:
        pass

    def handle(self) -> None:
        """Handle a single HTTP request."""
        self.handle_one_request()

    def handle_one_request(self) -> None:
        request_line: str = self._readline()
        if not request_line:
            return
        request_line = request_line.rstrip("\r\n")
        parts: list = request_line.split(" ")
        if len(parts) < 3:
            self.send_error(400, "Bad request syntax")
            return
        self.command = parts[0]
        self.path = parts[1]
        self.request_version = parts[2]
        self._parse_headers()
        method_name: str = "do_" + self.command
        if not self._has_method(method_name):
            self.send_error(405, "Method Not Allowed")
            return
        self._dispatch(method_name)

    def _has_method(self, name: str) -> int:
        # Subclasses override do_GET, do_POST, etc.
        return 0

    def _dispatch(self, method_name: str) -> None:
        # Subclasses override do_GET, do_POST, etc.
        pass

    def _parse_headers(self) -> None:
        self.headers = {}
        while True:
            line: str = self._readline()
            if not line or line in ("\r\n", "\n"):
                break
            line = line.rstrip("\r\n")
            colon: int = line.find(":")
            if colon >= 0:
                name: str = line[:colon].strip().lower()
                value: str = line[colon + 1:].strip()
                self.headers[name] = value

    def _readline(self) -> str:
        while "\n" not in self._recvbuf:
            chunk: str = self._sock.recv(4096)
            if not chunk:
                return ""
            self._recvbuf = self._recvbuf + chunk
        nl: int = self._recvbuf.find("\n")
        line: str = self._recvbuf[:nl + 1]
        self._recvbuf = self._recvbuf[nl + 1:]
        return line

    def _read_body(self, length: int) -> str:
        while len(self._recvbuf) < length:
            chunk: str = self._sock.recv(4096)
            if not chunk:
                break
            self._recvbuf = self._recvbuf + chunk
        body: str = self._recvbuf[:length]
        self._recvbuf = self._recvbuf[length:]
        return body

    def rfile(self) -> str:
        """Read request body (uses Content-Length header)."""
        cl_str: str = self.headers.get("content-length", "0")
        cl: int = 0
        if cl_str:
            cl = int(cl_str)
        if cl > 0:
            return self._read_body(cl)
        return ""

    def wfile(self, data: str) -> None:
        """Write data to the response body."""
        self._sock.sendall(data)

    def send_response(self, code: int, message: str = "") -> None:
        """Send HTTP response status line."""
        if not message:
            key: str = str(code)
            if key in _HTTP_RESPONSES:
                message = _HTTP_RESPONSES[key][0]
            else:
                message = "Unknown"
        self._response_code = code
        status_line: str = self.protocol_version + " " + str(code) + " " + message + "\r\n"
        self._sock.sendall(status_line)
        self.send_header("Server", self.server_version)
        self.send_header("Date", self.date_time_string())

    def send_header(self, keyword: str, value: str) -> None:
        """Send an HTTP header."""
        self._sock.sendall(keyword + ": " + value + "\r\n")

    def send_error(self, code: int, message: str = "", explain: str = "") -> None:
        """Send an error response."""
        key: str = str(code)
        short_msg: str = message
        long_msg: str = explain
        if not short_msg and key in _HTTP_RESPONSES:
            short_msg = _HTTP_RESPONSES[key][0]
        if not long_msg and key in _HTTP_RESPONSES:
            long_msg = _HTTP_RESPONSES[key][1]
        self.send_response(code, short_msg)
        self.send_header("Content-Type", self.error_content_type)
        body: str = "<!DOCTYPE html><html><head><title>Error " + str(code) + "</title></head>"
        body = body + "<body><h1>" + str(code) + " " + short_msg + "</h1>"
        body = body + "<p>" + long_msg + "</p></body></html>"
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile(body)

    def end_headers(self) -> None:
        """Send the blank line ending the HTTP headers."""
        self._sock.sendall("\r\n")
        self._headers_sent = 1

    def date_time_string(self, timestamp: float = 0.0) -> str:
        """Return the current date/time formatted for HTTP headers."""
        if timestamp == 0.0:
            timestamp = _time.time()
        t: list = _time.gmtime(timestamp)
        days: list = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        months: list = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        year: int = t[0]
        month: int = t[1]
        day: int = t[2]
        hour: int = t[3]
        minute: int = t[4]
        second: int = t[5]
        weekday: int = t[6]
        return (days[weekday] + ", " + str(day).zfill(2) + " " +
                months[month - 1] + " " + str(year) + " " +
                str(hour).zfill(2) + ":" + str(minute).zfill(2) + ":" +
                str(second).zfill(2) + " GMT")

    def log_request(self, code: int = 0, size: int = 0) -> None:
        pass

    def log_error(self, fmt: str, arg0: str = "", arg1: str = "") -> None:
        pass

    def log_message(self, fmt: str, arg0: str = "", arg1: str = "") -> None:
        pass

    def finish(self) -> None:
        pass


# ---------------------------------------------------------------------------
# SimpleHTTPRequestHandler
# ---------------------------------------------------------------------------

class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    """Handler for simple file serving from a directory.

    Maps incoming GET/HEAD requests to files in *directory* (default: cwd).
    """

    server_version: str = "SimpleHTTP/0.6"
    extensions_map: dict = {}
    directory: str = "."

    def __init__(self, request: object, client_address: list,
                 server: _socketserver.BaseServer,
                 directory: str = ".") -> None:
        self.directory = directory
        super().__init__(request, client_address, server)

    def _has_method(self, name: str) -> int:
        return name in ("do_GET", "do_HEAD")

    def _dispatch(self, method_name: str) -> None:
        if method_name == "do_GET":
            self.do_GET()
        elif method_name == "do_HEAD":
            self.do_HEAD()

    def do_GET(self) -> None:
        path: str = self._translate_path()
        if _os.path.isdir(path):
            index: str = path + "/index.html"
            if _os.path.exists(index):
                path = index
            else:
                self._list_directory(path)
                return
        if not _os.path.exists(path):
            self.send_error(404)
            return
        content: str = ""
        f = open(path, "r")
        content = f.read()
        f.close()
        ctype_pair: list = _mimetypes.guess_type(path)
        ctype: str = ctype_pair[0] if ctype_pair[0] else "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile(content)

    def do_HEAD(self) -> None:
        path: str = self._translate_path()
        if not _os.path.exists(path):
            self.send_error(404)
            return
        ctype_pair: list = _mimetypes.guess_type(path)
        ctype: str = ctype_pair[0] if ctype_pair[0] else "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()

    def _translate_path(self) -> str:
        path: str = self.path
        q: int = path.find("?")
        if q >= 0:
            path = path[:q]
        f: int = path.find("#")
        if f >= 0:
            path = path[:f]
        if path.startswith("/"):
            path = path[1:]
        if not path:
            return self.directory
        return self.directory + "/" + path

    def _list_directory(self, path: str) -> None:
        try:
            entries: list = _os.listdir(path)
        except Exception:
            self.send_error(403)
            return
        body: str = "<!DOCTYPE html><html><head><title>Directory listing for " + self.path + "</title></head>"
        body = body + "<body><h2>Directory listing for " + self.path + "</h2><hr><ul>"
        for name in entries:
            body = body + "<li><a href=\"" + name + "\">" + name + "</a></li>"
        body = body + "</ul><hr></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile(body)


# ---------------------------------------------------------------------------
# CGIHTTPRequestHandler (stub — CGI not supported)
# ---------------------------------------------------------------------------

class CGIHTTPRequestHandler(SimpleHTTPRequestHandler):
    """Serve CGI scripts (stub — CGI execution not supported in asmpython)."""

    cgi_directories: list = ["/cgi-bin", "/htbin"]

    def _is_cgi(self) -> int:
        for prefix in self.cgi_directories:
            if self.path.startswith(prefix):
                return 1
        return 0

    def do_GET(self) -> None:
        if self._is_cgi():
            self.send_error(501, "CGI execution not supported")
        else:
            super().do_GET()

    def do_POST(self) -> None:
        if self._is_cgi():
            self.send_error(501, "CGI execution not supported")
        else:
            self.send_error(405, "Method Not Allowed")


# ---------------------------------------------------------------------------
# test() helper
# ---------------------------------------------------------------------------

def test(HandlerClass: int = SimpleHTTPRequestHandler,
         ServerClass: int = HTTPServer, port: int = 8000,
         bind: str = "") -> None:
    """Run an HTTP server serving the current directory."""
    server_address: list = [bind, port]
    server: HTTPServer = HTTPServer(server_address, HandlerClass)
    server.serve_forever()
