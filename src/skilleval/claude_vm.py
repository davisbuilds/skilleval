"""Trusted stdio MCP capability for one explicitly selected disposable VM.

The native host Claude process owns its own authentication. This server never
reads authentication or runs model-selected commands on the host.
"""

from __future__ import annotations

import json
import math
import os
import select
import stat
import sys
import time
from pathlib import Path
from typing import Any, BinaryIO

from skilleval import (
    app_server,
    claude_vm_guest,
    context_guard,
    guest_runner,
    native_process,
    solver_boundary,
)
from skilleval.errors import SkillEvalError
from skilleval.evidence import _parse_json
from skilleval.lifecycle import (
    Lifecycle,
    LifecycleInterruptedError,
    interrupt_scope,
    successful,
)
from skilleval.task_transfer import GUEST_ROOT, GUEST_WORK

COMMAND_BYTES = 16 * 1024
MCP_INPUT_BYTES = 128 * 1024
TOOLS_LOG_BYTES = 64 * 1024 * 1024
TOOL = {
    "name": "execute",
    "description": (
        "Run a shell command inside the selected isolated, network-off VM. "
        "The working directory is the supplied evidence workspace. Host files "
        "and credentials are unavailable. Output is bounded and truncation explicit."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "minLength": 1, "maxLength": COMMAND_BYTES},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 30, "default": 30},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
}


def _validate_command(command: object, timeout: object) -> None:
    if (
        not isinstance(command, str)
        or not command.strip()
        or "\0" in command
        or len(command.encode()) > COMMAND_BYTES
        or type(timeout) is not int
        or not 1 <= timeout <= 30
    ):
        raise SkillEvalError("invalid bounded VM command")


def _guest(
    session: Lifecycle, operation: str, parameters: list[str], timeout: int
) -> dict[str, Any]:
    result = session.guest(
        "claude_vm_" + operation,
        [
            "python3",
            GUEST_ROOT + "/claude_vm_guest.py",
            operation,
            GUEST_ROOT,
            GUEST_WORK,
            *parameters,
        ],
        timeout,
    )
    if not successful(result) or result.get("output_truncated"):
        raise SkillEvalError("Claude VM operation did not complete")
    try:
        value = _parse_json(result["stdout"].encode())
    except (KeyError, TypeError, UnicodeError):
        raise SkillEvalError("invalid Claude VM operation evidence") from None
    if not isinstance(value, dict):
        raise SkillEvalError("invalid Claude VM operation evidence")
    return value


def tooling_entries() -> list[tuple[str, bytes, bool]]:
    modules = (
        claude_vm_guest,
        guest_runner,
        app_server,
        context_guard,
        native_process,
        solver_boundary,
    )
    entries = []
    for module in modules:
        filename = module.__file__
        if filename is None:
            raise SkillEvalError("Claude VM tooling source unavailable")
        entries.append(("native/" + Path(filename).name, Path(filename).read_bytes(), False))
    return [*entries, ("native/models.json", b"{}\n", False), ("native/config.toml", b"", False)]


def initialize(session: Lifecycle) -> dict[str, Any]:
    result = _guest(session, "initialize", [], 30)
    if result.get("status") != "ready":
        raise SkillEvalError("Claude VM initialization failed")
    return result


def execute(session: Lifecycle, command: str, timeout_seconds: int = 30) -> dict[str, Any]:
    _validate_command(command, timeout_seconds)
    return _guest(
        session,
        "execute",
        [json.dumps({"command": command, "timeout_seconds": timeout_seconds})],
        timeout_seconds + 10,
    )


def collect(
    session: Lifecycle,
    *,
    max_artifact_bytes: int = 64 * 1024 * 1024,
    max_artifact_files: int = 10_000,
) -> dict[str, Any]:
    if (
        type(max_artifact_bytes) is not int
        or not 10240 <= max_artifact_bytes <= 256 * 1024 * 1024
        or type(max_artifact_files) is not int
        or not 1 <= max_artifact_files <= 10_000
    ):
        raise SkillEvalError("invalid Claude VM collection limits")
    return _guest(session, "collect", [str(max_artifact_bytes), str(max_artifact_files)], 60)


def _private_json(path: Path, maximum: int) -> dict[str, Any]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_mode & 0o077 or before.st_size > maximum:
            raise SkillEvalError("invalid private Claude VM configuration")
        raw = source.read(maximum + 1)
        after = os.fstat(source.fileno())
        if len(raw) > maximum or (
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise SkillEvalError("private Claude VM configuration changed")
    value = _parse_json(raw)
    if not isinstance(value, dict):
        raise SkillEvalError("invalid private Claude VM configuration")
    return value


def _append_log(path: Path, value: dict[str, Any]) -> None:
    raw = (json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n").encode()
    descriptor = os.open(
        path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
    )
    with os.fdopen(descriptor, "ab") as target:
        info = os.fstat(target.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077
            or info.st_size + len(raw) > TOOLS_LOG_BYTES
        ):
            raise SkillEvalError("Claude VM tool evidence limit or unsafe log")
        target.write(raw)
        target.flush()
        os.fsync(target.fileno())


def _gate(path: Path, deadline: float) -> None:
    until = min(deadline, time.monotonic() + 2)
    while True:
        try:
            if _private_json(path, 1024) != {"status": "passed"}:
                raise SkillEvalError("Claude native tool inventory was not accepted")
            return
        except FileNotFoundError:
            if time.monotonic() >= until:
                raise SkillEvalError("Claude native tool inventory was not accepted") from None
            time.sleep(min(0.02, max(0, until - time.monotonic())))


def _tool_call(
    session: Lifecycle, config: dict[str, Any], parameters: dict[str, Any]
) -> dict[str, Any]:
    started = time.monotonic()
    arguments = parameters.get("arguments", {})
    entry: dict[str, Any] = {"started_monotonic": started, "parameters": parameters}
    result: dict[str, Any]
    try:
        if (
            parameters.get("name") != "execute"
            or not isinstance(arguments, dict)
            or not set(arguments) <= {"command", "timeout_seconds"}
        ):
            raise SkillEvalError("unknown VM tool or arguments")
        command, requested = arguments.get("command"), arguments.get("timeout_seconds", 30)
        _validate_command(command, requested)
        assert isinstance(command, str)
        _gate(Path(config["gate_path"]), config["deadline_monotonic"])
        # Lifecycle guest invocation adds ten seconds for supervision and five
        # for its host client. Never dispatch into the final cleanup headroom.
        remaining = math.floor(config["deadline_monotonic"] - time.monotonic()) - 15
        if remaining < 1:
            raise SkillEvalError("Claude VM tool deadline exhausted")
        selected_timeout = min(requested, remaining)
        entry["selected_timeout_seconds"] = selected_timeout
        # Commit intent before any guest effect. A recorder failure revokes work.
        entry["event"] = "command-started"
        _append_log(Path(config["tools_log"]), entry)
        result = execute(session, command, selected_timeout)
    except (OSError, ValueError, SkillEvalError) as error:
        result = {
            "status": "failed",
            "error_type": type(error).__name__,
            "error": str(error)[:1024],
        }
    entry.update(event="command-result", result=result, elapsed_seconds=time.monotonic() - started)
    _append_log(Path(config["tools_log"]), entry)
    return {
        "content": [{"type": "text", "text": json.dumps(result, sort_keys=True)}],
        "isError": result.get("status") != "completed",
    }


def serve(
    config_path: Path, *, source: BinaryIO | None = None, target: BinaryIO | None = None
) -> int:
    """Serve bounded, serial JSONL MCP calls for exactly one trusted lifecycle."""
    config = _private_json(config_path, 64 * 1024)
    if (
        set(config) != {"output", "deadline_monotonic", "gate_path", "tools_log"}
        or any(
            not isinstance(config[name], str) or not Path(config[name]).is_absolute()
            for name in ("output", "gate_path", "tools_log")
        )
        or type(config["deadline_monotonic"]) not in {float, int}
        or not math.isfinite(config["deadline_monotonic"])
    ):
        raise SkillEvalError("invalid Claude VM server configuration")
    session = Lifecycle.load(Path(config["output"]))
    vm_config = session.ticket.get("config", {})
    if (
        session.ticket.get("phase") != "running"
        or vm_config.get("guest_network") is not False
        or vm_config.get("host_mounts") != []
        or vm_config.get("secrets") != []
    ):
        raise SkillEvalError("Claude VM capability requires a running isolated lifecycle")
    source = sys.stdin.buffer if source is None else source
    target = sys.stdout.buffer if target is None else target
    initialized = False
    pending = b""
    result: dict[str, Any]
    while time.monotonic() < config["deadline_monotonic"]:
        try:
            descriptor = source.fileno()
        except (OSError, ValueError):
            descriptor = None
        if descriptor is None:
            raw = source.readline(MCP_INPUT_BYTES + 1)
        else:
            # Read the descriptor directly so BufferedReader prefetch cannot
            # hide a queued MCP message from select's kernel readiness check.
            while b"\n" not in pending:
                if not select.select(
                    [descriptor],
                    [],
                    [],
                    max(0, config["deadline_monotonic"] - time.monotonic()),
                )[0]:
                    return 0
                block = os.read(descriptor, min(65536, MCP_INPUT_BYTES + 1 - len(pending)))
                if not block:
                    break
                pending += block
                if len(pending) > MCP_INPUT_BYTES:
                    raise SkillEvalError("Claude VM MCP input limit exceeded")
            if b"\n" in pending:
                raw, pending = pending.split(b"\n", 1)
            else:
                raw, pending = pending, b""
        if not raw:
            return 0
        if len(raw) > MCP_INPUT_BYTES:
            raise SkillEvalError("Claude VM MCP input limit exceeded")
        identity: Any = None
        try:
            message = _parse_json(raw)
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                raise SkillEvalError("invalid MCP request")
            identity = message.get("id")
            if identity is None and str(message.get("method", "")).startswith("notifications/"):
                continue
            if type(identity) not in {int, str}:
                raise SkillEvalError("invalid MCP request identity")
            method, parameters = message.get("method"), message.get("params", {})
            if not isinstance(parameters, dict):
                raise SkillEvalError("invalid MCP request parameters")
            if method == "initialize" and not initialized:
                initialized = True
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "skilleval-vm", "version": "1"},
                }
            elif method == "ping":
                result = {}
            elif initialized and method == "tools/list":
                result = {"tools": [TOOL]}
            elif initialized and method == "tools/call":
                result = _tool_call(session, config, parameters)
            else:
                raise SkillEvalError("unsupported MCP request")
            response = {"jsonrpc": "2.0", "id": identity, "result": result}
        except (OSError, ValueError, SkillEvalError):
            response = {
                "jsonrpc": "2.0",
                "id": identity,
                "error": {"code": -32602, "message": "VM capability request refused"},
            }
        target.write((json.dumps(response, ensure_ascii=False) + "\n").encode())
        target.flush()
    return 0


def main(arguments: list[str] | None = None) -> int:
    args = sys.argv[1:] if arguments is None else arguments
    if len(args) != 1:
        return 2
    try:
        with interrupt_scope():
            return serve(Path(args[0]))
    except (OSError, ValueError, SkillEvalError, LifecycleInterruptedError):
        print("Claude VM capability failed; retained lifecycle requires cleanup", file=sys.stderr)
        return 24


if __name__ == "__main__":
    raise SystemExit(main())
