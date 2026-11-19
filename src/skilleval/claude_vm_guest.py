"""Trusted command supervisor copied into the credential-free VM."""

from __future__ import annotations

import json
import shutil
import sys
import tarfile
from pathlib import Path
from typing import Any

if __package__:
    from skilleval import guest_runner, native_process, solver_boundary
else:
    import guest_runner  # type: ignore[no-redef,import-not-found]
    import native_process  # type: ignore[no-redef,import-not-found]
    import solver_boundary  # type: ignore[no-redef,import-not-found]

COMMAND_BYTES = 16 * 1024
OUTPUT_BYTES = 64 * 1024
WIRE_BYTES = 64 * 1024
METADATA_BYTES = 16 * 1024
STATE = "claude-vm-state.json"


def _encoded(result: dict[str, Any]) -> bytes:
    return (json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode()


def _bounded_result(result: dict[str, Any]) -> dict[str, Any]:
    """Fit the complete JSON envelope, not just its decoded output strings.

    Lifecycle retains 64 KiB per pipe. Raw stdout/stderr and full supervisor
    metadata remain in the guest evidence; this projection must survive that
    existing transport without losing its JSON framing.
    """
    metadata = {name: value for name, value in result.items() if name not in {"stdout", "stderr"}}
    metadata_truncated = metadata.get("metadata_truncated") is True
    if len(_encoded(metadata)) > METADATA_BYTES:
        priority = (
            "command_id",
            "status",
            "returncode",
            "safe_to_collect",
            "timed_out",
            "pipe_drain_incomplete",
            "output_limit_reached",
            "agent_elapsed_seconds",
            "stdout_truncated",
            "stderr_truncated",
            "output_truncated",
            "output_bytes_limit",
            "boundary",
            "error_type",
            "error",
        )
        projected: dict[str, Any] = {}
        for name in (*priority, *(key for key in metadata if key not in priority)):
            if name not in metadata:
                continue
            value = metadata[name]
            if isinstance(value, str):
                value = value[:1024]
            if name == "boundary" and isinstance(value, dict):
                # The original complete receipt is retained separately. Keep
                # its reviewed setup/teardown contract ahead of diagnostics.
                fields = (
                    "schema_version",
                    "profile",
                    "status",
                    "uid",
                    "gid",
                    "groups",
                    "no_new_privs",
                    "capabilities_cleared",
                    "transport_filter",
                    "seccomp",
                    "namespace_reaped",
                    "native_returncode",
                    "native_elapsed_seconds",
                    "launcher_elapsed_seconds",
                    "error",
                )
                value = {
                    key: item[:1024] if isinstance(item, str) else item
                    for key, item in value.items()
                    if key in fields
                }
            candidate = {**projected, name: value}
            if len(_encoded(candidate)) <= METADATA_BYTES:
                projected = candidate
        metadata = projected
        metadata_truncated = True
    for name in ("command_id", "status", "returncode", "safe_to_collect"):
        if name in result and metadata.get(name) != result[name]:
            raise ValueError("command envelope lost required execution metadata")
    original_boundary = result.get("boundary")
    if solver_boundary.valid_receipt(original_boundary) and not solver_boundary.valid_receipt(
        metadata.get("boundary")
    ):
        raise ValueError("command envelope lost required boundary metadata")
    metadata["metadata_truncated"] = metadata_truncated
    metadata["wire_bytes_limit"] = WIRE_BYTES
    # Reserve both stream flags before either stream consumes the wire budget.
    # Updating false to true then shrinks the JSON rather than adding late keys.
    for name in ("stdout_truncated", "stderr_truncated", "output_truncated"):
        metadata.setdefault(name, False)
    bounded = {**metadata, "stdout": "", "stderr": ""}
    for name in ("stdout", "stderr"):
        original = result.get(name, "")
        if not isinstance(original, str):
            raise ValueError("invalid command output")
        bounded[name] = original
        if len(_encoded(bounded)) <= WIRE_BYTES:
            continue
        bounded[name + "_truncated"] = True
        bounded["output_truncated"] = True
        # Search by Unicode code point; the actual encoder accounts for UTF-8
        # and escaping of control bytes, quotes, backslashes, and the newline.
        low, high = 0, len(original) - 1
        while low < high:
            middle = (low + high + 1) // 2
            bounded[name] = original[:middle]
            if len(_encoded(bounded)) <= WIRE_BYTES:
                low = middle
            else:
                high = middle - 1
        bounded[name] = original[:low]
    if len(_encoded(bounded)) > WIRE_BYTES:
        raise ValueError("command envelope exceeds transport limit")
    return bounded


def _wire_result(result: dict[str, Any]) -> bytes:
    return _encoded(_bounded_result(result))


def _write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n")
    path.chmod(0o600)


def _state(root: Path) -> dict[str, Any]:
    path = root / STATE
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
        raise ValueError("invalid command state")
    value = json.loads(path.read_bytes())
    if (
        not isinstance(value, dict)
        or type(value.get("next_command")) is not int
        or value["next_command"] < 1
        or value.get("safe_to_collect") is not True
    ):
        raise ValueError("previous solver namespace termination unconfirmed")
    return value


def _request(request: dict[str, Any]) -> tuple[str, int]:
    if (
        not isinstance(request, dict)
        or set(request) != {"command", "timeout_seconds"}
        or not isinstance(request["command"], str)
        or not request["command"].strip()
        or "\0" in request["command"]
        or len(request["command"].encode()) > COMMAND_BYTES
        or type(request["timeout_seconds"]) is not int
        or not 1 <= request["timeout_seconds"] <= 30
    ):
        raise ValueError("invalid bounded VM command")
    return request["command"], request["timeout_seconds"]


def initialize(root: Path, work: Path) -> dict[str, Any]:
    for directory in (root, work):
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("command roots must be real directories")
    if (root / STATE).exists() or (root / STATE).is_symlink():
        raise ValueError("command environment must be fresh")
    for name in ("home", "codex-home", "evidence"):
        (root / name).mkdir(mode=0o700)
    (root / "evidence/commands").mkdir(mode=0o700)
    # These credential-free placeholders satisfy the existing mount contract.
    if (root / "models.json").read_bytes() != b"{}\n":
        raise ValueError("command model placeholder differs")
    if (root / "config.toml").read_bytes():
        raise ValueError("command config placeholder differs")
    (root / "codex-home/config.toml").write_bytes(b"")
    _write(root / STATE, {"next_command": 1, "safe_to_collect": True})
    return {"status": "ready", "profile": "solver-isolated-v1"}


def execute(root: Path, work: Path, request: dict[str, Any]) -> dict[str, Any]:
    command, timeout = _request(request)
    state = _state(root)
    sequence = state["next_command"]
    directory = root / "evidence/commands" / f"{sequence:06d}"
    directory.mkdir(mode=0o700)
    _write(directory / "request.json", request)
    prompt = directory / "stdin.txt"
    prompt.write_bytes(b"")
    # The old jail is a trusted, inaccessible skeleton. Private mounts vanish
    # with the previous boundary process; state only becomes safe after reaping.
    jail = root / "solver-root"
    if jail.exists() or jail.is_symlink():
        if jail.is_symlink():
            raise ValueError("unsafe old solver root")
        shutil.rmtree(jail)
    previous = root / "boundary.json"
    if previous.exists() or previous.is_symlink():
        if previous.is_symlink():
            raise ValueError("unsafe old boundary receipt")
        previous.unlink()
    state.update(next_command=sequence + 1, safe_to_collect=False)
    _write(root / STATE, state)
    environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(root / "home"),
        "CODEX_HOME": str(root / "codex-home"),
        "LANG": "C.UTF-8",
    }
    result: dict[str, Any] = {"command_id": sequence, "status": "failed"}
    try:
        process = native_process._execute_native(
            [
                "python3",
                str(root / "solver_boundary.py"),
                str(root),
                str(work),
                "--",
                "/bin/sh",
                "-lc",
                command,
            ],
            environment,
            prompt,
            work,
            directory,
            timeout,
            stream_limit_bytes=OUTPUT_BYTES,
            stdout_name="stdout.txt",
        )
        result.update(process)
        boundary = json.loads(previous.read_bytes())
        _write(directory / "boundary.json", boundary)
        result["boundary"] = boundary
        if not solver_boundary.valid_receipt(boundary):
            raise ValueError("solver boundary failed")
        state["safe_to_collect"] = True
        _write(root / STATE, state)
        result["returncode"] = boundary["native_returncode"]
        result["status"] = (
            "completed"
            if result["returncode"] == 0
            and not process["timed_out"]
            and not process["pipe_drain_incomplete"]
            else "failed"
        )
    except (OSError, ValueError, KeyError) as error:
        result["error_type"] = type(error).__name__
        result["error"] = str(error)[:1024]
    _write(directory / "process.json", result)
    remaining = OUTPUT_BYTES
    for name, filename in (("stdout", "stdout.txt"), ("stderr", "stderr.log")):
        file = directory / filename
        raw = file.read_bytes() if file.exists() else b""
        selected = raw[:remaining]
        encoded = selected.decode("utf-8", errors="replace").encode()
        text = encoded[:remaining].decode("utf-8", errors="ignore")
        result[name] = text
        result[name + "_truncated"] = len(raw) > len(selected) or len(encoded) > remaining
        remaining -= len(text.encode())
    result["output_truncated"] = bool(
        result.get("output_limit_reached")
        or result["stdout_truncated"]
        or result["stderr_truncated"]
    )
    result["output_bytes_limit"] = OUTPUT_BYTES
    result["safe_to_collect"] = state["safe_to_collect"]
    projected = _bounded_result(result)
    for name in (
        "stdout_truncated",
        "stderr_truncated",
        "output_truncated",
        "wire_bytes_limit",
        "metadata_truncated",
    ):
        result[name] = projected.get(name, False)
    _write(
        directory / "process.json",
        {name: value for name, value in result.items() if name not in {"stdout", "stderr"}},
    )
    return projected


def collect(root: Path, work: Path, maximum: int, count: int) -> dict[str, Any]:
    _state(root)
    if (
        type(maximum) is not int
        or not 10240 <= maximum <= 256 * 1024 * 1024
        or type(count) is not int
        or not 1 <= count <= 10_000
    ):
        raise ValueError("invalid command collection limits")
    try:
        result = guest_runner._archive(root, work, maximum, count)
    except (OSError, ValueError, tarfile.TarError) as error:
        result = {"status": "failed", "error_type": type(error).__name__}
    _write(root / "collection.json", result)
    return result


def main(arguments: list[str] | None = None) -> int:
    args = sys.argv[1:] if arguments is None else arguments
    try:
        operation, root, work, *parameters = args
        if operation == "initialize" and not parameters:
            result = initialize(Path(root), Path(work))
        elif operation == "execute" and len(parameters) == 1:
            if len(parameters[0].encode()) > COMMAND_BYTES * 7:
                raise ValueError("oversized command envelope")
            result = execute(Path(root), Path(work), json.loads(parameters[0]))
        elif operation == "collect" and len(parameters) == 2:
            result = collect(Path(root), Path(work), int(parameters[0]), int(parameters[1]))
        else:
            raise ValueError("invalid command operation")
    except (OSError, ValueError, TypeError) as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        return 24
    sys.stdout.buffer.write(_wire_result(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
