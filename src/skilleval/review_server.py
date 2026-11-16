"""Bounded loopback HTTP access to one immutable review packet and answer history."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import socket
import stat
import sys
import threading
from contextlib import suppress
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from skilleval.errors import SkillEvalError
from skilleval.review_results import MAX_RESPONSE_CHARS

_MAX_BODY = 65_536
_MAX_RESPONSES = 10_000
_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(value: str) -> Any:
    raise ValueError("non-finite JSON number")


def _json(data: bytes) -> Any:
    return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)


def _packet(directory: Path) -> dict[str, Any]:
    # Lazy import keeps this transport independent of packet construction.
    from skilleval.review_results import load_packet

    return load_packet(directory)


def _answer(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_RESPONSE_CHARS:
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _human_exception(packet: dict[str, Any], exception_id: Any) -> bool:
    return isinstance(exception_id, str) and any(
        item["id"] == exception_id and item["category"] == "human-needed"
        for item in packet["exceptions"]
    )


def _response(value: Any, packet: dict[str, Any], filename: str) -> dict[str, Any]:
    required = {"response_id", "packet_id", "exception_id", "answer", "created_at"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("invalid response fields")
    if any(not isinstance(value[key], str) for key in required):
        raise ValueError("invalid response field types")
    response_id = value["response_id"]
    if str(UUID(response_id)) != response_id or filename != f"{response_id}.json":
        raise ValueError("invalid response identity")
    if value["packet_id"] != packet["packet_id"]:
        raise ValueError("response belongs to a different packet")
    if not _human_exception(packet, value["exception_id"]) or not _answer(value["answer"]):
        raise ValueError("invalid response target or answer")
    timestamp = datetime.fromisoformat(value["created_at"])
    if timestamp.utcoffset() != UTC.utcoffset(timestamp):
        raise ValueError("response timestamp must be UTC")
    return value


def _response_directory(directory: Path, *, create: bool) -> int | None:
    responses = directory / "responses"
    if create:
        responses.mkdir(mode=0o700, exist_ok=True)
    try:
        return os.open(responses, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        if not create:
            return None
        raise


def _read_responses(directory: Path, packet: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        directory_fd = _response_directory(directory, create=False)
        if directory_fd is None:
            return []
        try:
            names = os.listdir(directory_fd)
            if len(names) > _MAX_RESPONSES:
                raise ValueError("too many responses")
            responses = []
            for name in sorted(names):
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
                with os.fdopen(fd, "rb") as handle:
                    metadata = os.fstat(handle.fileno())
                    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_BODY:
                        raise ValueError("invalid response file")
                    responses.append(_response(_json(handle.read(_MAX_BODY + 1)), packet, name))
            return sorted(responses, key=lambda item: (item["created_at"], item["response_id"]))
        finally:
            os.close(directory_fd)
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise SkillEvalError("Stored review responses are invalid or unreadable.") from exc


def _write_response(directory: Path, response: dict[str, Any]) -> None:
    directory_fd = None
    name = f"{response['response_id']}.json"
    created = False
    try:
        directory_fd = _response_directory(directory, create=True)
        assert directory_fd is not None
        fd = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd
        )
        created = True
        with os.fdopen(fd, "wb") as handle:
            handle.write(
                (json.dumps(response, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            )
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        if created and directory_fd is not None:
            with suppress(OSError):
                os.unlink(name, dir_fd=directory_fd)
        raise SkillEvalError("Could not save the review response.") from exc
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


class ReviewServer(ThreadingHTTPServer):
    """One packet, one process-local token, and at most sixteen active connections."""

    daemon_threads = True
    request_queue_size = 16

    def __init__(self, directory: Path, port: int) -> None:
        self.directory = directory.resolve()
        try:
            packet = _packet(self.directory)
        except (SkillEvalError, OSError, ValueError) as exc:
            raise SkillEvalError("Review packet is invalid or unreadable.") from exc
        self.packet_id: str = packet["packet_id"]
        self.session_token = secrets.token_urlsafe(32)
        self.response_lock = threading.Lock()
        self._connections = threading.BoundedSemaphore(16)
        super().__init__(("127.0.0.1", port), _Handler)

    def process_request(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: Any
    ) -> None:
        assert isinstance(request, socket.socket)
        if not self._connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            request.settimeout(5)
            super().process_request(request, client_address)
        except BaseException:
            self._connections.release()
            raise

    def process_request_thread(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: Any
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connections.release()

    def current_packet(self) -> dict[str, Any]:
        try:
            packet = _packet(self.directory)
            if packet["packet_id"] != self.packet_id:
                raise SkillEvalError("Review packet changed; restart with the intended packet.")
            return packet
        except (SkillEvalError, OSError, ValueError) as exc:
            raise SkillEvalError(
                "Review packet changed or is invalid; restart the review server."
            ) from exc


class _Handler(BaseHTTPRequestHandler):
    server: ReviewServer
    server_version = "SkillevalReview"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:
        # Request targets, answers, and tokens never enter logs.
        pass

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        self._error(code, "Review request is unsupported or malformed.")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", _CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _send_json(self, status: int, value: dict[str, Any]) -> None:
        self._send(
            status,
            json.dumps(value, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _error(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message})

    def _same_origin(self, *, required: bool = False) -> bool:
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get_all("Host", [])
        origins = self.headers.get_all("Origin", [])
        if len(host) != 1 or host[0] not in hosts:
            self._error(403, "Unrecognized review server host.")
            return False
        if len(origins) > 1 or (required and not origins):
            self._error(403, "A matching local Origin is required.")
            return False
        if origins and origins[0] != f"http://{host[0]}":
            self._error(403, "A matching local Origin is required.")
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self._error(403, "Cross-site review requests are forbidden.")
            return False
        return True

    def do_GET(self) -> None:
        if not self._same_origin():
            return
        try:
            if self.path == "/api/packet":
                self._send_json(200, self.server.current_packet())
            elif self.path == "/api/decisions":
                with self.server.response_lock:
                    packet = self.server.current_packet()
                    decisions = _read_responses(self.server.directory, packet)
                self._send_json(
                    200,
                    {
                        "packet_id": packet["packet_id"],
                        "decisions": decisions,
                        "session_token": self.server.session_token,
                    },
                )
            elif self.path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
            elif self.path in _ASSETS:
                filename, content_type = _ASSETS[self.path]
                asset = files("skilleval").joinpath("review_ui", filename).read_bytes()
                self._send(200, asset, content_type)
            else:
                self._error(404, "Review resource not found.")
        except SkillEvalError as exc:
            self._error(409, str(exc))
        except OSError:
            self._error(500, "Review resource is unavailable.")

    def do_POST(self) -> None:
        if not self._same_origin(required=True):
            return
        tokens = self.headers.get_all("X-Skilleval-Token", [])
        if len(tokens) != 1 or not hmac.compare_digest(
            tokens[0].encode("utf-8"), self.server.session_token.encode("utf-8")
        ):
            self._error(403, "Review session token is invalid; refresh the page.")
            return
        if self.path != "/api/decisions":
            self._error(404, "Review resource not found.")
            return
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") is not None or len(lengths) != 1:
            self._error(400, "A single Content-Length is required.")
            return
        try:
            length = int(lengths[0])
        except ValueError:
            self._error(400, "Invalid Content-Length.")
            return
        if not 0 < length <= _MAX_BODY:
            self._error(413, "Review response body must be between 1 and 65536 bytes.")
            return
        if self.headers.get_content_type() != "application/json":
            self._error(415, "Review responses require application/json.")
            return
        try:
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError("incomplete body")
            value = _json(data)
        except (ValueError, UnicodeError, RecursionError, OSError):
            self._error(400, "Review response must be valid UTF-8 JSON.")
            return
        if (
            not isinstance(value, dict)
            or set(value) != {"packet_id", "exception_id", "answer"}
            or any(not isinstance(item, str) for item in value.values())
        ):
            self._error(422, "Response requires packet_id, exception_id, and answer strings.")
            return
        if value["packet_id"] != self.server.packet_id:
            self._error(409, "Response packet_id does not match this review packet.")
            return
        if not _answer(value["answer"]):
            self._error(422, "Answer must contain text and be at most 10000 characters.")
            return
        try:
            with self.server.response_lock:
                packet = self.server.current_packet()
                if not _human_exception(packet, value["exception_id"]):
                    self._error(422, "Only human-needed review exceptions accept answers.")
                    return
                previous = _read_responses(self.server.directory, packet)
                if len(previous) >= _MAX_RESPONSES:
                    self._error(409, "Review response history limit reached.")
                    return
                response = {
                    "response_id": str(uuid4()),
                    **value,
                    "created_at": datetime.now(UTC).isoformat(),
                }
                _write_response(self.server.directory, response)
            self._send_json(201, response)
        except SkillEvalError as exc:
            self._error(409, str(exc))


def create_server(directory: Path, port: int = 0) -> ReviewServer:
    """Construct an unstarted server bound exclusively to IPv4 loopback."""
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise SkillEvalError("Review port must be between 0 and 65535.")
    return ReviewServer(directory, port)


def serve_review(directory: Path, port: int = 0) -> None:
    """Serve the local review UI until interrupted; no inference or VM work runs."""
    with create_server(directory, port) as server:
        print(f"Review: http://127.0.0.1:{server.server_port}/", file=sys.stderr)
        with suppress(KeyboardInterrupt):
            server.serve_forever()
