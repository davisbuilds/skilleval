"""Shared trusted child execution and bounded pipe collection inside the guest."""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import time
from contextlib import nullcontext, suppress
from pathlib import Path
from typing import Any, Protocol


class ProcessProtocol(Protocol):
    """Optional interactive wire protocol; evidence projection belongs to its caller."""

    @property
    def settled(self) -> bool: ...

    def start(self) -> list[dict[str, Any]]: ...

    def receive(self, record: dict[str, Any]) -> list[dict[str, Any]]: ...


def _stop(process: subprocess.Popen[bytes]) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=3)
    # The leader may exit on TERM while its descendants ignore it. Always kill
    # the original group before collecting mutable guest artifacts.
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=3)


def _execute_native(
    argv: list[str],
    environment: dict[str, str],
    prompt: Path,
    work: Path,
    evidence: Path,
    timeout: int,
    *,
    stream_limit_bytes: int | None = None,
    protocol: ProcessProtocol | None = None,
    stdout_name: str = "events.jsonl",
    input_name: str | None = None,
) -> dict[str, Any]:
    """Keep controller file descriptors outside the child; stream bounded pipes.

    Namespace-wide teardown is the boundary launcher's responsibility. The
    process-group fallback here also serves the trusted relay and host tests.
    """
    with (
        (evidence / stdout_name).open("wb") as stdout,
        (evidence / "stderr.log").open("wb") as stderr,
        (
            (evidence / input_name).open("wb") if input_name is not None else nullcontext()
        ) as rpc_input,
        selectors.DefaultSelector() as selector,
    ):
        pending = b"" if protocol else prompt.read_bytes()
        received = b""
        protocol_error = None

        def enqueue(messages: list[dict[str, Any]]) -> None:
            nonlocal pending
            for message in messages:
                pending += json.dumps(message, ensure_ascii=False).encode() + b"\n"

        if protocol:
            enqueue(protocol.start())
        started = time.monotonic()
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=work,
            env=environment,
            start_new_session=True,
            close_fds=True,
        )
        assert process.stdin and process.stdout and process.stderr
        for stream in (process.stdin, process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
        selector.register(process.stdin, selectors.EVENT_WRITE, None)
        selector.register(process.stdout, selectors.EVENT_READ, stdout)
        selector.register(process.stderr, selectors.EVENT_READ, stderr)
        timed_out = False
        truncated = False
        drain_incomplete = False
        stopped_at = None
        try:
            while selector.get_map():
                now = time.monotonic()
                if stopped_at is None and (
                    process.poll() is not None or now - started >= timeout or protocol_error
                ):
                    timed_out = process.poll() is None
                    if protocol_error:
                        timed_out = False
                    _stop(process)
                    stopped_at = time.monotonic()
                if stopped_at is not None and now - stopped_at > 1:
                    drain_incomplete = True
                    break
                for key, _ in selector.select(timeout=0.05):
                    if key.data is None:
                        if pending:
                            try:
                                written = os.write(key.fd, pending[:4096])
                                if rpc_input is not None:
                                    rpc_input.write(pending[:written])
                                pending = pending[written:]
                            except BrokenPipeError:
                                pending = b""
                                if protocol:
                                    protocol_error = "app-server input closed early"
                        if not pending:
                            selector.unregister(key.fileobj)
                            if protocol is None or protocol.settled or protocol_error:
                                process.stdin.close()
                        continue
                    block = os.read(key.fd, 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    output = key.data
                    if stream_limit_bytes is not None:
                        available = max(0, stream_limit_bytes - output.tell())
                        truncated |= len(block) > available
                        block = block[:available]
                    output.write(block)
                    if protocol and output is stdout and not protocol_error:
                        received += block
                        if truncated or len(received) > 4 * 1024 * 1024:
                            protocol_error = "app-server output limit exceeded"
                            continue
                        try:
                            while b"\n" in received:
                                line, received = received.split(b"\n", 1)
                                enqueue(protocol.receive(json.loads(line)))
                            if pending and not process.stdin.closed:
                                if process.stdin not in selector.get_map():
                                    selector.register(process.stdin, selectors.EVENT_WRITE, None)
                            elif protocol.settled and not process.stdin.closed:
                                process.stdin.close()
                        except (ValueError, UnicodeError) as error:
                            protocol_error = str(error)[:1024]
            # A process can close all streams before it exits.
            if stopped_at is None:
                try:
                    process.wait(timeout=max(0.001, timeout - (time.monotonic() - started)))
                except subprocess.TimeoutExpired:
                    timed_out = True
        finally:
            _stop(process)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
    result = {
        "returncode": process.returncode,
        "timed_out": timed_out,
        "agent_elapsed_seconds": time.monotonic() - started,
        "output_limit_reached": truncated,
        "pipe_drain_incomplete": drain_incomplete,
    }
    if protocol:
        if received and protocol_error is None:
            protocol_error = "incomplete app-server JSON line"
        result["protocol_error"] = protocol_error
    return result


NATIVE_PAX_PATH_BYTES = 4096


def _pax_path_size(name: str) -> int:
    payload = len(("path=" + name + "\n").encode("utf-8"))
    length = payload + 2
    while (actual := payload + len(str(length)) + 1) != length:
        length = actual
    return length
