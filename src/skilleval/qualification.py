"""Explicit credential-free offline probes, never scored-run admission.

CLI contract inspected at smol-machines/smolvm tag v1.32.3, src/cli/machine.rs.
Registry image acquisition may use host networking; guest networking is disabled.
Execution supports only explicit docker.io/ references: v1.32.3's
src/data/pack_ref.rs skips automatic packed-machine policy import for Docker Hub.
Other registries remain plan-only pending artifact-type attestation.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import tarfile
import threading
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any, BinaryIO

from skilleval.errors import SkillEvalError
from skilleval.evidence import MAX_MANIFEST_BYTES, _directory, _read_file, verify

RUNTIME_VERSION = "1.32.3"
LOG_BYTES = 64 * 1024
PENDING = [
    "effective host-filesystem and network isolation with positive controls",
    "native Git base-to-candidate history reconstruction",
    "provider authentication route",
    "native Codex model/tool loop",
    "scored campaign admission",
]


def plan_qualification(
    bundle: Path,
    image: str,
    commands: list[list[str]],
    cpus: int = 2,
    memory_mib: int = 2048,
) -> dict[str, Any]:
    """Bind a historical import to explicit VM-only project controls.

    Only ``case/source`` enters the VM. Hidden adjudication, historical reports,
    and provenance remain outside the solver filesystem.
    """
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9./_-]*@sha256:[a-f0-9]{64}", image):
        raise SkillEvalError("image must be pinned by OCI sha256 digest, without a tag")
    if type(cpus) is not int or not 1 <= cpus <= 32:
        raise SkillEvalError("cpus must be between 1 and 32")
    if type(memory_mib) is not int or not 256 <= memory_mib <= 65536:
        raise SkillEvalError("memory_mib must be between 256 and 65536")
    if not isinstance(commands, list) or not commands:
        raise SkillEvalError("at least one explicit command argv is required")
    for argv in commands:
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv)
        ):
            raise SkillEvalError("command must be a nonempty argv list of nonempty strings")
    # Verify before reading the manifest digest, then bind that exact version.
    # Reuse bounded no-follow reads if the path is replaced between checks.
    verify(bundle)
    with _directory(bundle) as root:
        raw, _ = _read_file(root, "manifest.json", MAX_MANIFEST_BYTES)
    bundle_digest = hashlib.sha256(raw).hexdigest()
    manifest = verify(bundle, bundle_digest)
    if manifest["kind"] != "historical_review_import":
        raise SkillEvalError("first qualification path requires a historical review import")
    if not any(entry["path"].startswith("case/source/") for entry in manifest["files"]):
        raise SkillEvalError("captured review has no case/source task files")
    return {
        "schema_version": 1,
        "status": "planned",
        "admitted": False,
        "bundle": str(bundle.absolute()),
        "bundle_sha256": bundle_digest,
        "task_relative_path": "case/source",
        "image": image,
        "runtime": {
            "binary": "smolvm",
            "required_version": RUNTIME_VERSION,
            "executable_image_supported": image.startswith("docker.io/"),
            "image_route": "docker-hub-oci-only",
        },
        "cpus": cpus,
        "memory_mib": memory_mib,
        "policy": {"guest_network": False, "host_mounts": [], "secrets": []},
        "commands": [{"argv": argv.copy(), "timeout_seconds": 120} for argv in commands],
        "pending": PENDING.copy(),
    }


def _archive_task(bundle: Path, manifest: dict[str, Any], destination: Path) -> None:
    """Archive only manifest-listed source bytes, with no-follow path traversal."""
    with tarfile.open(destination, "w") as archive:
        for entry in manifest["files"]:
            if not entry["path"].startswith("case/source/"):
                continue
            descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
            try:
                components = (bundle.absolute() / entry["path"]).parts[1:]
                for component in components[:-1]:
                    child = os.open(
                        component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                    )
                    os.close(descriptor)
                    descriptor = child
                handle = os.open(
                    components[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor
                )
                try:
                    info = os.fstat(handle)
                    if not stat.S_ISREG(info.st_mode) or info.st_size != entry["size"]:
                        raise SkillEvalError("task changed before transfer")
                    with os.fdopen(os.dup(handle), "rb") as source:
                        data = source.read(entry["size"] + 1)
                finally:
                    os.close(handle)
            finally:
                os.close(descriptor)
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise SkillEvalError("task digest changed before transfer")
            item = tarfile.TarInfo(entry["path"].removeprefix("case/source/"))
            item.size = len(data)
            item.mode = 0o755 if entry["executable"] else 0o644
            archive.addfile(item, io.BytesIO(data))


def _invoke(argv: list[str], cwd: Path, env: dict[str, str], timeout: int) -> dict[str, Any]:
    """Drain both pipes continuously while retaining at most LOG_BYTES each."""
    buffers: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    sizes = {"stdout": 0, "stderr": 0}

    def drain(pipe: BinaryIO, key: str) -> None:
        with pipe:
            while chunk := pipe.read(8192):
                sizes[key] += len(chunk)
                buffers[key].extend(chunk[: max(0, LOG_BYTES - len(buffers[key]))])

    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        return {"argv": argv, "returncode": None, "error": str(error), "timed_out": False}
    assert process.stdout is not None and process.stderr is not None
    threads = [
        threading.Thread(target=drain, args=(process.stdout, "stdout"), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, "stderr"), daemon=True),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    except BaseException:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise
    for thread in threads:
        thread.join(timeout=2)
    return {
        "argv": argv,
        "returncode": process.returncode,
        "timed_out": timed_out,
        **{key: bytes(value).decode("utf-8", errors="replace") for key, value in buffers.items()},
        "output_bytes": sizes.copy(),
        "output_truncated": any(value > LOG_BYTES for value in sizes.values()),
        "pipe_drain_incomplete": any(thread.is_alive() for thread in threads),
    }


def run_offline_qualification(
    plan: dict[str, Any], output: Path, runtime_binary: str = "smolvm"
) -> dict[str, Any]:
    """Explicitly run controls in a fresh VM, retaining bounded lifecycle receipts.

    The caller authorizes VM creation and possible registry image acquisition by
    invoking this function. No credentials, host mounts, or inherited environment
    are supplied. Cleanup is attempted after crashes and timeouts; failures retain
    the machine name and isolated runtime state for operator recovery.
    """
    try:
        bundle = Path(plan["bundle"])
        rebuilt = plan_qualification(
            bundle,
            plan["image"],
            [entry["argv"] for entry in plan["commands"]],
            plan["cpus"],
            plan["memory_mib"],
        )
    except (KeyError, TypeError) as error:
        raise SkillEvalError("invalid qualification plan") from error
    if rebuilt != plan:
        raise SkillEvalError("qualification plan or captured bundle changed; replan explicitly")
    manifest = verify(bundle, plan["bundle_sha256"])
    # v1.32.3 create --image otherwise auto-restores registry packs, inheriting
    # their network and mount policy. The literal Docker Hub route bypasses
    # that detector before fetching or interpreting a packed manifest.
    if not plan["image"].startswith("docker.io/"):
        raise SkillEvalError(
            "image is plan-only: executable qualification requires explicit docker.io/ "
            "OCI references; other registries may restore packed runtime policy"
        )
    resolved = shutil.which(runtime_binary)
    if resolved is None:
        raise SkillEvalError(f"runtime binary unavailable: {runtime_binary}; nothing launched")
    binary = str(Path(resolved).resolve())
    output = output.resolve()
    captured_root = bundle.resolve()
    if output == captured_root or captured_root in output.parents:
        raise SkillEvalError("qualification output must be outside captured evidence")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    state = output / "runtime-state"
    state.mkdir(mode=0o700)
    for directory in ("home", "config", "data", "cache", "run", "tmp", "docker"):
        (state / directory).mkdir(mode=0o700)
    env = {
        "HOME": str(state / "home"),
        "XDG_CONFIG_HOME": str(state / "config"),
        "XDG_DATA_HOME": str(state / "data"),
        "XDG_CACHE_HOME": str(state / "cache"),
        "XDG_RUNTIME_DIR": str(state / "run"),
        "TMPDIR": str(state / "tmp"),
        "DOCKER_CONFIG": str(state / "docker"),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "C.UTF-8",
    }
    name = "skilleval-offline-" + uuid.uuid4().hex
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "partial",
        "admitted": False,
        "machine_name": name,
        "plan": plan,
        "runtime_binary": binary,
        "runtime_sha256": hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
        "runtime_state": str(state),
        "steps": [],
        "cleanup": [],
        "pending": PENDING.copy(),
        "limits": [
            "offline controls do not qualify the provider/tool loop or effective isolation",
            "OCI acquisition may contact registries from the host",
        ],
    }
    attempted_create = False

    def step(label: str, arguments: list[str], timeout: int = 120) -> bool:
        result = _invoke([binary, *arguments], state, env, timeout)
        report["steps"].append({"label": label, **result})
        return (
            result["returncode"] == 0
            and not result["timed_out"]
            and not result.get("pipe_drain_incomplete", False)
        )

    def guest(label: str, command: list[str], optional: bool = False) -> bool:
        passed = step(
            label,
            [
                "machine",
                "exec",
                "--name",
                name,
                "--workdir",
                "/workspace/task",
                "--timeout",
                "120s",
                "--",
                *command,
            ],
            125,
        )
        report["steps"][-1]["optional"] = optional
        return passed

    try:
        if not step("runtime_version", ["--version"], 10):
            return report
        version = report["steps"][-1]["stdout"].strip()
        if not re.search(r"(?<![0-9.])1\.32\.3(?![0-9.])", version):
            report["error"] = "runtime version differs from inspected CLI contract"
            return report
        archive = state / "task.tar"
        _archive_task(bundle, manifest, archive)
        attempted_create = True
        if not step(
            "create",
            [
                "machine",
                "create",
                "--name",
                name,
                "--image",
                plan["image"],
                "--cpus",
                str(plan["cpus"]),
                "--mem",
                str(plan["memory_mib"]),
            ],
            300,
        ):
            return report
        if not step("start", ["machine", "start", "--name", name]):
            return report
        if not step("copy_source", ["machine", "cp", str(archive), name + ":/workspace/task.tar"]):
            return report
        if not step(
            "prepare",
            [
                "machine",
                "exec",
                "--name",
                name,
                "--timeout",
                "120s",
                "--",
                "mkdir",
                "-p",
                "/workspace/task",
            ],
        ):
            return report
        if not guest(
            "extract_source",
            [
                "tar",
                "-xf",
                "/workspace/task.tar",
                "-C",
                "/workspace/task",
            ],
        ):
            return report
        if not guest("git_version", ["git", "--version"]):
            return report
        passed = True
        for index, command in enumerate(plan["commands"]):
            passed = guest(f"project_control_{index}", command["argv"]) and passed
        guest("codex_version", ["codex", "--version"], optional=True)
        if passed:
            report["status"] = "offline_passed"
        return report
    finally:
        if attempted_create:
            for action in ("stop", "delete"):
                arguments = [binary, "machine", action, "--name", name]
                if action == "delete":
                    # DEVNULL cannot answer the interactive confirmation prompt.
                    arguments.append("--force")
                result = _invoke(arguments, state, env, 30)
                report["cleanup"].append({"action": action, **result})
            report["cleanup_complete"] = all(
                item["returncode"] == 0
                and not item["timed_out"]
                and not item.get("pipe_drain_incomplete", False)
                for item in report["cleanup"]
            )
            if not report["cleanup_complete"]:
                report["status"] = "partial"
        (output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
