#!/usr/bin/env python3
"""Opt-in credential-free smolvm lifecycle probes; all reports remain unadmitted.

Run with --runtime, --image, --image-sha256, and --output under .local. Explicit
--recover OUTPUT resumes a durable ticket without creating another machine.
No model, auth, external network, task archive, or host task execution is used.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from skilleval.errors import SkillEvalError
from skilleval.identity import snapshot_identity
from skilleval.lifecycle import (
    TICKET,
    Lifecycle,
    LifecycleInterruptedError,
    _write,
    interrupt_scope,
    recover,
    successful,
)

GUEST_SOCKET = "/run/lifecycle.sock"
WORKSPACE_MARKER = "/workspace/synthetic/previous-workspace"
SESSION_MARKER = "/run/qualification/codex-home/sessions/previous-session"


class Heartbeats:
    """Bounded, receive-only observer: accepts only an exact per-control nonce."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.counts: dict[str, int] = {}
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(path))
        path.chmod(0o600)
        self.listener.listen(8)
        self.listener.settimeout(0.1)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def register(self) -> str:
        token = secrets.token_hex(24)
        with self.lock:
            self.counts[token] = 0
        return token

    def count(self, token: str) -> int:
        with self.lock:
            return self.counts[token]

    def _serve(self) -> None:
        while not self.stopping.is_set():
            try:
                connection, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(0.2)
                try:
                    token = connection.recv(64).decode("ascii").strip()
                except (OSError, UnicodeError):
                    continue
                with self.lock:
                    if token in self.counts:
                        self.counts[token] += 1

    def positive(self, token: str, timeout: float = 5) -> bool:
        initial = self.count(token)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.count(token) >= initial + 3:
                return True
            time.sleep(0.05)
        return False

    def stopped(self, token: str) -> dict[str, Any]:
        # Permit already queued heartbeats to drain, then require a quiet window.
        time.sleep(0.5)
        before = self.count(token)
        time.sleep(1)
        after = self.count(token)
        alive = self.thread.is_alive() and self.listener.fileno() >= 0
        return {
            "before": before,
            "after": after,
            "quiet_seconds": 1,
            "observer_alive": alive,
            "stopped": alive and before == after,
        }

    def close(self) -> dict[str, bool]:
        self.stopping.set()
        self.listener.close()
        self.thread.join(timeout=1)
        return {
            "thread_stopped": not self.thread.is_alive(),
            "socket_closed": self.listener.fileno() < 0,
        }


def heartbeat_code(token: str) -> str:
    return (
        "import socket,time\n"
        "while True:\n"
        " try:\n"
        "  s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.settimeout(.3)\n"
        f"  s.connect({GUEST_SOCKET!r});s.sendall({(token + chr(10)).encode()!r});s.close()\n"
        " except OSError: pass\n"
        " time.sleep(.15)\n"
    )


def background_command(token: str, *, wait: bool = False) -> list[str]:
    source = (
        "import subprocess,sys,time\n"
        f"p=subprocess.Popen([sys.executable,'-c',{heartbeat_code(token)!r}],"
        "start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
        "stderr=subprocess.DEVNULL)\n"
        "print('DETACHED_BACKGROUND_STARTED',p.pid,flush=True)\n"
    )
    if wait:
        source += "time.sleep(300)\n"
    return ["python3", "-c", source]


def require(condition: bool, detail: str) -> None:
    if not condition:
        raise SkillEvalError(detail)


def marker_positive(session: Lifecycle, token: str) -> dict[str, Any]:
    code = (
        "import json,pathlib\n"
        f"paths={[WORKSPACE_MARKER, SESSION_MARKER]!r}\n"
        f"token={token!r}\n"
        "for name in paths:\n"
        " p=pathlib.Path(name);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(token)\n"
        "print(json.dumps({name:pathlib.Path(name).read_text() for name in paths}))\n"
    )
    result = session.guest("workspace_session_known_positive", ["python3", "-c", code])
    require(successful(result), "cannot establish workspace/session markers")
    require(
        json.loads(result["stdout"]) == dict.fromkeys([WORKSPACE_MARKER, SESSION_MARKER], token),
        "workspace/session known-positive marker mismatch",
    )
    return {"paths": [WORKSPACE_MARKER, SESSION_MARKER], "both_known_positive": True}


def marker_absence(session: Lifecycle) -> dict[str, Any]:
    code = (
        "import json,pathlib\n"
        f"print(json.dumps({{name:pathlib.Path(name).exists() for name in "
        f"{[WORKSPACE_MARKER, SESSION_MARKER]!r}}}))"
    )
    result = session.guest("fresh_workspace_session_absence", ["python3", "-c", code])
    require(successful(result), "fresh machine marker observer failed")
    observed = json.loads(result["stdout"])
    require(
        observed == dict.fromkeys([WORKSPACE_MARKER, SESSION_MARKER], False), "stale state reused"
    )
    return {"observed": observed, "both_absent": True}


def worker(output: Path, token: str) -> int:
    """Separate controller deliberately interrupted with a live guest worker."""
    session = Lifecycle.load(output)
    try:
        with interrupt_scope():
            session.create()
            require(
                successful(session.guest("background_launch", background_command(token))),
                "interruption heartbeat launch failed",
            )
            session.ticket["phase"] = "interruption-ready"
            session.save()
            while True:
                time.sleep(0.1)
    except LifecycleInterruptedError as error:
        session.ticket["interruption"] = str(error)
        session.ticket["phase"] = "interrupted"
        session.save()
        return 143


def interrupted_control(
    output: Path,
    runtime: Path,
    image: Path,
    digest: str,
    observer: Heartbeats,
    number: signal.Signals,
) -> dict[str, Any]:
    token = observer.register()
    Lifecycle.prepare(output, runtime, image, digest, observer_socket=observer.path)
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--worker", str(output), "--token", token],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
        start_new_session=True,
    )
    controller_ready = False
    result: dict[str, Any] = {"signal": number.name, "admitted": False}
    try:
        deadline = time.monotonic() + 450
        while time.monotonic() < deadline and process.poll() is None:
            ticket = json.loads((output / TICKET).read_text())
            if ticket["phase"] == "interruption-ready":
                controller_ready = True
                break
            time.sleep(0.1)
        require(controller_ready, "interruption controller did not reach durable ready phase")
        require(observer.positive(token), "guest heartbeat missing before controller interruption")
        result["heartbeat_known_positive"] = True
        process.send_signal(number)
        process.wait(timeout=10)
        result["controller_returncode"] = process.returncode
        require(
            process.returncode == (143 if number == signal.SIGTERM else -signal.SIGKILL),
            "controller did not exit through the selected interruption route",
        )
        require(observer.positive(token), "guest worker did not survive controller interruption")
        result["heartbeat_survived_controller"] = True
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        recovered = recover(output)
        result["cleanup_complete"] = recovered["cleanup_complete"]
        result["ticket"] = str(output / TICKET)
        result["heartbeat_after_recovery"] = observer.stopped(token)
    require(result["cleanup_complete"], "interrupted controller recovery failed; state retained")
    require(result["heartbeat_after_recovery"]["stopped"], "heartbeat continued after recovery")
    second = recover(output)
    result["idempotent_recovery"] = second["cleanup_complete"]
    return result


def run(runtime: Path, image: Path, digest: str, output: Path) -> dict[str, Any]:
    output = output.resolve()
    # Private receipts cannot accidentally enter tracked/public source paths.
    repository = Path(__file__).resolve().parents[1]
    require(
        repository / ".local" in output.parents, "probe output must be inside repository .local"
    )
    identity = snapshot_identity(str(runtime))
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "partial",
        "admitted": False,
        "identity": identity,
        "image_sha256": digest,
        "controls": {},
        "limits": [
            "credential-free lifecycle controls only; no native model/tool admission",
            "heartbeat silence is a bounded observation paired with same-state inventory absence",
            "native execution timeout alone is not descendant termination authority",
        ],
    }
    socket_home = Path(
        tempfile.mkdtemp(prefix="sklo-", dir="/private/tmp" if sys.platform == "darwin" else "/tmp")
    )
    observer = Heartbeats(socket_home / "observer.sock")
    sessions: list[Lifecycle] = []
    try:
        with interrupt_scope():
            token = observer.register()
            first = Lifecycle.prepare(
                output / "timeout", runtime, image, digest, observer_socket=observer.path
            )
            sessions.append(first)
            first.create()
            report["controls"]["markers_positive"] = marker_positive(first, token)
            started = time.monotonic()
            timed = first.guest(
                "execution_timeout_with_detached_worker", background_command(token, wait=True), 3
            )
            elapsed = time.monotonic() - started
            diagnostic = bool(
                re.search(r"timed?\s*out|timeout", timed["stdout"] + timed["stderr"], re.IGNORECASE)
            )
            require(
                not successful(timed)
                and not timed["timed_out"]
                and 2.5 <= elapsed < 8
                and diagnostic,
                "native guest execution timeout was ineffective or client-only",
            )
            require(
                observer.count(token) >= 3, "timeout guest heartbeat lacks a known-positive control"
            )
            continued = observer.positive(token, 2)
            report["controls"]["execution_timeout"] = {
                "native_returncode": timed["returncode"],
                "controller_timed_out": timed["timed_out"],
                "native_timeout_diagnostic": diagnostic,
                "elapsed_seconds": elapsed,
                "heartbeat_known_positive": True,
                "detached_worker_continued_after_native_timeout": continued,
            }
            cleaned = first.cleanup()
            require(cleaned["cleanup_complete"], "timeout machine cleanup failed; state retained")
            stopped = observer.stopped(token)
            require(stopped["stopped"], "detached heartbeat survived whole-machine cleanup")
            report["controls"]["whole_machine_termination"] = stopped
            fresh = Lifecycle.prepare(
                output / "fresh", runtime, image, digest, observer_socket=observer.path
            )
            sessions.append(fresh)
            fresh.create()
            report["controls"]["fresh_workspace_session"] = marker_absence(fresh)
            require(fresh.cleanup()["cleanup_complete"], "fresh machine cleanup failed")
            for number in (signal.SIGTERM, signal.SIGKILL):
                report["controls"][number.name] = interrupted_control(
                    output / number.name.lower(), runtime, image, digest, observer, number
                )
            report["status"] = "lifecycle_controls_passed"
    except (SkillEvalError, LifecycleInterruptedError, OSError, ValueError) as error:
        report["error"] = str(error)
    finally:
        for session in sessions:
            if not session.ticket["cleanup_complete"]:
                session.cleanup()
        report["observer_cleanup"] = observer.close()
        if not all(report["observer_cleanup"].values()):
            report["status"] = "partial"
            report["error"] = "host observer did not stop or close"
        try:
            shutil.rmtree(socket_home)
        except OSError:
            report["status"] = "partial"
            report["error"] = "observer directory cleanup failed"
        report["observer_directory_removed"] = not socket_home.exists()
        report["sessions"] = [session.ticket for session in sessions]
        try:
            report["identity_changed"] = snapshot_identity(str(runtime)) != identity
        except (SkillEvalError, OSError):
            report["identity_changed"] = True
            report["error"] = "final identity snapshot unavailable; retained partial report"
        if report["identity_changed"]:
            report["status"] = "partial"
            report.setdefault("error", "runtime, host, or control sources changed during probe")
        _write(output / "report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--image-sha256")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--recover", type=Path)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--token", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        return worker(args.worker, args.token)
    if args.recover:
        result = recover(args.recover)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["cleanup_complete"] else 1
    if not all((args.runtime, args.image, args.image_sha256, args.output)):
        parser.error("provide --runtime, --image, --image-sha256, --output or explicit --recover")
    report = run(args.runtime, args.image, args.image_sha256, args.output)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "lifecycle_controls_passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
