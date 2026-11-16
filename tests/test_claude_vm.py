"""VM command transport contracts; subprocess fixtures do not prove isolation."""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import tarfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest
from test_lifecycle import runtime_at, wait_for
from test_qualification import docker_archive

from skilleval import claude_vm, claude_vm_guest, solver_boundary
from skilleval.errors import SkillEvalError
from skilleval.lifecycle import Lifecycle
from skilleval.qualification import LOG_BYTES, _invoke


class Session:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], int]] = []
        self.ticket: dict[str, Any] = {
            "phase": "running",
            "config": {"guest_network": False, "host_mounts": [], "secrets": []},
        }

    def guest(self, label: str, argv: list[str], timeout: int = 30) -> dict[str, Any]:
        self.calls.append((label, argv, timeout))
        return {
            "returncode": 0,
            "timed_out": False,
            "pipe_drain_incomplete": False,
            "stdout": json.dumps({"status": "completed", "returncode": 0}),
        }


def test_command_text_is_one_guest_argument_and_never_a_host_shell() -> None:
    session = Session()
    command = "printf '%s' '$(touch /host-canary);`pwd`'"
    result = claude_vm.execute(session, command, 7)  # type: ignore[arg-type]
    assert result["returncode"] == 0
    _label, argv, timeout = session.calls[0]
    assert argv[:2] == ["python3", "/var/tmp/native/claude_vm_guest.py"]
    assert argv[2:5] == ["execute", "/var/tmp/native", "/workspace/task"]
    assert json.loads(argv[5]) == {"command": command, "timeout_seconds": 7}
    assert timeout == 17


def test_guest_initialization_has_no_credentials_and_keeps_existing_work(tmp_path: Path) -> None:
    root, work = tmp_path / "native", tmp_path / "task"
    root.mkdir(mode=0o700)
    work.mkdir()
    (root / "models.json").write_text("{}\n")
    (root / "config.toml").write_bytes(b"")
    (work / "existing").write_text("keep")
    result = claude_vm_guest.initialize(root, work)
    assert result["status"] == "ready"
    assert (work / "existing").read_text() == "keep"
    assert list((root / "home").iterdir()) == []
    assert (root / "codex-home/config.toml").read_bytes() == b""
    assert json.loads((root / "claude-vm-state.json").read_text())["safe_to_collect"] is True


def native_at(tmp_path: Path) -> tuple[Path, Path]:
    root, work = tmp_path / "native", tmp_path / "task"
    root.mkdir(mode=0o700)
    work.mkdir()
    (root / "models.json").write_text("{}\n")
    (root / "config.toml").write_bytes(b"")
    claude_vm_guest.initialize(root, work)
    return root, work


def boundary(*, reaped: bool = True, returncode: int = 0) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "profile": "solver-isolated-v1",
        "status": "ready",
        "uid": 61000,
        "gid": 61000,
        "groups": [],
        "no_new_privs": True,
        "capabilities_cleared": True,
        "transport_filter": "vsock-deny-v1",
        "seccomp": True,
        "namespace_reaped": reaped,
        "native_returncode": returncode,
        "native_elapsed_seconds": 0.1,
        "launcher_elapsed_seconds": 0.2,
    }


def synthetic_process(
    monkeypatch: pytest.MonkeyPatch, root: Path, *, reaped: bool = True
) -> list[list[str]]:
    calls: list[list[str]] = []

    def launch(
        argv: list[str],
        environment: dict[str, str],
        prompt: Path,
        work: Path,
        evidence: Path,
        timeout: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        calls.append(argv)
        assert argv[:2] == ["python3", str(root / "solver_boundary.py")]
        assert argv[2:7] == [str(root), str(work), "--", "/bin/sh", "-lc"]
        assert environment["HOME"] == str(root / "home")
        assert set(environment) == {"HOME", "CODEX_HOME", "PATH", "LANG"}
        assert prompt.read_bytes() == b""
        assert 1 <= timeout <= 30
        assert kwargs["stream_limit_bytes"] == 65536
        (root / "solver-root").mkdir()
        (root / "boundary.json").write_text(json.dumps(boundary(reaped=reaped)))
        (evidence / "stdout.txt").write_bytes(b"x" * 65536)
        (evidence / "stderr.log").write_bytes(b"failure details")
        return {
            "returncode": 0,
            "timed_out": False,
            "pipe_drain_incomplete": False,
            "output_limit_reached": False,
        }

    monkeypatch.setattr(claude_vm_guest.native_process, "_execute_native", launch)
    return calls


def test_guest_commands_preserve_workspace_and_each_boundary_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, work = native_at(tmp_path)
    (work / "artifact.txt").write_text("persistent")
    (root / "home/setting").write_text("persistent")
    calls = synthetic_process(monkeypatch, root)
    assert solver_boundary.valid_receipt(boundary())
    for sequence in (1, 2):
        result = claude_vm_guest.execute(
            root, work, {"command": "cat artifact.txt", "timeout_seconds": 3}
        )
        assert result["status"] == "completed"
        assert result["command_id"] == sequence
        assert result["safe_to_collect"] is True
        assert len(result["stdout"].encode()) + len(result["stderr"].encode()) <= 65536
        assert result["output_truncated"] is True
        assert result["stderr_truncated"] is True
        assert (
            json.loads((root / f"evidence/commands/{sequence:06d}/boundary.json").read_text())[
                "namespace_reaped"
            ]
            is True
        )
    assert len(calls) == 2
    assert (work / "artifact.txt").read_text() == "persistent"
    assert (root / "home/setting").read_text() == "persistent"


def test_unreaped_boundary_blocks_future_commands_and_collection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, work = native_at(tmp_path)
    calls = synthetic_process(monkeypatch, root, reaped=False)
    result = claude_vm_guest.execute(root, work, {"command": "true", "timeout_seconds": 3})
    assert result["status"] == "failed"
    assert result["safe_to_collect"] is False
    assert (root / "evidence/commands/000001/process.json").is_file()
    with pytest.raises(ValueError, match="termination unconfirmed"):
        claude_vm_guest.execute(root, work, {"command": "true", "timeout_seconds": 3})
    with pytest.raises(ValueError, match="termination unconfirmed"):
        claude_vm_guest.collect(root, work, 1024 * 1024, 100)
    assert len(calls) == 1
    assert not (root / "artifacts.tar").exists()


def test_invalid_utf8_cannot_expand_returned_output_past_byte_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, work = native_at(tmp_path)
    synthetic_process(monkeypatch, root)
    launch = claude_vm_guest.native_process._execute_native

    def invalid_output(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = launch(*args, **kwargs)
        (args[4] / "stdout.txt").write_bytes(b"\xff" * 65536)
        return result

    monkeypatch.setattr(claude_vm_guest.native_process, "_execute_native", invalid_output)
    result = claude_vm_guest.execute(root, work, {"command": "true", "timeout_seconds": 3})
    assert len(result["stdout"].encode()) + len(result["stderr"].encode()) <= 65536
    assert result["stdout_truncated"] is True
    retained = json.loads((root / "evidence/commands/000001/process.json").read_text())
    assert retained["output_truncated"] is True


@pytest.mark.parametrize(
    "payload",
    [
        "short positive output",
        "x" * 65536,
        ('"\\\n\0\t' * 16384),
        "比較🙂" * 16384,
    ],
)
def test_complete_guest_result_survives_real_lifecycle_pipe_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: str,
) -> None:
    root, work = native_at(tmp_path)
    synthetic_process(monkeypatch, root)
    launch = claude_vm_guest.native_process._execute_native
    original = payload.encode()[:65536]

    def output(*args: Any, **kwargs: Any) -> dict[str, Any]:
        process = launch(*args, **kwargs)
        (args[4] / "stdout.txt").write_bytes(original)
        (args[4] / "stderr.log").write_bytes(b"diagnostic")
        return process

    monkeypatch.setattr(claude_vm_guest.native_process, "_execute_native", output)
    guest_result = claude_vm_guest.execute(
        root, work, {"command": "authored control", "timeout_seconds": 3}
    )
    assert claude_vm_guest.WIRE_BYTES == LOG_BYTES
    wire = claude_vm_guest._wire_result(guest_result)
    reply = tmp_path / "reply.json"
    reply.write_bytes(wire)
    observed = _invoke(
        [
            sys.executable,
            "-c",
            "import pathlib,sys;sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())",
            str(reply),
        ],
        tmp_path,
        {"PATH": "/usr/bin:/bin"},
        5,
    )
    assert observed["output_truncated"] is False
    assert len(wire) <= LOG_BYTES
    received = json.loads(observed["stdout"])
    session = Session()
    monkeypatch.setattr(session, "guest", lambda *_args: observed)
    assert claude_vm.execute(session, "authored control", 3) == received  # type: ignore[arg-type]
    assert received["status"] == "completed"
    assert original.decode("utf-8", errors="replace").startswith(received["stdout"])
    if len(original) == 65536:
        assert received["stdout_truncated"] is True
        assert received["output_truncated"] is True
    else:
        assert received["stdout"] == payload
        assert received["stderr"] == "diagnostic"
        assert received["output_truncated"] is False
    assert (root / "evidence/commands/000001/stdout.txt").read_bytes() == original


def test_large_error_and_boundary_diagnostics_are_bounded_on_wire_but_retained(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, work = native_at(tmp_path)
    synthetic_process(monkeypatch, root)
    launch = claude_vm_guest.native_process._execute_native
    diagnostic = '"\\\n\0' * 65536

    def verbose(*args: Any, **kwargs: Any) -> dict[str, Any]:
        process = launch(*args, **kwargs)
        process.update(error=diagnostic, debug={"details": diagnostic})
        value = json.loads((root / "boundary.json").read_text())
        value["debug"] = diagnostic
        (root / "boundary.json").write_text(json.dumps(value))
        return process

    monkeypatch.setattr(claude_vm_guest.native_process, "_execute_native", verbose)
    result = claude_vm_guest.execute(root, work, {"command": "true", "timeout_seconds": 3})
    wire = claude_vm_guest._wire_result(result)
    reply = tmp_path / "reply.json"
    reply.write_bytes(wire)
    observed = _invoke(
        [
            sys.executable,
            "-c",
            "import pathlib,sys;sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())",
            str(reply),
        ],
        tmp_path,
        {"PATH": "/usr/bin:/bin"},
        5,
    )
    assert observed["output_truncated"] is False
    value = json.loads(observed["stdout"])
    assert value["metadata_truncated"] is True
    assert value["error"] == diagnostic[:1024]
    assert "debug" not in value
    assert "debug" not in value["boundary"]
    assert solver_boundary.valid_receipt(value["boundary"])
    retained = json.loads((root / "evidence/commands/000001/process.json").read_text())
    assert retained["error"] == diagnostic
    assert retained["debug"] == {"details": diagnostic}
    assert retained["boundary"]["debug"] == diagnostic


@pytest.mark.parametrize(
    "stdout,stderr",
    [
        ("x" * 65536, "late stderr diagnostic"),
        ("x" * 65000, "y" * 500),
    ],
)
def test_wire_budget_reserves_flags_for_both_nonempty_streams(
    tmp_path: Path,
    stdout: str,
    stderr: str,
) -> None:
    result = {"status": "completed", "returncode": 0, "stdout": stdout, "stderr": stderr}
    reply = tmp_path / "reply.json"
    reply.write_bytes(claude_vm_guest._wire_result(result))
    observed = _invoke(
        [
            sys.executable,
            "-c",
            "import pathlib,sys;sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())",
            str(reply),
        ],
        tmp_path,
        {"PATH": "/usr/bin:/bin"},
        5,
    )
    assert observed["output_truncated"] is False
    value = json.loads(observed["stdout"])
    assert value["status"] == "completed"
    assert stdout.startswith(value["stdout"])
    assert stderr.startswith(value["stderr"])
    assert value["output_truncated"] is True
    assert value["stderr_truncated"] is True


def test_collection_keeps_regular_files_and_link_metadata_without_following_links(
    tmp_path: Path,
) -> None:
    root, work = native_at(tmp_path)
    outside = tmp_path / "hidden"
    outside.write_text("host-like canary")
    (work / "ordinary").write_text("selected evidence")
    (work / "escape").symlink_to(outside)
    result = claude_vm_guest.collect(root, work, 1024 * 1024, 100)
    assert result["status"] == "complete"
    with tarfile.open(root / "artifacts.tar") as archive:
        file = archive.extractfile("task/ordinary")
        assert file is not None and file.read() == b"selected evidence"
        assert "task/escape" not in archive.getnames()
        metadata = archive.extractfile("evidence/filesystem-links.json")
        assert metadata is not None
        assert json.loads(metadata.read()) == [
            {"path": "task/escape", "kind": "symlink", "target": str(outside)}
        ]


def test_transferred_guest_modules_import_without_installed_package(tmp_path: Path) -> None:
    root, work = tmp_path / "native", tmp_path / "task"
    root.mkdir(mode=0o700)
    work.mkdir()
    for name, raw, _executable in claude_vm.tooling_entries():
        (root / Path(name).name).write_bytes(raw)
    result = subprocess.run(
        [sys.executable, str(root / "claude_vm_guest.py"), "initialize", str(root), str(work)],
        cwd=work,
        capture_output=True,
        timeout=10,
        check=False,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "ready"


def configuration(tmp_path: Path, *, gated: bool = True) -> tuple[Path, dict[str, Any]]:
    gate = tmp_path / "gate.json"
    if gated:
        gate.write_text('{"status":"passed"}')
        gate.chmod(0o600)
    config = {
        "output": str(tmp_path / "lifecycle"),
        "deadline_monotonic": time.monotonic() + 60,
        "gate_path": str(gate),
        "tools_log": str(tmp_path / "tools.jsonl"),
    }
    file = tmp_path / "mcp.json"
    file.write_text(json.dumps(config))
    file.chmod(0o600)
    return file, config


def messages(*calls: dict[str, Any]) -> io.BytesIO:
    records = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        *calls,
    ]
    return io.BytesIO(b"".join((json.dumps(row) + "\n").encode() for row in records))


def call(arguments: dict[str, Any], *, identity: int = 3) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": identity,
        "method": "tools/call",
        "params": {"name": "execute", "arguments": arguments},
    }


def test_mcp_lists_only_vm_command_and_records_serial_guest_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, config = configuration(tmp_path)
    session = Session()
    monkeypatch.setattr(claude_vm.Lifecycle, "load", lambda _path: session)
    target = io.BytesIO()
    assert (
        claude_vm.serve(
            path,
            source=messages(
                call({"command": "pwd"}),
                call({"command": "cat ordinary", "timeout_seconds": 2}, identity=4),
            ),
            target=target,
        )
        == 0
    )
    results = [json.loads(line) for line in target.getvalue().splitlines()]
    assert results[1]["result"]["tools"] == [claude_vm.TOOL]
    assert all(row["result"]["isError"] is False for row in results[2:])
    assert len(session.calls) == 2
    log = [json.loads(line) for line in Path(config["tools_log"]).read_text().splitlines()]
    assert [row["event"] for row in log] == [
        "command-started",
        "command-result",
        "command-started",
        "command-result",
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        {"command": "true", "timeout_seconds": True},
        {"command": "true", "timeout_seconds": 31},
        {"command": "x" * (16384 + 1)},
        {"command": "true", "host_path": "/private/secret"},
        {"command": "\0"},
    ],
)
def test_invalid_mcp_arguments_refused_without_guest_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: dict[str, Any],
) -> None:
    path, config = configuration(tmp_path)
    session = Session()
    monkeypatch.setattr(claude_vm.Lifecycle, "load", lambda _path: session)
    target = io.BytesIO()
    claude_vm.serve(path, source=messages(call(arguments)), target=target)
    result = json.loads(target.getvalue().splitlines()[-1])
    assert result["result"]["isError"] is True
    assert session.calls == []
    assert json.loads(Path(config["tools_log"]).read_text())["event"] == "command-result"


def test_gate_and_remaining_deadline_refuse_without_effects_and_retain_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, config = configuration(tmp_path, gated=False)
    config["deadline_monotonic"] = time.monotonic() + 0.01
    path.write_text(json.dumps(config))
    session = Session()
    monkeypatch.setattr(claude_vm.Lifecycle, "load", lambda _path: session)
    target = io.BytesIO()
    claude_vm.serve(path, source=messages(call({"command": "true"})), target=target)
    assert session.calls == []
    assert "inventory was not accepted" in Path(config["tools_log"]).read_text()
    Path(config["gate_path"]).write_text('{"status":"passed"}')
    Path(config["gate_path"]).chmod(0o600)
    config["deadline_monotonic"] = time.monotonic() + 5
    path.write_text(json.dumps(config))
    claude_vm.serve(path, source=messages(call({"command": "true"})), target=io.BytesIO())
    assert session.calls == []
    assert "deadline exhausted" in Path(config["tools_log"]).read_text()


def test_log_failure_prevents_guest_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _config = configuration(tmp_path)
    session = Session()
    monkeypatch.setattr(claude_vm.Lifecycle, "load", lambda _path: session)
    monkeypatch.setattr(claude_vm, "TOOLS_LOG_BYTES", 1)
    output = io.BytesIO()
    claude_vm.serve(path, source=messages(call({"command": "true"})), target=output)
    assert session.calls == []
    assert "error" in json.loads(output.getvalue().splitlines()[-1])


def test_symlink_gate_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.write_text('{"status":"passed"}')
    real.chmod(0o600)
    gate = tmp_path / "gate"
    gate.symlink_to(real)
    with pytest.raises(OSError):
        claude_vm._gate(gate, time.monotonic() + 30)


def test_oversized_input_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, _config = configuration(tmp_path)
    session = Session()
    monkeypatch.setattr(claude_vm.Lifecycle, "load", lambda _path: session)
    with pytest.raises(SkillEvalError, match="input limit"):
        claude_vm.serve(path, source=io.BytesIO(b"x" * (128 * 1024 + 1)), target=io.BytesIO())
    assert session.calls == []


def test_pipe_prefetch_does_not_hide_queued_mcp_messages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, config = configuration(tmp_path)
    config["deadline_monotonic"] = time.monotonic() + 0.05
    path.write_text(json.dumps(config))
    monkeypatch.setattr(claude_vm.Lifecycle, "load", lambda _path: Session())
    reader, writer = os.pipe()
    target = io.BytesIO()
    try:
        os.write(writer, messages().getvalue())
        with os.fdopen(reader, "rb") as source:
            claude_vm.serve(path, source=source, target=target)
        assert [json.loads(line)["id"] for line in target.getvalue().splitlines()] == [1, 2]
    finally:
        os.close(writer)


@pytest.mark.parametrize("change", ["phase", "network", "mounts", "secrets"])
def test_server_refuses_nonisolated_or_inactive_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    path, _config = configuration(tmp_path)
    session = Session()
    if change == "phase":
        session.ticket["phase"] = "prepared"
    elif change == "network":
        session.ticket["config"]["guest_network"] = True
    else:
        session.ticket["config"]["host_mounts" if change == "mounts" else "secrets"] = ["bad"]
    monkeypatch.setattr(claude_vm.Lifecycle, "load", lambda _path: session)
    with pytest.raises(SkillEvalError, match="running isolated"):
        claude_vm.serve(path, source=messages(), target=io.BytesIO())
    assert session.calls == []


def test_standalone_mcp_process_preserves_reloaded_lifecycle_steps(tmp_path: Path) -> None:
    runtime = runtime_at(tmp_path / "runtime")
    source = runtime.read_text()
    source = source.replace(
        "elif args[:2]==['machine','stop']:",
        "elif args[:2]==['machine','exec']:\n"
        " command=args[args.index('--')+1:]\n"
        " assert command[:3]==['python3','/var/tmp/native/claude_vm_guest.py','execute']\n"
        " request=json.loads(command[-1]);assert request['command']=='pwd'\n"
        " print(json.dumps({'status':'completed','returncode':0,'stdout':'/workspace/task',"
        "'stderr':'','output_truncated':False}));raise SystemExit\n"
        "elif args[:2]==['machine','stop']:",
    )
    runtime.write_text(source)
    image, digest = docker_archive(tmp_path / "image")
    session = Lifecycle.prepare(tmp_path / "lifecycle", runtime, image, digest)
    session.create()
    path, config = configuration(tmp_path)
    config["output"] = str(session.output)
    path.write_text(json.dumps(config))
    try:
        child = subprocess.run(
            [sys.executable, "-I", "-m", "skilleval.claude_vm", str(path)],
            input=messages(call({"command": "pwd", "timeout_seconds": 2})).getvalue(),
            capture_output=True,
            timeout=15,
            check=False,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert child.returncode == 0, child.stderr
        results = [json.loads(line) for line in child.stdout.splitlines()]
        assert results[-1]["result"]["isError"] is False
        assert all(step["label"] != "claude_vm_execute" for step in session.ticket["steps"])
        session = Lifecycle.load(session.output)
        assert session.ticket["steps"][-1]["label"] == "claude_vm_execute"
        assert json.loads(session.ticket["steps"][-1]["stdout"])["stdout"] == "/workspace/task"
        session.cleanup()
        saved = Lifecycle.load(session.output)
        assert any(step["label"] == "claude_vm_execute" for step in saved.ticket["steps"])
        assert saved.ticket["cleanup_complete"] is True
    finally:
        session = Lifecycle.load(session.output)
        session.cleanup()


def test_sigterm_unwinds_and_reaps_separately_grouped_host_client(tmp_path: Path) -> None:
    runtime = runtime_at(tmp_path / "runtime")
    runtime.write_text(
        runtime.read_text().replace(
            "elif args[:2]==['machine','stop']:",
            "elif args[:2]==['machine','exec']:\n"
            " (state/'pending-client.json').write_text(\n"
            "  json.dumps({'pid':os.getpid(),'pgid':os.getpgrp()}))\n"
            " time.sleep(60)\n"
            " raise SystemExit\n"
            "elif args[:2]==['machine','stop']:",
        )
    )
    image, digest = docker_archive(tmp_path / "image")
    session = Lifecycle.prepare(tmp_path / "lifecycle", runtime, image, digest)
    session.create()
    path, config = configuration(tmp_path)
    config["output"] = str(session.output)
    path.write_text(json.dumps(config))
    process = subprocess.Popen(
        [sys.executable, "-I", "-m", "skilleval.claude_vm", str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    helper_pid: int | None = None
    try:
        assert process.stdin is not None
        process.stdin.write(messages(call({"command": "authored pending command"})).getvalue())
        process.stdin.flush()
        marker = Path(session.ticket["runtime_state"]) / "pending-client.json"
        wait_for(marker)
        helper = json.loads(marker.read_text())
        helper_pid = helper["pid"]
        assert helper["pgid"] == helper_pid
        assert helper["pgid"] != os.getpgid(process.pid)
        os.kill(helper_pid, 0)  # Known-positive independent helper liveness.
        os.killpg(process.pid, signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)
        with pytest.raises(ProcessLookupError):
            os.kill(helper_pid, 0)
        assert process.returncode == 24, (stdout, stderr)
        assert b"retained lifecycle requires cleanup" in stderr
        assert '"event": "command-started"' in Path(config["tools_log"]).read_text()
        # Interrupting the host client does not establish remote guest teardown.
        # The owning controller reloads and performs whole-VM lifecycle cleanup.
        session = Lifecycle.load(session.output)
        session.cleanup()
        assert session.ticket["cleanup_complete"] is True
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)
        if helper_pid is not None:
            with suppress(ProcessLookupError):
                os.killpg(helper_pid, signal.SIGKILL)
        session = Lifecycle.load(session.output)
        session.cleanup()
