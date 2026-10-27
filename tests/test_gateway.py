import base64
import hashlib
import http.client
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from typing import cast

import pytest

from skilleval import gateway
from skilleval.errors import SkillEvalError
from skilleval.gateway import (
    ADAPTED_SHA256,
    EFFORT,
    MAX_RECEIPTS,
    MODEL,
    UPSTREAM_SHA256,
    GatewayLimits,
    broker_scope,
    codex_provider_config,
    load_chatgpt_auth,
    relay_scope,
)

# Real-socket controls adapted from OpenBench's MIT-licensed gateway tests at
# 39fec903f1e73bd3e8e3b15cacefbcce051bd1a6. See _vendor/OPENBENCH_LICENSE.
BODY: dict[str, object] = {
    "model": MODEL,
    "reasoning": {"effort": EFFORT},
    "store": False,
    "stream": True,
    "input": [{"role": "user", "content": [{"type": "input_text", "text": "local task"}]}],
}
LIMITS = GatewayLimits(max_requests=2, timeout_seconds=0.5, lifetime_seconds=10)
EVENT = (
    b'data: {"type":"response.completed","response":{"usage":'
    b'{"input_tokens":3,"output_tokens":2,"total_tokens":5}}}\n\n'
)


class Stream:
    status = 200

    def __init__(self, body: bytes = EVENT, content_type: str = "text/event-stream") -> None:
        self.headers = {"Content-Type": content_type}
        self.body = io.BytesIO(body)
        self.closed = False

    def read(self, size: int) -> bytes:
        return self.body.read(size)

    def close(self) -> None:
        self.closed = True


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: Path) -> None:
        super().__init__("localhost", timeout=3)
        self.socket_path = path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(self.socket_path))


def request(
    socket_path: Path,
    body: dict[str, object] = BODY,
    *,
    route: str = "/responses",
    method: str = "POST",
    port: int | None = None,
) -> tuple[int, bytes]:
    connection = (
        UnixConnection(socket_path)
        if port is None
        else http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    )
    try:
        # An early policy refusal can close while http.client is still sending
        # the body. Read that response once; never retry or dispatch another call.
        with suppress(BrokenPipeError):
            connection.request(
                method, route, json.dumps(body), {"Content-Type": "application/json"}
            )
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


@pytest.fixture
def short_root() -> Iterator[Path]:
    # macOS AF_UNIX paths cannot fit pytest's full generated directory name.
    with tempfile.TemporaryDirectory(prefix="segw-", dir="/tmp") as name:
        yield Path(name)


def write_auth(root: Path, expiration: object) -> Path:
    payload = (
        base64.urlsafe_b64encode(json.dumps({"exp": expiration}).encode()).decode().rstrip("=")
    )
    auth = root / "synthetic-auth.json"
    auth.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": f"synthetic.{payload}.private-synthetic-token",
                    "account_id": "private-synthetic-account",
                }
            }
        )
    )
    return auth


def test_auth_is_private_and_freshness_is_required(tmp_path: Path) -> None:
    auth = load_chatgpt_auth(write_auth(tmp_path, time.time() + 600))
    assert repr(auth) == "HostAuth(<private>)"
    assert "private-synthetic" not in repr(auth)
    with pytest.raises(SkillEvalError, match="unavailable or expired"):
        load_chatgpt_auth(write_auth(tmp_path, time.time() - 1))


def test_request_observer_sees_actual_body_without_auth_headers(short_root: Path) -> None:
    auth = load_chatgpt_auth(write_auth(short_root, time.time() + 600))
    observed: list[bytes] = []
    with broker_scope(
        auth,
        short_root / "observer",
        limits=LIMITS,
        test_transport=lambda *_: Stream(),
        request_observer=observed.append,
    ) as broker:
        assert request(broker.socket_path)[0] == 200
    assert observed == [json.dumps(BODY).encode()]
    assert b"private-synthetic" not in observed[0]


def test_request_observer_failure_refuses_provider_call(short_root: Path) -> None:
    auth = load_chatgpt_auth(write_auth(short_root, time.time() + 600))
    sent: list[bytes] = []

    def fail(_: bytes) -> None:
        raise RuntimeError("PRIVATE_FAILURE")

    def transport(body: bytes, *_: object) -> Stream:
        sent.append(body)
        return Stream()

    with broker_scope(
        auth,
        short_root / "observer",
        limits=LIMITS,
        test_transport=transport,
        request_observer=fail,
    ) as broker:
        status, body = request(broker.socket_path)
        assert status == 503
        assert b"PRIVATE_FAILURE" not in body
    assert not sent


@pytest.mark.parametrize("expiration", [None, True, "private-value", 0, float("nan"), 10**400])
def test_invalid_auth_expiry_fails_safely(tmp_path: Path, expiration: object) -> None:
    with pytest.raises(SkillEvalError) as error:
        load_chatgpt_auth(write_auth(tmp_path, expiration))
    assert "private-" not in str(error.value)
    assert str(tmp_path) not in str(error.value)


def test_missing_symlink_duplicate_and_api_key_auth_are_refused(tmp_path: Path) -> None:
    missing = tmp_path / "absent"
    with pytest.raises(SkillEvalError):
        load_chatgpt_auth(missing)
    auth_file = write_auth(tmp_path, time.time() + 600)
    link = tmp_path / "link"
    link.symlink_to(auth_file)
    with pytest.raises(SkillEvalError):
        load_chatgpt_auth(link)
    auth_file.write_text('{"tokens":{},"tokens":{}}')
    with pytest.raises(SkillEvalError):
        load_chatgpt_auth(auth_file)


def test_auth_fifo_is_refused_without_waiting_for_a_writer(tmp_path: Path) -> None:
    fifo = tmp_path / "synthetic-fifo"
    os.mkfifo(fifo)
    code = (
        "from pathlib import Path; from skilleval.gateway import load_chatgpt_auth; "
        "from skilleval.errors import SkillEvalError\n"
        "try: load_chatgpt_auth(Path(__import__('sys').argv[1]))\n"
        "except SkillEvalError: print('safe_refusal')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(fifo)],
        capture_output=True,
        timeout=2,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == "safe_refusal\n" and not result.stderr
    auth_file = write_auth(tmp_path, time.time() + 600)
    document = json.loads(auth_file.read_text())
    document["auth_mode"] = "apikey"
    auth_file.write_text(json.dumps(document))
    with pytest.raises(SkillEvalError):
        load_chatgpt_auth(auth_file)


def test_vendor_bytes_and_fixed_native_configuration() -> None:
    vendor = Path(gateway.__file__).parent / "_vendor" / "openbench_gateway.py"
    assert hashlib.sha256(vendor.read_bytes()).hexdigest() == ADAPTED_SHA256
    provenance = json.loads(vendor.with_suffix(".provenance.json").read_text())
    assert provenance["upstream_sha256"] == UPSTREAM_SHA256
    assert provenance["adapted_sha256"] == ADAPTED_SHA256
    config = tomllib.loads(codex_provider_config(8123, Path("/run/models.json")))
    assert config["model"] == "gpt-6.1-sol"
    assert config["model_reasoning_effort"] == "high"
    assert config["model_catalog_json"] == "/run/models.json"
    provider = config["model_providers"]["qualification-broker"]
    assert provider == {
        "name": "skilleval qualification",
        "base_url": "http://127.0.0.1:8123",
        "wire_api": "responses",
        "requires_openai_auth": False,
        "supports_websockets": False,
    }
    assert config["web_search"] == "disabled"
    assert not config["features"]["output_token_replay"]
    assert not config["features"]["concurrent_reasoning_summaries"]


def test_real_unix_and_loopback_relay_keep_auth_on_host(short_root: Path) -> None:
    auth_file = write_auth(short_root, time.time() + 600)
    auth = load_chatgpt_auth(auth_file)
    seen: list[tuple[dict[str, object], dict[str, str]]] = []
    streams: list[Stream] = []

    def transport(body: bytes, headers: dict[str, str], timeout: float) -> Stream:
        assert timeout == LIMITS.timeout_seconds
        seen.append((json.loads(body), headers))
        streams.append(Stream())
        streams[-1].peer = {"ip": "203.0.113.1", "port": 443}
        return streams[-1]

    private = short_root / "b"
    with broker_scope(auth, private, limits=LIMITS, test_transport=transport) as broker:
        assert private.stat().st_mode & 0o777 == 0o700
        with relay_scope(broker.socket_path, limits=LIMITS) as relay:
            status, body = request(broker.socket_path, port=relay.port)
            assert status == 200 and body == EVENT
            assert "private-synthetic" not in body.decode()
        assert relay.clean is True
    assert broker.clean is True
    assert not private.exists()
    assert streams[0].closed
    assert seen[0][0] == BODY
    assert seen[0][1]["Authorization"].endswith(".private-synthetic-token")
    assert seen[0][1]["ChatGPT-Account-Id"] == "private-synthetic-account"
    receipts = json.dumps(broker.receipts)
    assert "private-synthetic" not in receipts
    assert "local task" not in receipts
    request_rows = [row for row in broker.receipts if row["event"] == "request"]
    assert request_rows[0]["usage"] == {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
    assert request_rows[0]["upstream_peer"] == {"ip": "203.0.113.1", "port": 443}


def test_denials_do_not_dispatch_but_local_tool_history_does(short_root: Path) -> None:
    auth = load_chatgpt_auth(write_auth(short_root, time.time() + 600))
    seen: list[dict[str, object]] = []

    def transport(body: bytes, _headers: dict[str, str], _timeout: float) -> Stream:
        seen.append(json.loads(body))
        return Stream()

    with broker_scope(auth, short_root / "b", limits=LIMITS, test_transport=transport) as broker:
        assert request(broker.socket_path)[0] == 200
        for bad in (
            {**BODY, "model": "another-model"},
            {**BODY, "reasoning": {"effort": "low"}},
            {**BODY, "tools": [{"type": "web_search"}]},
            {**BODY, "input": [{"type": "item_reference", "id": "remote"}]},
            {**BODY, "store": True},
        ):
            assert request(broker.socket_path, bad)[0] == 400
        assert request(broker.socket_path, route="/responses?upstream=other")[0] == 404
        assert request(broker.socket_path, method="CONNECT")[0] == 405
        assert len(seen) == 1
        local = {
            **BODY,
            "input": [
                {
                    "type": "additional_tools",
                    "role": "developer",
                    "tools": [
                        {
                            "type": "namespace",
                            "name": "functions",
                            "tools": [
                                {
                                    "type": "function",
                                    "name": "exec",
                                    "parameters": {
                                        "type": "object",
                                        "properties": {"cmd": {"type": "string"}},
                                    },
                                }
                            ],
                        }
                    ],
                },
                {
                    "type": "function_call",
                    "call_id": "c",
                    "namespace": "functions",
                    "name": "exec",
                    "arguments": "{}",
                },
                {"type": "function_call_output", "call_id": "c", "output": "local exit 0"},
            ],
        }
        assert request(broker.socket_path, local)[0] == 200
        assert request(broker.socket_path)[0] == 429
        assert len(seen) == 2


def test_native_empty_output_text_metadata_replays_without_changing_bytes(short_root: Path) -> None:
    auth = load_chatgpt_auth(write_auth(short_root, time.time() + 600))
    seen: list[bytes] = []

    def transport(body: bytes, _headers: dict[str, str], _timeout: float) -> Stream:
        seen.append(body)
        return Stream()

    body = {
        **BODY,
        "input": [
            {
                "type": "message",
                "role": "assistant",
                "phase": "commentary",
                "content": [
                    {
                        "type": "output_text",
                        "text": "A local command follows.",
                        "annotations": [],
                        "logprobs": [],
                    }
                ],
            }
        ],
    }
    with broker_scope(auth, short_root / "b", limits=LIMITS, test_transport=transport) as broker:
        assert request(broker.socket_path, body)[0] == 200
        assert seen == [json.dumps(body).encode()]
        for content in (
            {
                "type": "output_text",
                "text": "text",
                "annotations": [{"url": "https://example.invalid"}],
            },
            {"type": "output_text", "text": "text", "logprobs": [0]},
            {"type": "output_text", "text": "text", "annotations": None},
            {"type": "input_text", "text": "text", "annotations": []},
            {"type": "output_text", "text": "text", "unknown_field": []},
        ):
            denied = {**BODY, "input": [{"role": "assistant", "content": [content]}]}
            assert request(broker.socket_path, denied)[0] == 400
        assert len(seen) == 1


def test_expiration_between_requests_fails_without_refresh_or_upstream(
    short_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = time.time()
    auth = load_chatgpt_auth(write_auth(short_root, initial + 600))
    seen: list[bytes] = []

    def transport(body: bytes, _headers: dict[str, str], _timeout: float) -> Stream:
        seen.append(body)
        return Stream()

    with broker_scope(auth, short_root / "b", limits=LIMITS, test_transport=transport) as broker:
        assert request(broker.socket_path)[0] == 200
        monkeypatch.setattr(gateway.time, "time", lambda: initial + 601)
        status, body = request(broker.socket_path)
        assert status == 503 and b"authentication_expired" in body
        assert len(seen) == 1


def test_transport_errors_and_wrong_responses_never_echo_secrets(short_root: Path) -> None:
    auth = load_chatgpt_auth(write_auth(short_root, time.time() + 600))

    def transport(_body: bytes, _headers: dict[str, str], _timeout: float) -> Stream:
        raise OSError("private-synthetic-token")

    with broker_scope(auth, short_root / "b", limits=LIMITS, test_transport=transport) as broker:
        status, body = request(broker.socket_path)
        assert status == 502 and b"private-synthetic" not in body
    assert "private-synthetic" not in json.dumps(broker.receipts)

    def wrong_response(_body: bytes, _headers: dict[str, str], _timeout: float) -> Stream:
        return Stream(b"private-synthetic-token", "application/json")

    with broker_scope(
        auth, short_root / "c", limits=LIMITS, test_transport=wrong_response
    ) as broker:
        status, body = request(broker.socket_path)
        assert status == 502 and b"private-synthetic" not in body


def test_duplicate_headers_and_compression_never_dispatch(short_root: Path) -> None:
    auth = load_chatgpt_auth(write_auth(short_root, time.time() + 600))
    seen: list[bytes] = []

    def transport(body: bytes, _headers: dict[str, str], _timeout: float) -> Stream:
        seen.append(body)
        return Stream()

    with broker_scope(auth, short_root / "b", limits=LIMITS, test_transport=transport) as broker:
        assert request(broker.socket_path)[0] == 200
        for headers in (
            "Content-Length: 0\r\nContent-Length: 0\r\n",
            "Content-Length: 0\r\nContent-Encoding: gzip\r\n",
            "Content-Length: 0\r\nUpgrade: websocket\r\n",
            "Transfer-Encoding: chunked\r\n",
        ):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(3)
                connection.connect(str(broker.socket_path))
                connection.sendall(
                    (
                        "POST /responses HTTP/1.1\r\nHost: localhost\r\n"
                        "Content-Type: application/json\r\n" + headers + "\r\n"
                    ).encode()
                )
                assert connection.recv(4096).startswith(b"HTTP/1.1 400")
        assert len(seen) == 1


def test_scope_exit_cancels_active_stream_and_observes_cleanup(short_root: Path) -> None:
    auth = load_chatgpt_auth(write_auth(short_root, time.time() + 600))
    reading = threading.Event()
    closed = threading.Event()

    class StalledStream(Stream):
        def read(self, _size: int) -> bytes:
            reading.set()
            assert closed.wait(3)
            return b""

        def close(self) -> None:
            closed.set()
            super().close()

    def transport(_body: bytes, _headers: dict[str, str], _timeout: float) -> Stream:
        return StalledStream()

    limits = GatewayLimits(timeout_seconds=2)

    def client(path: Path) -> None:
        with suppress(OSError, http.client.HTTPException):
            request(path)

    with broker_scope(auth, short_root / "b", limits=limits, test_transport=transport) as broker:
        thread = threading.Thread(target=client, args=(broker.socket_path,))
        thread.start()
        assert reading.wait(1)
    thread.join(3)
    assert closed.is_set() and not thread.is_alive()
    assert broker.clean is True
    assert any(row.get("outcome") == "revoked" for row in broker.receipts)


def test_startup_and_sink_errors_are_safe_and_remove_socket(short_root: Path) -> None:
    auth = load_chatgpt_auth(write_auth(short_root, time.time() + 600))
    existing = short_root / "existing"
    existing.mkdir()
    sentinel = existing / "keep"
    sentinel.write_text("keep")
    with pytest.raises(SkillEvalError, match="startup failed"), broker_scope(auth, existing):
        pytest.fail("must not start")
    assert sentinel.read_text() == "keep"

    def fail(_record: dict[str, object]) -> None:
        raise OSError("private-synthetic-sink-detail")

    private = short_root / "failed"
    with pytest.raises(SkillEvalError) as error, broker_scope(auth, private, receipt=fail):
        pytest.fail("must not yield without receipt")
    assert "private-synthetic" not in str(error.value)
    assert not private.exists()


def test_lifetime_revokes_socket_and_cleanup_is_observed(short_root: Path) -> None:
    auth = load_chatgpt_auth(write_auth(short_root, time.time() + 600))
    limits = GatewayLimits(timeout_seconds=0.1, lifetime_seconds=0.1)
    with broker_scope(auth, short_root / "b", limits=limits) as broker:
        deadline = time.monotonic() + 2
        while broker.socket_path.exists() and time.monotonic() < deadline:
            threading.Event().wait(0.01)
        assert not broker.socket_path.exists()
    assert broker.clean is True


def test_receipt_flood_revokes_with_bounded_memory_and_no_extra_inference(short_root: Path) -> None:
    auth = load_chatgpt_auth(write_auth(short_root, time.time() + 600))
    seen: list[bytes] = []

    def transport(body: bytes, _headers: dict[str, str], _timeout: float) -> Stream:
        seen.append(body)
        return Stream()

    private = short_root / "b"
    with (
        pytest.raises(SkillEvalError, match="cleanup incomplete"),
        broker_scope(
            auth,
            private,
            limits=GatewayLimits(max_requests=1, timeout_seconds=1),
            test_transport=transport,
        ) as broker,
    ):
        assert request(broker.socket_path)[0] == 200
        for _ in range(MAX_RECEIPTS * 4):
            if not broker.socket_path.exists():
                break
            try:
                request(broker.socket_path, method="GET")
            except (OSError, http.client.HTTPException):
                if not broker.socket_path.exists():
                    break
    assert len(broker.receipts) <= MAX_RECEIPTS
    assert len(seen) == 1
    assert broker.clean is False and not private.exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"max_requests": 33},
        {"timeout_seconds": 0},
        {"lifetime_seconds": float("nan")},
        {"max_requests": True},
    ],
)
def test_limits_are_bounded(changes: dict[str, object]) -> None:
    with pytest.raises(SkillEvalError):
        GatewayLimits(**cast(dict, changes))
