"""Real process/filesystem contracts; never evidence of real VM isolation."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_qualification import docker_archive

from skilleval.errors import SkillEvalError
from skilleval.lifecycle import Lifecycle, inventory_names, recover
from skilleval.qualification import _invoke


def runtime_at(root: Path) -> Path:
    """Synthetic complete distribution; commands are actual child processes."""
    root.mkdir()
    for directory in ("agent-rootfs", "lib"):
        (root / directory).mkdir()
    for name in ("smolvm-bin", "storage-template.ext4", "overlay-template.ext4"):
        (root / name).write_bytes(b"synthetic-runtime-component")
    runtime = root / "smolvm"
    runtime.write_text(
        f"#!{sys.executable}\n"
        "import json,os,pathlib,sys,time\n"
        "args=sys.argv[1:]\n"
        "state=pathlib.Path(os.environ['XDG_CONFIG_HOME']).parent\n"
        "machines=state/'machines.json'\n"
        "names=json.loads(machines.read_text()) if machines.exists() else []\n"
        "with (state/'calls.jsonl').open('a') as log:\n"
        " log.write(json.dumps({'argv':args,'env':dict(os.environ)})+'\\n')\n"
        "if args==['--version']: print('smolvm 1.32.3');raise SystemExit\n"
        "name=args[args.index('--name')+1] if '--name' in args else None\n"
        "if args[:2]==['machine','create']:\n"
        " ticket=json.loads((state.parent/'recovery.json').read_text())\n"
        " assert ticket['create_attempted'] and ticket['machine_name']==name\n"
        " names.append(name);machines.write_text(json.dumps(names))\n"
        "elif args[:2]==['machine','ls']:\n"
        " if (state/'bad-inventory').exists():print('invalid');raise SystemExit\n"
        " print(json.dumps([{'name':name} for name in names]));raise SystemExit\n"
        "elif args[:2]==['machine','stop']:\n"
        " if (state/'fail-stop').exists():raise SystemExit(7)\n"
        "elif args[:2]==['machine','delete']:\n"
        " assert '--force' in args\n"
        " if (state/'fail-delete').exists():raise SystemExit(8)\n"
        " names=[item for item in names if item!=name];machines.write_text(json.dumps(names))\n"
        "print('ok')\n"
    )
    runtime.chmod(0o700)
    return runtime


def session_at(root: Path) -> Lifecycle:
    runtime = runtime_at(root / "runtime")
    archive, digest = docker_archive(root / "image")
    return Lifecycle.prepare(root / "result", runtime, archive, digest)


def wait_for(path: Path, *, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size > 0:
            return
        time.sleep(0.02)
    pytest.fail(f"process did not establish known-positive {path.name}")


def test_image_copy_tamper_refused_before_create(tmp_path: Path) -> None:
    session = session_at(tmp_path)
    copied = Path(session.ticket["runtime_state"]) / "image.tar"
    copied.write_bytes(b"replaced after durable registration")
    try:
        with pytest.raises(SkillEvalError, match=r"image|archive"):
            session.create()
        assert session.ticket["create_attempted"] is False
    finally:
        session.cleanup()


def test_durable_ticket_env_positive_inventory_and_idempotent_recovery(tmp_path: Path) -> None:
    session = session_at(tmp_path)
    home = Path(session.ticket["runtime_home"])
    session.create()
    ticket_path = session.output / "recovery.json"
    ticket = json.loads(ticket_path.read_text())
    assert ticket_path.stat().st_mode & 0o077 == 0
    assert ticket["phase"] == "running" and ticket["observer_positive"]
    assert session.machine in session.observe("independent_positive")
    calls = [
        json.loads(line)
        for line in (Path(ticket["runtime_state"]) / "calls.jsonl").read_text().splitlines()
    ]
    assert all("OPENAI_API_KEY" not in call["env"] for call in calls)
    assert all(call["env"]["HOME"] == str(home) for call in calls)
    recovered = recover(session.output)
    assert recovered["cleanup_complete"] and recovered["runtime_home_removed"]
    assert not home.exists()
    assert recovered["admitted"] is False
    assert recovered["observer_positive"]
    assert json.loads(recovered["steps"][-1]["stdout"]) == []
    assert recover(session.output) == recovered


@pytest.mark.parametrize("fault", ["fail-stop", "fail-delete", "bad-inventory"])
def test_cleanup_failure_retains_namespace_until_explicit_recovery(
    tmp_path: Path, fault: str
) -> None:
    session = session_at(tmp_path)
    session.create()
    home = Path(session.ticket["runtime_home"])
    trigger = Path(session.ticket["runtime_state"]) / fault
    trigger.touch()
    first = session.cleanup()
    assert first["cleanup_complete"] is False
    assert home.is_dir()
    assert (session.output / "recovery.json").is_file()
    trigger.unlink()
    repaired = recover(session.output)
    assert repaired["cleanup_complete"] is True
    assert not home.exists()


def test_inventory_failure_cannot_masquerade_as_absence(tmp_path: Path) -> None:
    session = session_at(tmp_path)
    session.create()
    assert session.machine in session.observe("positive")
    (Path(session.ticket["runtime_state"]) / "bad-inventory").touch()
    with pytest.raises(SkillEvalError, match="inventory"):
        session.observe("broken_observer")
    assert session.cleanup()["cleanup_complete"] is False
    (Path(session.ticket["runtime_state"]) / "bad-inventory").unlink()
    assert recover(session.output)["cleanup_complete"]


def test_absence_without_positive_control_retains_recovery_state(tmp_path: Path) -> None:
    session = session_at(tmp_path)
    session.mark_create_attempted()  # interruption before create's observable effect
    result = session.cleanup()
    assert result["cleanup_complete"] is False
    assert "known-present" in result["error"]
    assert Path(result["runtime_home"]).is_dir()
    # Test fixture repairs missing control by performing the pending create.
    session.invoke("fixture_create", ["machine", "create", "--name", session.machine])
    assert recover(session.output)["cleanup_complete"]


def test_runtime_drift_refuses_recovery_and_retains_original_state(tmp_path: Path) -> None:
    session = session_at(tmp_path)
    session.create()
    component = tmp_path / "runtime" / "smolvm-bin"
    original = component.read_bytes()
    component.write_bytes(b"changed-runtime")
    with pytest.raises(SkillEvalError, match="identity changed"):
        recover(session.output)
    assert Path(session.ticket["runtime_home"]).exists()
    component.write_bytes(original)
    assert recover(session.output)["cleanup_complete"]


def test_native_controller_timeout_kills_real_process_group_descendant(tmp_path: Path) -> None:
    beat = tmp_path / "heartbeat"
    child = (
        "import pathlib,time\n"
        f"p=pathlib.Path({str(beat)!r})\n"
        "while True:\n p.write_text(str(time.monotonic_ns()));time.sleep(.05)\n"
    )
    parent = (
        "import subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable,'-c',{child!r}])\n"
        "time.sleep(30)\n"
    )
    # Allow both interpreters to start while native VM controls share the host.
    # The positive heartbeat is still required before asserting group shutdown.
    result = _invoke([sys.executable, "-c", parent], tmp_path, {"PATH": "/usr/bin:/bin"}, 5)
    assert result["timed_out"] and result["returncode"] < 0
    wait_for(beat)
    known_positive = beat.read_text()
    time.sleep(0.2)
    assert beat.read_text() == known_positive
    assert not result["pipe_drain_incomplete"]


@pytest.mark.parametrize("number", [signal.SIGTERM, signal.SIGKILL])
def test_interrupted_controller_durable_ticket_recovers_in_same_namespace(
    tmp_path: Path, number: signal.Signals
) -> None:
    session = session_at(tmp_path)
    ready = tmp_path / "ready"
    controller = (
        "import pathlib,time\n"
        "from skilleval.lifecycle import Lifecycle, LifecycleInterruptedError,interrupt_scope\n"
        f"session=Lifecycle.load(pathlib.Path({str(session.output)!r}))\n"
        "try:\n"
        " with interrupt_scope():\n"
        "  session.create()\n"
        f"  pathlib.Path({str(ready)!r}).write_text('known-present')\n"
        "  while True:time.sleep(.05)\n"
        "except LifecycleInterruptedError:\n"
        " session.ticket['phase']='interrupted';session.save()\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", controller],
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
    )
    try:
        wait_for(ready)
        assert session.machine in Lifecycle.load(session.output).observe("parent_known_positive")
        process.send_signal(number)
        process.wait(timeout=5)
        retained = json.loads((session.output / "recovery.json").read_text())
        assert retained["create_attempted"] and retained["observer_positive"]
        assert not retained["cleanup_complete"]
        assert Path(retained["runtime_home"]).is_dir()
        result = recover(session.output)
        assert result["cleanup_complete"] and result["runtime_home_removed"]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        recover(session.output)


def test_sigterm_unwinds_client_and_real_child_group(tmp_path: Path) -> None:
    beat = tmp_path / "child-heartbeat"
    done = tmp_path / "interrupted"
    child = (
        "import pathlib,time\n"
        f"p=pathlib.Path({str(beat)!r})\n"
        "while True:\n p.write_text(str(time.monotonic_ns()));time.sleep(.05)\n"
    )
    controller = (
        "from pathlib import Path\n"
        "from skilleval.lifecycle import LifecycleInterruptedError,interrupt_scope\n"
        "from skilleval.qualification import _invoke\n"
        "import sys\n"
        "try:\n"
        " with interrupt_scope():\n"
        f"  _invoke([sys.executable,'-c',{child!r}],Path({str(tmp_path)!r}),"
        "{'PATH':'/usr/bin:/bin'},30)\n"
        "except LifecycleInterruptedError:\n"
        f" Path({str(done)!r}).write_text('interrupted')\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", controller],
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
    )
    try:
        wait_for(beat)
        before = beat.read_text()
        time.sleep(0.1)
        assert beat.read_text() != before
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=5)
        assert done.read_text() == "interrupted"
        last = beat.read_text()
        time.sleep(0.2)
        assert beat.read_text() == last
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_inventory_rejects_valid_json_from_failed_or_truncated_detector() -> None:
    positive = {"returncode": 0, "timed_out": False, "stdout": '[{"name":"positive"}]'}
    assert inventory_names(positive) == {"positive"}
    for change in ({"returncode": 1}, {"output_truncated": True}, {"pipe_drain_incomplete": True}):
        with pytest.raises(SkillEvalError, match="inventory"):
            inventory_names({**positive, **change})


def test_cleanup_identity_drift_is_retained_report_instead_of_exception(tmp_path: Path) -> None:
    session = session_at(tmp_path)
    session.create()
    component = tmp_path / "runtime" / "smolvm-bin"
    original = component.read_bytes()
    component.write_bytes(b"changed-runtime")
    before = len(session.ticket["steps"])
    result = session.cleanup()
    assert result["identity_changed"] is True
    assert result["cleanup_complete"] is False
    assert len(result["steps"]) == before  # no changed-runtime command was launched
    assert Path(result["runtime_home"]).exists()
    component.write_bytes(original)
    assert recover(session.output)["cleanup_complete"]


@pytest.mark.parametrize("tamper", ["env", "state-symlink", "home-symlink", "duplicate", "fifo"])
def test_corrupt_ticket_refuses_recovery_before_runtime_command(
    tmp_path: Path, tamper: str
) -> None:
    import os

    session = session_at(tmp_path)
    session.create()
    original = (session.output / "recovery.json").read_bytes()
    calls = Path(session.ticket["runtime_state"]) / "calls.jsonl"
    before = calls.read_bytes()
    ticket = session.output / "recovery.json"
    moved = None
    try:
        if tamper == "env":
            session.ticket["env"]["LD_PRELOAD"] = "/outside/synthetic"
            session.save()
        elif tamper in ("state-symlink", "home-symlink"):
            target = Path(
                session.ticket["runtime_state" if tamper == "state-symlink" else "runtime_home"]
            )
            moved = target.with_name(target.name + "-moved")
            target.rename(moved)
            target.symlink_to(moved, target_is_directory=True)
        elif tamper == "duplicate":
            ticket.write_bytes(original.rstrip()[:-1] + b', "machine_name":"other"}')
        else:
            ticket.unlink()
            os.mkfifo(ticket, 0o600)
        with pytest.raises((SkillEvalError, OSError)):
            recover(session.output)
        assert calls.read_bytes() == before
    finally:
        if moved is not None:
            target.unlink()
            moved.rename(target)
        if tamper == "fifo":
            ticket.unlink()
        ticket.write_bytes(original)
        ticket.chmod(0o600)
        assert recover(session.output)["cleanup_complete"]


def test_interruption_after_home_removal_recovers_idempotently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from skilleval import lifecycle

    session = session_at(tmp_path)
    session.create()
    original = lifecycle.shutil.rmtree

    def removed_then_interrupted(target: str) -> None:
        original(target)
        raise KeyboardInterrupt

    monkeypatch.setattr(lifecycle.shutil, "rmtree", removed_then_interrupted)
    with pytest.raises(KeyboardInterrupt):
        session.cleanup()
    assert not Path(session.ticket["runtime_home"]).exists()
    assert session.ticket["home_removal_authorized"] is True
    assert session.ticket["cleanup_complete"] is False
    monkeypatch.setattr(lifecycle.shutil, "rmtree", original)
    result = recover(session.output)
    assert result["cleanup_complete"] and result["observed_absent"]
    assert recover(session.output) == result


def test_probe_observer_receives_only_registered_tokens_and_remains_alive(tmp_path: Path) -> None:
    import ast
    import runpy
    import socket
    import tempfile
    import threading

    probe = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "qualify-lifecycle.py"))
    # macOS's 103-byte UDS pathname limit also applies to this synthetic control.
    with tempfile.TemporaryDirectory(prefix="sko-", dir="/tmp") as shallow:
        observer = probe["Heartbeats"](Path(shallow) / "observer.sock")
        token = observer.register()
        stop = threading.Event()

        def send() -> None:
            while not stop.is_set():
                for message in ("unregistered", token):
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                        connection.connect(str(observer.path))
                        connection.sendall((message + "\n").encode())
                time.sleep(0.05)

        sender = threading.Thread(target=send)
        sender.start()
        try:
            assert observer.positive(token)
            assert set(observer.counts) == {token}
        finally:
            stop.set()
            sender.join(timeout=1)
        quiet = observer.stopped(token)
        assert quiet["stopped"] and quiet["observer_alive"]
        assert quiet["before"] >= 3
        assert observer.close() == {"thread_stopped": True, "socket_closed": True}
        assert observer.stopped(token)["stopped"] is False  # dead detector is never absence
    for wait in (True, False):
        command = probe["background_command"]("synthetic", wait=wait)
        ast.parse(command[-1])
    ast.parse(probe["heartbeat_code"]("synthetic"))


def test_probe_final_identity_failure_preserves_partial_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runpy

    probe = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "qualify-lifecycle.py"))
    function = probe["run"]
    calls = []

    def identity(_runtime: str) -> dict[str, str]:
        calls.append(True)
        if len(calls) > 1:
            raise SkillEvalError("identity unavailable at final snapshot")
        return {"synthetic": "initial"}

    def refused(*_args: object, **_kwargs: object) -> None:
        raise SkillEvalError("synthetic refusal before VM launch")

    monkeypatch.setitem(function.__globals__, "snapshot_identity", identity)
    monkeypatch.setitem(function.__globals__, "__file__", str(tmp_path / "scripts" / "probe.py"))
    monkeypatch.setattr(probe["Lifecycle"], "prepare", refused)
    output = tmp_path / ".local" / "result"
    report = function(tmp_path / "runtime", tmp_path / "image", "b" * 64, output)
    assert report["status"] == "partial" and report["admitted"] is False
    assert report["identity_changed"] is True
    assert "final identity" in report["error"]
    assert json.loads((output / "report.json").read_text()) == report


def test_recovery_after_partial_home_removal_finishes_authorized_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from skilleval import lifecycle

    session = session_at(tmp_path)
    session.create()
    home = Path(session.ticket["runtime_home"])
    original = lifecycle.shutil.rmtree

    def partially_removed_then_interrupted(target: str) -> None:
        original(Path(target) / "cache")
        raise KeyboardInterrupt

    monkeypatch.setattr(lifecycle.shutil, "rmtree", partially_removed_then_interrupted)
    with pytest.raises(KeyboardInterrupt):
        session.cleanup()
    assert home.exists() and not (home / "cache").exists()
    assert session.ticket["home_removal_authorized"] is True
    monkeypatch.setattr(lifecycle.shutil, "rmtree", original)
    try:
        result = recover(session.output)
        assert result["cleanup_complete"] and result["runtime_home_removed"]
        assert not home.exists()
    finally:
        original(home, ignore_errors=True)


def test_reported_observer_shutdown_failure_downgrades_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runpy
    from typing import Any

    probe = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "qualify-lifecycle.py"))
    function = probe["run"]

    class ObserverFixture:
        def __init__(self, path: Path) -> None:
            self.path = path

        def register(self) -> str:
            return "synthetic"

        def count(self, _token: str) -> int:
            return 3

        def positive(self, *_args: object) -> bool:
            return True

        def stopped(self, _token: str) -> dict[str, Any]:
            return {"stopped": True, "observer_alive": True, "before": 3, "after": 3}

        def close(self) -> dict[str, bool]:
            return {"thread_stopped": False, "socket_closed": True}

    class SessionFixture:
        def __init__(self) -> None:
            self.ticket = {"cleanup_complete": False}

        @classmethod
        def prepare(cls, *_args: object, **_kwargs: object) -> SessionFixture:
            return cls()

        def create(self) -> None:
            pass

        def guest(self, *_args: object) -> dict[str, Any]:
            return {"returncode": 124, "timed_out": False, "stdout": "timed out", "stderr": ""}

        def cleanup(self) -> dict[str, Any]:
            self.ticket["cleanup_complete"] = True
            return self.ticket

    globals_ = function.__globals__
    monkeypatch.setitem(globals_, "__file__", str(tmp_path / "scripts" / "probe.py"))
    monkeypatch.setitem(globals_, "snapshot_identity", lambda _runtime: {"synthetic": "identity"})
    monkeypatch.setitem(globals_, "Heartbeats", ObserverFixture)
    monkeypatch.setitem(globals_, "Lifecycle", SessionFixture)
    monkeypatch.setitem(globals_, "marker_positive", lambda *_args: {"both_known_positive": True})
    monkeypatch.setitem(globals_, "marker_absence", lambda *_args: {"both_absent": True})
    monkeypatch.setitem(globals_, "interrupted_control", lambda *_args: {"cleanup_complete": True})
    ticks = iter([0.0, 3.0])
    monkeypatch.setattr(probe["time"], "monotonic", lambda: next(ticks))
    output = tmp_path / ".local" / "observer-failure"
    result = function(tmp_path / "runtime", tmp_path / "image", "b" * 64, output)
    assert result["observer_cleanup"]["thread_stopped"] is False
    assert result["status"] == "partial" and result["admitted"] is False
    assert "observer" in result["error"]
    assert json.loads((output / "report.json").read_text()) == result
