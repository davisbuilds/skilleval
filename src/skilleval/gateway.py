"""Explicit host-only ChatGPT authentication for a bounded native qualification.

No function here runs automatically from ordinary checks. Callers explicitly
choose the auth file; importing this module never inspects login or environment.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import math
import os
import stat
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast

from skilleval._vendor import openbench_gateway as _vendor
from skilleval.errors import SkillEvalError
from skilleval.harnesses.codex import CODEX_BASELINE_VERSION

CODEX_VERSION = CODEX_BASELINE_VERSION
MODEL = "gpt-6.1-sol"
EFFORT = "high"
MAX_RECEIPTS = 256
UPSTREAM_COMMIT = "39fec903f1e73bd3e8e3b15cacefbcce051bd1a6"
UPSTREAM_SHA256 = "ed02f42cc52a22cfd16d49e3d639a27ce66de0b3bc3a9f41758c646bb26aed48"
ADAPTED_SHA256 = "5159253383750db2583a51ea6789ff9e7cb897dfb7c73e2629293c84bd5ef5a0"


class UpstreamResponse(Protocol):
    """Synthetic transport seam; production always uses the fixed HTTPS upstream."""

    status: int
    headers: Mapping[str, str]

    def read(self, size: int) -> bytes: ...

    def close(self) -> None: ...


TestTransport = Callable[[bytes, dict[str, str], float], UpstreamResponse]
Receipt = dict[str, object]
ReceiptSink = Callable[[Receipt], None]


class _Handler(Protocol):
    def _error(self, status: int, code: str) -> None: ...


class _Server(Protocol):
    forward: Callable[[_Handler, bytes], None]
    receipt: ReceiptSink
    server_address: object
    _receipt_failed: bool
    _drain_incomplete: bool
    _stop_complete: threading.Event

    def serve_forever(self, poll_interval: float = ...) -> None: ...

    def stop(self) -> None: ...

    def server_close(self) -> None: ...


class _VendorBoundary(Protocol):
    def _json(self, value: bytes) -> object: ...

    def AuthCredentials(self, access_token: str, account_id: str) -> object: ...  # noqa: N802

    def GatewayConfig(  # noqa: N802
        self,
        model: str,
        effort: str,
        max_requests: int,
        max_body_bytes: int,
        timeout_seconds: float,
        max_concurrent_requests: int,
    ) -> object: ...

    def BrokerServer(  # noqa: N802
        self,
        socket_path: Path,
        config: object,
        auth: object,
        *,
        transport: TestTransport | None,
        receipt: ReceiptSink,
    ) -> _Server: ...

    def RelayServer(  # noqa: N802
        self,
        address: tuple[str, int],
        socket_path: Path,
        *,
        max_body_bytes: int,
        timeout_seconds: float,
    ) -> _Server: ...


_upstream = cast(_VendorBoundary, _vendor)


def _read_auth_document(path: Path) -> object:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 128 * 1024:
            raise ValueError
        raw = source.read(128 * 1024 + 1)
        after = os.fstat(source.fileno())
        if len(raw) > 128 * 1024 or any(
            getattr(before, key) != getattr(after, key)
            for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        ):
            raise ValueError
        return _upstream._json(raw)


@dataclass(frozen=True, repr=False)
class HostAuth:
    """Private in-memory bearer material; never serialize or transfer to guest."""

    _access_token: str = field(repr=False)
    _account_id: str = field(repr=False)
    expires_at: float

    def __repr__(self) -> str:
        return "HostAuth(<private>)"


def load_chatgpt_auth(path: Path, *, minimum_validity_seconds: float = 30) -> HostAuth:
    """Load explicitly selected stored ChatGPT auth without refreshing it.

    JWT decoding checks freshness only; the fixed HTTPS upstream authenticates
    the credential. No token, claims, account identity, or path enters errors.
    """
    if (
        isinstance(minimum_validity_seconds, bool)
        or not isinstance(minimum_validity_seconds, (int, float))
        or not math.isfinite(minimum_validity_seconds)
        or not 0 <= minimum_validity_seconds <= 3600
    ):
        raise SkillEvalError("invalid authentication validity requirement")
    try:
        document = _read_auth_document(path)
        if not isinstance(document, dict) or document.get("auth_mode") not in (None, "chatgpt"):
            raise ValueError
        tokens = document["tokens"]
        token, account = tokens["access_token"], tokens["account_id"]
        # Reuse upstream's strict ASCII/header validation before retaining values.
        _upstream.AuthCredentials(token, account)
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError
        payload = base64.b64decode(
            parts[1] + "=" * (-len(parts[1]) % 4), altchars=b"-_", validate=True
        )
        claims = _upstream._json(payload)
        if not isinstance(claims, dict):
            raise ValueError
        expires_at = claims["exp"]
        if (
            isinstance(expires_at, bool)
            or not isinstance(expires_at, (int, float))
            or not math.isfinite(expires_at)
            or expires_at <= time.time() + minimum_validity_seconds
        ):
            raise ValueError
        return HostAuth(token, account, float(expires_at))
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        binascii.Error,
        RecursionError,
        OverflowError,
    ):
        raise SkillEvalError("ChatGPT authentication unavailable or expired") from None


@dataclass(frozen=True)
class GatewayLimits:
    """Small explicit qualification ceilings, independent of campaign admission."""

    max_requests: int = 4
    max_body_bytes: int = 4 * 1024 * 1024
    timeout_seconds: float = 60
    max_concurrent_requests: int = 1
    lifetime_seconds: float = 180

    def __post_init__(self) -> None:
        for value, ceiling in (
            (self.max_requests, 32),
            (self.max_body_bytes, 8 * 1024 * 1024),
            (self.max_concurrent_requests, 2),
        ):
            if type(value) is not int or not 1 <= value <= ceiling:
                raise SkillEvalError("invalid gateway limit")
        for duration, maximum in ((self.timeout_seconds, 300), (self.lifetime_seconds, 900)):
            if (
                isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or not math.isfinite(duration)
                or not 0 < duration <= maximum
            ):
                raise SkillEvalError("invalid gateway deadline")


DEFAULT_LIMITS = GatewayLimits()


class _Receipts:
    """Only known scalar metadata survives the untyped implementation boundary."""

    def __init__(self, sink: ReceiptSink | None) -> None:
        self._sink = sink
        self._records: list[Receipt] = []
        self._lock = threading.Lock()

    def record(self, incoming: Receipt) -> None:
        allowed = {
            "event",
            "request_id",
            "started_at_unix_seconds",
            "elapsed_seconds",
            "timeout_seconds",
            "model",
            "effort",
            "input_bytes",
            "response_bytes",
            "outcome",
            "upstream_status",
            "status",
            "code",
            "role",
            "clean",
        }
        record: Receipt = {
            key: value[:256] if isinstance(value, str) else value
            for key, value in incoming.items()
            if key in allowed and (value is None or isinstance(value, (str, int, float, bool)))
        }
        usage = incoming.get("usage")
        if isinstance(usage, dict):
            record["usage"] = {
                key: value
                for key, value in usage.items()
                if key in {"input_tokens", "output_tokens", "total_tokens"}
                and type(value) is int
                and value >= 0
            }
        peer = incoming.get("upstream_peer")
        if isinstance(peer, dict):
            address, port = peer.get("ip"), peer.get("port")
            if (
                isinstance(address, str)
                and "%" not in address
                and type(port) is int
                and 1 <= port <= 65535
            ):
                with suppress(ValueError):
                    record["upstream_peer"] = {
                        "ip": str(ipaddress.ip_address(address)),
                        "port": port,
                    }
        with self._lock:
            if len(self._records) >= MAX_RECEIPTS:
                raise SkillEvalError("gateway receipt limit reached")
            self._records.append(record)
        if self._sink is not None:
            try:
                self._sink(cast(Receipt, json.loads(json.dumps(record))))
            except Exception:
                raise SkillEvalError("gateway receipt unavailable") from None

    def snapshot(self) -> list[Receipt]:
        with self._lock:
            return cast(list[Receipt], json.loads(json.dumps(self._records)))


@dataclass
class GatewayHandle:
    """Metadata-only service handle; auth material is deliberately absent."""

    socket_path: Path
    port: int | None
    _receipts: _Receipts = field(repr=False)
    clean: bool | None = None

    @property
    def receipts(self) -> list[Receipt]:
        return self._receipts.snapshot()


@contextmanager
def _running(
    server: _Server,
    handle: GatewayHandle,
    lifetime_seconds: float,
) -> Iterator[GatewayHandle]:
    failed = threading.Event()

    def serve() -> None:
        try:
            server.serve_forever(poll_interval=0.05)
        except Exception:
            failed.set()

    thread = threading.Thread(target=serve, name="skilleval-inference", daemon=True)
    timer = threading.Timer(lifetime_seconds, server.stop)
    timer.daemon = True
    try:
        thread.start()
        timer.start()
        handle._receipts.record(
            {"event": "ready", "role": "broker" if handle.port is None else "relay"}
        )
        yield handle
    finally:
        timer.cancel()
        server.stop()
        server._stop_complete.wait(3)
        thread.join(timeout=3)
        handle.clean = not (
            thread.is_alive()
            or failed.is_set()
            or server._receipt_failed
            or server._drain_incomplete
        )
        try:
            handle._receipts.record({"event": "stopped", "clean": handle.clean})
        except Exception:
            handle.clean = False
        if not handle.clean:
            raise SkillEvalError("gateway cleanup incomplete")


@contextmanager
def broker_scope(
    auth: HostAuth,
    private_directory: Path,
    *,
    limits: GatewayLimits = DEFAULT_LIMITS,
    socket_path: Path | None = None,
    receipt: ReceiptSink | None = None,
    test_transport: TestTransport | None = None,
    request_observer: Callable[[bytes], None] | None = None,
) -> Iterator[GatewayHandle]:
    """Serve one bounded host-only UDS capability; always stop and remove it.

    The directory must be fresh. Only the socket enters the VM. ``test_transport``
    is an in-process test seam, never an endpoint or a CLI-selectable destination.
    """
    if (
        not math.isfinite(auth.expires_at)
        or auth.expires_at <= time.time() + limits.timeout_seconds + 30
    ):
        raise SkillEvalError("ChatGPT authentication unavailable or expired")
    socket_path = socket_path or private_directory / "inference.sock"
    if (
        not private_directory.is_absolute()
        or not socket_path.is_absolute()
        or socket_path.parent != private_directory
        or ".." in socket_path.parts
        or len(os.fsencode(socket_path)) > 100
    ):
        raise SkillEvalError("gateway requires a short socket path in a fresh private directory")
    created = False
    server: _Server | None = None
    records = _Receipts(receipt)
    try:
        private_directory.mkdir(mode=0o700)
        created = True
        server = _upstream.BrokerServer(
            socket_path,
            _upstream.GatewayConfig(
                MODEL,
                EFFORT,
                limits.max_requests,
                limits.max_body_bytes,
                limits.timeout_seconds,
                limits.max_concurrent_requests,
            ),
            _upstream.AuthCredentials(auth._access_token, auth._account_id),
            transport=test_transport,
            receipt=records.record,
        )
    except (OSError, ValueError):
        if created:
            try:
                private_directory.rmdir()
            except OSError:
                raise SkillEvalError("gateway startup failed; cleanup incomplete") from None
        raise SkillEvalError("gateway startup failed") from None
    original_forward = server.forward
    observation_failed = threading.Event()

    def forward(handler: _Handler, body: bytes) -> None:
        if observation_failed.is_set():
            handler._error(503, "request_observation_failed")
            return
        if auth.expires_at <= time.time() + limits.timeout_seconds + 5:
            handler._error(503, "authentication_expired")
            return
        if request_observer is not None:
            try:
                # Body bytes contain guest-supplied task/history, never the host
                # authorization headers constructed by the forwarding transport.
                request_observer(body)
            except Exception:
                observation_failed.set()
                records.record({"event": "request_observation_failed", "outcome": "refused"})
                handler._error(503, "request_observation_failed")
                return
        original_forward(handler, body)

    server.forward = forward
    handle = GatewayHandle(socket_path, None, records)
    try:
        with _running(server, handle, limits.lifetime_seconds):
            yield handle
    finally:
        try:
            private_directory.rmdir()
        except OSError:
            handle.clean = False
            raise SkillEvalError("gateway cleanup incomplete") from None


@contextmanager
def relay_scope(
    socket_path: Path,
    *,
    port: int = 0,
    limits: GatewayLimits = DEFAULT_LIMITS,
) -> Iterator[GatewayHandle]:
    """Guest-side loopback relay to one explicitly mounted inference socket."""
    if not socket_path.is_absolute() or ".." in socket_path.parts:
        raise SkillEvalError("relay socket must be absolute")
    if type(port) is not int or not 0 <= port <= 65535:
        raise SkillEvalError("invalid relay port")
    records = _Receipts(None)
    try:
        server = _upstream.RelayServer(
            ("127.0.0.1", port),
            socket_path,
            max_body_bytes=limits.max_body_bytes,
            timeout_seconds=limits.timeout_seconds,
        )
    except (OSError, ValueError):
        raise SkillEvalError("relay startup failed") from None
    server.receipt = records.record
    bound_port = cast(tuple[str, int], server.server_address)[1]
    handle = GatewayHandle(socket_path, bound_port, records)
    with _running(server, handle, limits.lifetime_seconds):
        yield handle


def codex_provider_config(port: int, catalog_path: Path) -> str:
    """Render a credential-free, pinned native guest profile for this route."""
    if type(port) is not int or not 1 <= port <= 65535:
        raise SkillEvalError("invalid relay port")
    if not catalog_path.is_absolute() or ".." in catalog_path.parts:
        raise SkillEvalError("model catalog must use an absolute guest path")
    return f'''model = "{MODEL}"
model_provider = "local-gateway"
model_reasoning_effort = "{EFFORT}"
model_reasoning_summary = "none"
model_catalog_json = {json.dumps(str(catalog_path))}
model_auto_compact_token_limit = 1000000
cli_auth_credentials_store = "ephemeral"
web_search = "disabled"
approval_policy = "never"
sandbox_mode = "danger-full-access"

[features]
output_token_replay = false
concurrent_reasoning_summaries = false
enable_request_compression = false
apps = false
hooks = false
plugins = false
memories = false
shell_snapshot = false

[model_providers.local-gateway]
name = "Local gateway"
base_url = "http://127.0.0.1:{port}"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = false
'''
