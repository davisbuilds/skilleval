"""Durable, explicit VM lifecycle controls. Never grants scored admission.

A recovery ticket is committed before create can have effects. The same private
HOME/XDG namespace and pinned runtime are used for observation and recovery.
Guest execution timeouts are client observations; whole-machine teardown owns
termination of detached guest descendants.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from skilleval.errors import SkillEvalError
from skilleval.evidence import _directory, _parse_json, _read_file
from skilleval.identity import snapshot_identity
from skilleval.qualification import _image_archive, _invoke

TICKET = "recovery.json"
TICKET_BYTES = 16 * 1024 * 1024


def _write(path: Path, value: dict[str, Any]) -> None:
    """Atomic replace and fsync both the bytes and containing directory."""
    descriptor, name = tempfile.mkstemp(prefix=".lifecycle-", dir=path.parent)
    temporary = Path(name)
    with os.fdopen(descriptor, "w") as target:
        json.dump(value, target, indent=2, sort_keys=True)
        target.write("\n")
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def inventory_names(result: dict[str, Any]) -> set[str]:
    """Reject blind, truncated, malformed, or unsuccessful observations."""
    if not successful(result) or result.get("output_truncated"):
        raise SkillEvalError("machine inventory observation failed")
    try:
        rows = _parse_json(result["stdout"].encode())
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("name"), str) for row in rows
        ):
            raise ValueError
        names = {row["name"] for row in rows}
        if len(names) != len(rows):
            raise ValueError
        return names
    except (ValueError, KeyError, TypeError, SkillEvalError):
        raise SkillEvalError("machine inventory returned invalid JSON") from None


def successful(result: dict[str, Any]) -> bool:
    return (
        result.get("returncode") == 0
        and not result.get("timed_out")
        and not result.get("pipe_drain_incomplete")
    )


def _environment(state: Path, home: Path) -> dict[str, str]:
    return {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(state / "config"),
        "XDG_DATA_HOME": str(state / "data"),
        "XDG_CACHE_HOME": str(home / "cache"),
        "XDG_RUNTIME_DIR": str(home / "run"),
        "TMPDIR": str(state / "tmp"),
        "DOCKER_CONFIG": str(state / "docker"),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "C.UTF-8",
    }


def _namespace(output: Path, ticket: dict[str, Any]) -> None:
    state = output / "runtime-state"
    home = Path(ticket["runtime_home"])
    if (
        ticket["output"] != str(output)
        or ticket["runtime_state"] != str(state)
        or home.parent != Path("/private/tmp" if sys.platform == "darwin" else "/tmp")
        or not re.fullmatch(r"(?:skl|skn|ske)-[a-zA-Z0-9_]+", home.name)
        or ticket["env"] != _environment(state, home)
    ):
        raise SkillEvalError("invalid lifecycle runtime namespace")
    directories = [output, state, *(state / name for name in ("config", "data", "tmp", "docker"))]
    home_directories = [home, home / "cache", home / "run"]
    if ticket.get("home_removal_authorized"):
        # A killed rmtree can leave any subset of these directories. Existing
        # entries must still be private real directories; missing ones are only
        # allowed after machine absence durably authorized home removal.
        directories += [entry for entry in home_directories if entry.exists() or entry.is_symlink()]
    else:
        directories += home_directories
    for directory in directories:
        with _directory(directory) as descriptor:
            if os.fstat(descriptor).st_mode & 0o077:
                raise SkillEvalError("lifecycle runtime namespace must be private")


class LifecycleInterruptedError(Exception):
    """Catchable controller interruption; SIGKILL requires explicit recovery."""


@contextmanager
def interrupt_scope() -> Iterator[None]:
    """Make SIGTERM follow Python unwinding, including client group termination."""
    if threading.current_thread() is not threading.main_thread():
        raise SkillEvalError("lifecycle signal scope requires the main thread")

    def interrupted(number: int, _frame: Any) -> None:
        raise LifecycleInterruptedError(f"controller interrupted by signal {number}")

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


class Lifecycle:
    """One disposable machine with durable state, not a general guest runner."""

    def __init__(self, output: Path, ticket: dict[str, Any]) -> None:
        self.output = output
        self.ticket = ticket

    @classmethod
    def prepare(
        cls,
        output: Path,
        runtime: Path,
        image: Path,
        image_sha256: str,
        *,
        observer_socket: Path | None = None,
    ) -> Lifecycle:
        identity = snapshot_identity(str(runtime))
        image_identity = _image_archive(image, image_sha256)
        output = output.resolve()
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
        state = output / "runtime-state"
        state.mkdir(mode=0o700)
        home = Path(
            tempfile.mkdtemp(
                prefix="skl-", dir="/private/tmp" if sys.platform == "darwin" else "/tmp"
            )
        )
        for name in ("config", "data", "tmp", "docker"):
            (state / name).mkdir(mode=0o700)
        for name in ("cache", "run"):
            (home / name).mkdir(mode=0o700)
        env = _environment(state, home)
        copied_image = state / "image.tar"
        _image_archive(image, image_sha256, copied_image)
        _image_archive(copied_image, image_sha256)
        config = {
            "cpus": 2,
            "memory_mib": 2048,
            "guest_network": False,
            "host_mounts": [],
            "secrets": [],
            "observer_socket": str(observer_socket) if observer_socket else None,
            "guest_observer_socket": "/run/lifecycle.sock" if observer_socket else None,
        }
        return cls.register(
            output,
            state,
            home,
            env,
            # The guest kernel and overlay metadata expose this identifier.
            "workspace-" + uuid.uuid4().hex,
            runtime,
            image_identity,
            config,
            identity=identity,
        )

    @classmethod
    def register(
        cls,
        output: Path,
        state: Path,
        home: Path,
        env: dict[str, str],
        machine: str,
        runtime: Path,
        image_identity: dict[str, Any] | None,
        config: dict[str, Any],
        *,
        identity: dict[str, Any] | None = None,
    ) -> Lifecycle:
        """Register an existing isolated namespace before its first VM create.

        Offline/native routes may retain their own execution/report steps while
        using this durable recovery and same-state cleanup contract.
        """
        if (output / TICKET).exists():
            raise SkillEvalError("lifecycle ticket already exists")
        ticket: dict[str, Any] = {
            "schema_version": 1,
            "kind": "skilleval-lifecycle-recovery",
            "admitted": False,
            "identity": identity or snapshot_identity(str(runtime)),
            "image": image_identity,
            "output": str(output),
            "runtime_state": str(state),
            "runtime_home": str(home),
            "env": env,
            "machine_name": machine,
            "config": config,
            "config_sha256": hashlib.sha256(
                json.dumps(config, sort_keys=True).encode()
            ).hexdigest(),
            "phase": "prepared",
            "create_attempted": False,
            "observer_positive": False,
            "observed_absent": False,
            "cleanup_complete": False,
            "runtime_home_removed": False,
            "home_removal_authorized": False,
            "steps": [],
            "cleanup": [],
        }
        _namespace(output, ticket)
        session = cls(output, ticket)
        session.save()  # durable before any VM mutation, including partial create
        return session

    @classmethod
    def load(cls, output: Path) -> Lifecycle:
        output = output.resolve()
        try:
            with _directory(output) as directory:
                raw, _ = _read_file(directory, TICKET, TICKET_BYTES)
                if os.stat(TICKET, dir_fd=directory, follow_symlinks=False).st_mode & 0o077:
                    raise ValueError
            ticket = _parse_json(raw)
            if (
                ticket["kind"] != "skilleval-lifecycle-recovery"
                or ticket["schema_version"] != 1
                or not re.fullmatch(
                    r"(?:workspace|skilleval-(?:lifecycle|native|offline))-[a-f0-9]{32}",
                    ticket["machine_name"],
                )
                or hashlib.sha256(json.dumps(ticket["config"], sort_keys=True).encode()).hexdigest()
                != ticket["config_sha256"]
            ):
                raise ValueError
            _namespace(output, ticket)
        except (OSError, ValueError, KeyError, TypeError):
            raise SkillEvalError("invalid private lifecycle recovery ticket") from None
        session = cls(output, ticket)
        session.check_identity()
        return session

    @property
    def machine(self) -> str:
        return str(self.ticket["machine_name"])

    def save(self) -> None:
        _write(self.output / TICKET, self.ticket)

    def check_identity(self) -> None:
        current = snapshot_identity(self.ticket["identity"]["runtime"]["binary"])
        self.ticket["identity_changed"] = current != self.ticket["identity"]
        if self.ticket["identity_changed"]:
            self.ticket["error"] = (
                "runtime, host, or control source identity changed; state retained"
            )
            self.save()
            raise SkillEvalError(self.ticket["error"])

    def invoke(self, label: str, arguments: list[str], timeout: int = 30) -> dict[str, Any]:
        result = _invoke(
            [self.ticket["identity"]["runtime"]["binary"], *arguments],
            Path(self.ticket["runtime_state"]),
            self.ticket["env"],
            timeout,
        )
        self.ticket["steps"].append({"label": label, **result})
        self.save()
        return result

    def observe(self, label: str) -> set[str]:
        names = inventory_names(self.invoke(label, ["machine", "ls", "--json"]))
        if self.machine in names:
            self.ticket["observer_positive"] = True
            self.save()
        return names

    def create(self) -> None:
        self.check_identity()
        if self.ticket["create_attempted"]:
            raise SkillEvalError("lifecycle ticket cannot be reused to create a machine")
        _image_archive(
            Path(self.ticket["runtime_state"]) / "image.tar", self.ticket["image"]["sha256"]
        )
        version = self.invoke("runtime_version", ["--version"], 10)
        if not successful(version) or not re.fullmatch(r"smolvm 1\.32\.3\s*", version["stdout"]):
            raise SkillEvalError("runtime version differs from inspected lifecycle contract")
        if self.machine in self.observe("inventory_before_create"):
            raise SkillEvalError("fresh machine name already present")
        self.mark_create_attempted()
        config = self.ticket["config"]
        arguments = [
            "machine",
            "create",
            "--name",
            self.machine,
            "--image",
            str(Path(self.ticket["runtime_state"]) / "image.tar"),
            "--cpus",
            str(config["cpus"]),
            "--mem",
            str(config["memory_mib"]),
        ]
        if config["observer_socket"]:
            arguments += [
                "--mount-socket",
                f"{config['observer_socket']}:{config['guest_observer_socket']}",
            ]
        arguments += ["--", "sleep", "infinity"]
        if not successful(self.invoke("create", arguments, 300)):
            raise SkillEvalError("VM create failed; recover the durable ticket")
        if not successful(self.invoke("start", ["machine", "start", "--name", self.machine], 120)):
            raise SkillEvalError("VM start failed; recover the durable ticket")
        if self.machine not in self.observe("inventory_known_present"):
            raise SkillEvalError("same-state inventory missed known-present machine")
        self.ticket["phase"] = "running"
        self.save()

    def mark_create_attempted(self) -> None:
        """Commit the recovery obligation before invoking an external create."""
        if self.ticket["create_attempted"]:
            raise SkillEvalError("lifecycle create was already attempted")
        self.ticket["create_attempted"] = True
        self.ticket["phase"] = "create-attempted"
        self.save()

    def guest(self, label: str, argv: list[str], timeout: int = 30) -> dict[str, Any]:
        return self.invoke(
            label,
            ["machine", "exec", "--name", self.machine, "--timeout", f"{timeout}s", "--", *argv],
            timeout + 5,
        )

    def cleanup(self) -> dict[str, Any]:
        """Idempotent recovery; every failure retains HOME and its recovery ticket."""
        try:
            self.check_identity()
            _namespace(self.output, self.ticket)
            if self.ticket["cleanup_complete"]:
                return self.ticket
            self.ticket["phase"] = "recovering"
            self.ticket["observed_absent"] = False
            self.save()
            if self.ticket["create_attempted"]:
                present = self.machine in self.observe("recovery_inventory_before")
                if present:
                    for action in ("stop", "delete"):
                        arguments = ["machine", action, "--name", self.machine]
                        if action == "delete":
                            arguments.append("--force")
                        result = self.invoke("recovery_" + action, arguments)
                        self.ticket["cleanup"].append({"action": action, **result})
                        self.save()
                        if not successful(result):
                            raise SkillEvalError(f"VM {action} failed; state retained for recovery")
                if self.machine in self.observe("recovery_inventory_after"):
                    raise SkillEvalError("machine remains in same-state inventory")
                self.ticket["observed_absent"] = True
                self.save()
                if not self.ticket["observer_positive"]:
                    raise SkillEvalError(
                        "absence lacks a known-present same-state inventory control"
                    )
            self.check_identity()
            self.ticket["home_removal_authorized"] = True
            self.save()
            if Path(self.ticket["runtime_home"]).exists():
                shutil.rmtree(self.ticket["runtime_home"])
            self.ticket["runtime_home_removed"] = True
            self.ticket["cleanup_complete"] = True
            self.ticket["phase"] = "recovered"
            self.ticket.pop("error", None)
        except (OSError, SkillEvalError) as error:
            self.ticket["error"] = str(error)
            self.ticket["cleanup_complete"] = False
        finally:
            self.save()
            if self.ticket["cleanup_complete"]:
                from skilleval.retention import prune_image

                prune_image(self.output, apply=True)
        return self.ticket


def recover(output: Path) -> dict[str, Any]:
    """Explicit recovery of exactly the machine bound to a private ticket."""
    return Lifecycle.load(output).cleanup()
