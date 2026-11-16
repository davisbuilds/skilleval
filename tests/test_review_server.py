"""Real HTTP and filesystem controls for the local review authority boundary."""

from __future__ import annotations

import hashlib
import http.client
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from skilleval.review_results import build_review, packet_identity
from skilleval.review_server import ReviewServer, create_server


@pytest.fixture
def packet(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    source = tmp_path / "original.txt"
    source.write_text("Original retained evidence.\nNo automatic policy decision.\n")
    spec = {
        "title": "Synthetic review",
        "author": {"name": "Test agent", "role": "agent", "model": "authored-control"},
        "summary": {
            "headline": "Needs policy choice",
            "conclusion": "Unresolved",
            "scope": "One synthetic run",
            "limitations": ["Authored control"],
            "evidence": ["citation"],
        },
        "arms": [
            {
                "id": "off",
                "label": "OFF",
                "status": "complete",
                "quality": ["unresolved"],
                "usage": ["unknown"],
                "cost": ["unknown"],
                "timing": ["unknown"],
                "evidence": ["citation"],
            }
        ],
        "exceptions": [
            {
                "id": "policy",
                "title": "Policy choice",
                "category": "human-needed",
                "question": "Which outcome matters?",
                "why_human": "Requires a preference",
                "investigation": ["Evidence inspected"],
                "impact": "Changes interpretation",
                "evidence": ["citation"],
                "options": ["A", "B"],
            },
            {
                "id": "retry",
                "title": "Retry analysis",
                "category": "agent-follow-up",
                "question": "Can replay resolve it?",
                "investigation": ["Not yet replayed"],
                "impact": "Unresolved",
                "evidence": ["citation"],
            },
        ],
        "sources": [
            {
                "id": "source",
                "label": "Retained synthetic evidence",
                "path": "original.txt",
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        ],
        "citations": [
            {
                "id": "citation",
                "source_id": "source",
                "label": "Evidence",
                "start_line": 1,
                "end_line": 2,
            }
        ],
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    directory = tmp_path / "review"
    build_review(spec_path, directory)
    return directory, json.loads((directory / "packet.json").read_text())


@contextmanager
def running(directory: Path) -> Iterator[ReviewServer]:
    with create_server(directory) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield server
        finally:
            server.shutdown()
            worker.join(timeout=3)
            assert not worker.is_alive()


def request(
    server: ReviewServer,
    method: str = "GET",
    target: str = "/api/packet",
    value: Any = None,
    headers: dict[str, str] | None = None,
    raw: bytes | None = None,
) -> tuple[int, dict[str, str], Any]:
    client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    body = json.dumps(value).encode() if raw is None and value is not None else raw
    supplied = {"Content-Type": "application/json", **(headers or {})}
    client.request(method, target, body=body, headers=supplied)
    response = client.getresponse()
    data = response.read()
    result = (
        json.loads(data) if "application/json" in response.getheader("Content-Type", "") else data
    )
    status, response_headers = response.status, dict(response.getheaders())
    client.close()
    return status, response_headers, result


def auth(server: ReviewServer) -> dict[str, str]:
    status, _, state = request(server, target="/api/decisions")
    assert status == 200
    return {
        "Origin": f"http://127.0.0.1:{server.server_port}",
        "X-Skilleval-Token": state["session_token"],
    }


def decision(packet: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return {
        "packet_id": packet["packet_id"],
        "exception_id": "policy",
        "answer": "Choose A",
        **changes,
    }


def test_review_server_serves_packet_and_is_loopback_only(
    packet: tuple[Path, dict[str, Any]],
) -> None:
    directory, expected = packet
    with running(directory) as server:
        assert server.server_address[0] == "127.0.0.1"
        status, headers, body = request(server)
        assert status == 200 and body == expected
        assert headers["Cache-Control"] == "no-store"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
        assert "Access-Control-Allow-Origin" not in headers
        assert request(server, target="/favicon.ico")[0] == 204
        for target, fragment in [("/", b"<html"), ("/app.js", b""), ("/style.css", b"")]:
            status, _, body = request(server, target=target)
            assert status == 200 and fragment in body


def test_answers_are_new_immutable_history_and_survive_refresh(
    packet: tuple[Path, dict[str, Any]],
) -> None:
    directory, frozen = packet
    originals = {path: path.read_bytes() for path in directory.parent.rglob("*") if path.is_file()}
    with running(directory) as server:
        headers = auth(server)
        created = []
        for answer in ["Choose A", "Changed preference: choose B"]:
            status, _, body = request(
                server, "POST", "/api/decisions", decision(frozen, answer=answer), headers
            )
            assert status == 201 and body["answer"] == answer
            created.append(body)
        assert created[0]["response_id"] != created[1]["response_id"]
        status, _, state = request(server, target="/api/decisions")
        assert status == 200 and state["decisions"] == created
    with running(directory) as refreshed:
        _, _, state = request(refreshed, target="/api/decisions")
        assert state["decisions"] == created
        assert state["session_token"] != headers["X-Skilleval-Token"]
    for path, original in originals.items():
        assert path.read_bytes() == original
    assert len(list((directory / "responses").iterdir())) == 2


@pytest.mark.parametrize(
    "change,status",
    [
        ({"packet_id": "0" * 64}, 409),
        ({"exception_id": "missing"}, 422),
        ({"exception_id": "retry"}, 422),
        ({"answer": " "}, 422),
        ({"answer": "x" * 10001}, 422),
        ({"answer": 42}, 422),
        ({"question": "tampered"}, 422),
        ({"answer": "\ud800"}, 422),
    ],
)
def test_rejected_answers_create_no_files(
    packet: tuple[Path, dict[str, Any]], change: dict[str, Any], status: int
) -> None:
    directory, frozen = packet
    original = (directory / "packet.json").read_bytes()
    with running(directory) as server:
        result, _, _ = request(
            server, "POST", "/api/decisions", decision(frozen, **change), auth(server)
        )
        assert result == status
    assert not (directory / "responses").exists()
    assert (directory / "packet.json").read_bytes() == original


@pytest.mark.parametrize(
    "changes",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Host": "evil.example"},
        {"X-Skilleval-Token": "incorrect"},
        {"X-Skilleval-Token": "nonascii-ü"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Origin": ""},
        {"Origin": "http://localhost:1"},
    ],
)
def test_mutation_auth_guards_with_positive_control(
    packet: tuple[Path, dict[str, Any]], changes: dict[str, str]
) -> None:
    directory, frozen = packet
    with running(directory) as server:
        headers = {**auth(server), **changes}
        assert request(server, "POST", "/api/decisions", decision(frozen), headers)[0] == 403
        assert not (directory / "responses").exists()
        assert request(server, "POST", "/api/decisions", decision(frozen), auth(server))[0] == 201


def test_missing_token_and_origin_and_cross_site_reads(packet: tuple[Path, dict[str, Any]]) -> None:
    directory, frozen = packet
    with running(directory) as server:
        assert request(server, "POST", "/api/decisions", decision(frozen))[0] == 403
        headers = auth(server)
        del headers["Origin"]
        assert request(server, "POST", "/api/decisions", decision(frozen), headers)[0] == 403
        for target in ["/api/packet", "/api/decisions", "/"]:
            status, _, body = request(
                server, target=target, headers={"Origin": "https://evil.example"}
            )
            assert status == 403 and "session_token" not in body
            assert request(server, target=target, headers={"Host": "evil.example"})[0] == 403


@pytest.mark.parametrize(
    "target",
    [
        "/packet.json",
        "/source-spec.json",
        "/responses/anything.json",
        "/../original.txt",
        "/%2e%2e/original.txt",
        "/api/packet?token=bad",
    ],
)
def test_no_arbitrary_file_or_query_routes(
    packet: tuple[Path, dict[str, Any]], target: str
) -> None:
    directory, _ = packet
    with running(directory) as server:
        assert request(server, target=target)[0] == 404


@pytest.mark.parametrize(
    "body,status",
    [
        (b"{", 400),
        (b"[]", 422),
        (b"\xff", 400),
        (b'{"answer":NaN}', 400),
        (b'{"answer":"A","answer":"B"}', 400),
        (b"x" * 65537, 413),
    ],
)
def test_malformed_or_large_bodies_are_rejected(
    packet: tuple[Path, dict[str, Any]], body: bytes, status: int
) -> None:
    directory, _ = packet
    with running(directory) as server:
        assert (
            request(server, "POST", "/api/decisions", headers=auth(server), raw=body)[0] == status
        )
    assert not (directory / "responses").exists()


@pytest.mark.parametrize("rehash", [False, True])
def test_packet_tampering_fails_closed(packet: tuple[Path, dict[str, Any]], rehash: bool) -> None:
    directory, frozen = packet
    with running(directory) as server:
        headers = auth(server)
        original_id = frozen["packet_id"]
        frozen["exceptions"][0]["question"] = "Tampered question"
        if rehash:
            frozen["packet_id"] = packet_identity(
                {key: value for key, value in frozen.items() if key != "packet_id"}
            )
        (directory / "packet.json").write_text(json.dumps(frozen))
        assert request(server)[0] == 409
        assert (
            request(
                server, "POST", "/api/decisions", decision(frozen, packet_id=original_id), headers
            )[0]
            == 409
        )
    assert not (directory / "responses").exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"packet_id": "0" * 64},
        {"answer": False},
        {"created_at": "invalid"},
        {"extra": "unexpected"},
        {"exception_id": "retry"},
    ],
)
def test_invalid_saved_response_is_surfaced_without_private_path(
    packet: tuple[Path, dict[str, Any]], changes: dict[str, Any]
) -> None:
    directory, frozen = packet
    with running(directory) as server:
        status, _, saved = request(server, "POST", "/api/decisions", decision(frozen), auth(server))
        assert status == 201
        stored = directory / "responses" / f"{saved['response_id']}.json"
        saved.update(changes)
        stored.write_text(json.dumps(saved))
        status, _, body = request(server, target="/api/decisions")
        assert status == 409 and "invalid" in body["error"]
        assert str(directory) not in json.dumps(body)
        assert "session_token" not in body
    with running(directory) as refreshed:
        assert request(refreshed)[0] == 200
        status, _, body = request(refreshed, target="/api/decisions")
        assert status == 409 and "session_token" not in body


def test_response_symlinks_do_not_escape_directory(packet: tuple[Path, dict[str, Any]]) -> None:
    directory, frozen = packet
    elsewhere = directory.parent / "elsewhere"
    elsewhere.mkdir()
    sentinel = elsewhere / "sentinel"
    sentinel.write_text("untouched")
    with running(directory) as server:
        headers = auth(server)
        (directory / "responses").symlink_to(elsewhere, target_is_directory=True)
        status, _, _ = request(server, "POST", "/api/decisions", decision(frozen), headers)
        assert status == 409
    assert list(elsewhere.iterdir()) == [sentinel]
    assert sentinel.read_text() == "untouched"


def test_localhost_alias_and_json_content_type(packet: tuple[Path, dict[str, Any]]) -> None:
    directory, frozen = packet
    with running(directory) as server:
        headers = {
            **auth(server),
            "Host": f"localhost:{server.server_port}",
            "Origin": f"http://localhost:{server.server_port}",
        }
        wrong = {**headers, "Content-Type": "text/plain"}
        assert request(server, "POST", "/api/decisions", decision(frozen), wrong)[0] == 415
        assert not (directory / "responses").exists()
        assert request(server, "POST", "/api/decisions", decision(frozen), headers)[0] == 201


def test_duplicate_host_and_lengths_are_rejected(packet: tuple[Path, dict[str, Any]]) -> None:
    directory, frozen = packet
    with running(directory) as server:
        headers = auth(server)
        body = json.dumps(decision(frozen)).encode()
        for duplicate, expected in [("Host", 403), ("Content-Length", 400), ("Origin", 403)]:
            client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            client.putrequest("POST", "/api/decisions", skip_host=True)
            fields = {
                "Host": f"127.0.0.1:{server.server_port}",
                **headers,
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            }
            for key, value in fields.items():
                client.putheader(key, value)
            client.putheader(duplicate, fields[duplicate])
            client.endheaders(body)
            response = client.getresponse()
            assert response.status == expected
            response.read()
            client.close()
        assert not (directory / "responses").exists()


def test_symlinked_response_file_is_rejected(packet: tuple[Path, dict[str, Any]]) -> None:
    directory, frozen = packet
    with running(directory) as server:
        headers = auth(server)
        status, _, saved = request(server, "POST", "/api/decisions", decision(frozen), headers)
        assert status == 201
        stored = directory / "responses" / f"{saved['response_id']}.json"
        moved = directory.parent / "external-response.json"
        stored.rename(moved)
        stored.symlink_to(moved)
        before = moved.read_bytes()
        assert request(server, target="/api/decisions")[0] == 409
        assert request(server, "POST", "/api/decisions", decision(frozen), headers)[0] == 409
        assert moved.read_bytes() == before


def test_default_method_errors_use_sanitized_json_and_security_headers(
    packet: tuple[Path, dict[str, Any]],
) -> None:
    directory, _ = packet
    with running(directory) as server:
        status, headers, body = request(server, "DELETE", "/private-target")
        assert status == 501 and set(body) == {"error"}
        assert headers["Cache-Control"] == "no-store"
        assert "private-target" not in body["error"]


def test_answers_tokens_and_request_targets_are_not_logged(
    packet: tuple[Path, dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    directory, frozen = packet
    with running(directory) as server:
        headers = auth(server)
        answer = "Private decision text"
        assert (
            request(server, "POST", "/api/decisions", decision(frozen, answer=answer), headers)[0]
            == 201
        )
        assert request(server, target="/unknown?token=" + headers["X-Skilleval-Token"])[0] == 404
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
