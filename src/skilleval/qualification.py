"""Explicit credential-free offline probes, never scored-run admission.

CLI contract inspected at smol-machines/smolvm tag v1.32.3, src/cli/machine.rs.
Registry image acquisition may use host networking; guest networking is disabled.
Execution supports explicit docker.io/ references and hash-pinned local Docker-save
archives. v1.32.3's src/data/pack_ref.rs skips automatic packed-machine policy import
for those routes. Other registries remain plan-only pending artifact-type attestation.
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
import sys
import tarfile
import tempfile
import threading
import uuid
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from skilleval.errors import SkillEvalError
from skilleval.evidence import (
    MAX_MANIFEST_BYTES,
    _directory,
    _parse_json,
    _read_file,
    _walk,
    verify,
)
from skilleval.workspace import prepare_review

RUNTIME_VERSION = "1.32.3"
LOG_BYTES = 64 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024 * 1024
PENDING = [
    "effective host-filesystem and network isolation with positive controls",
    "native Git base-to-candidate history reconstruction",
    "provider authentication route",
    "native Codex model/tool loop",
    "scored campaign admission",
]


def _plain_tar_headers(source: BinaryIO) -> None:
    """Bound parser work before tarfile can expand PAX/GNU extension metadata."""
    length = source.seek(0, os.SEEK_END)
    source.seek(0)
    count = 0
    while header := source.read(512):
        if len(header) != 512:
            raise SkillEvalError("truncated Docker-save archive header")
        if header == b"\0" * 512:
            while tail := source.read(1024 * 1024):
                if tail.strip(b"\0"):
                    raise SkillEvalError("unexpected data after Docker-save archive end")
            source.seek(0)
            return
        count += 1
        encoded_size = header[124:136].strip(b"\0 ")
        if (
            count > 10_000
            or header[156:157] not in (b"0", b"\0", b"5")
            or not re.fullmatch(rb"[0-7]+", encoded_size)
        ):
            raise SkillEvalError("unsupported Docker-save tar header or extension")
        size = int(encoded_size, 8)
        next_header = source.tell() + ((size + 511) // 512) * 512
        if size > MAX_IMAGE_BYTES or next_header > length:
            raise SkillEvalError("Docker-save member exceeds archive bounds")
        source.seek(next_header)
    raise SkillEvalError("Docker-save archive has no end marker")


def _docker_save(source: BinaryIO) -> dict[str, Any]:
    """Inspect Docker-save structure without extracting or executing its layers."""
    _plain_tar_headers(source)
    files: dict[str, tarfile.TarInfo] = {}
    with tarfile.open(fileobj=source, mode="r:") as archive:
        for member in archive:
            name = member.name.rstrip("/") if member.isdir() else member.name
            parts = PurePosixPath(name).parts
            if (
                not name
                or name.startswith("/")
                or "\\" in name
                or any(part in ("", ".", "..") for part in name.split("/"))
                or "/".join(parts) != name
                or any(".smolmachine" in part.lower() for part in parts)
                or not (member.isfile() or member.isdir())
                or not 0 <= member.size <= MAX_IMAGE_BYTES
                or name in files
                or len(files) >= 10_000
            ):
                raise SkillEvalError("unsupported or unsafe Docker-save archive member")
            files[name] = member
        manifest_info = files.get("manifest.json")
        if (
            manifest_info is None
            or not manifest_info.isfile()
            or manifest_info.size > MAX_MANIFEST_BYTES
        ):
            raise SkillEvalError("image archive requires Docker-save manifest.json")
        manifest_file = archive.extractfile(manifest_info)
        assert manifest_file is not None
        manifest = _parse_json(manifest_file.read(MAX_MANIFEST_BYTES + 1))
        if (
            not isinstance(manifest, list)
            or len(manifest) != 1
            or not isinstance(manifest[0], dict)
        ):
            raise SkillEvalError("exactly one Docker-save image is supported")
        image = manifest[0]
        if (
            set(image) != {"Config", "RepoTags", "Layers"}
            or not isinstance(image["Config"], str)
            or not isinstance(image["Layers"], list)
            or not image["Layers"]
            or len(image["Layers"]) > 1024
            or any(not isinstance(layer, str) for layer in image["Layers"])
            or (
                image["RepoTags"] is not None
                and (
                    not isinstance(image["RepoTags"], list)
                    or any(not isinstance(tag, str) for tag in image["RepoTags"])
                )
            )
        ):
            raise SkillEvalError("unsupported Docker-save image manifest")
        required = [image["Config"], *image["Layers"]]
        if len(set(required)) != len(required) or any(
            name not in files or not files[name].isfile() for name in required
        ):
            raise SkillEvalError("Docker-save config or layers missing or duplicated")
        config_info = files[image["Config"]]
        if config_info.size > MAX_MANIFEST_BYTES:
            raise SkillEvalError("Docker-save image config size limit exceeded")
        config_file = archive.extractfile(config_info)
        assert config_file is not None
        raw_config = config_file.read(MAX_MANIFEST_BYTES + 1)
        config_digest = hashlib.sha256(raw_config).hexdigest()
        if PurePosixPath(image["Config"]).name not in (config_digest, config_digest + ".json"):
            raise SkillEvalError("Docker-save config identity mismatch")
        config = _parse_json(raw_config)
        rootfs = config.get("rootfs") if isinstance(config, dict) else None
        if (
            not isinstance(rootfs, dict)
            or rootfs.get("type") != "layers"
            or not isinstance(rootfs.get("diff_ids"), list)
            or len(rootfs["diff_ids"]) != len(image["Layers"])
            or any(
                not isinstance(value, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", value)
                for value in rootfs["diff_ids"]
            )
        ):
            raise SkillEvalError("unsupported Docker-save root filesystem config")
        # Docker-save can carry legacy per-layer metadata or modern OCI-layout
        # metadata. Never accept arbitrary extra top-level policy documents.
        allowed = {"manifest.json", "index.json", "oci-layout", *required}
        for layer in image["Layers"]:
            parent = str(PurePosixPath(layer).parent)
            if parent != ".":
                allowed.update({parent + "/VERSION", parent + "/json"})
        for name, info in files.items():
            if info.isdir() or name in allowed:
                continue
            if not re.fullmatch(r"blobs/sha256/[a-f0-9]{64}", name):
                raise SkillEvalError("unexpected file in Docker-save archive")
            # Supplemental OCI manifests are content addressed too.
            extra = archive.extractfile(info)
            assert extra is not None
            observed = hashlib.sha256()
            while chunk := extra.read(1024 * 1024):
                observed.update(chunk)
            digest = observed.hexdigest()
            if digest != PurePosixPath(name).name:
                raise SkillEvalError("Docker-save supplemental blob identity mismatch")
        return {"config_sha256": config_digest, "layer_count": len(image["Layers"])}


def _image_archive(path: Path, expected: str, copy_to: Path | None = None) -> dict[str, Any]:
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise SkillEvalError("local image archive requires an explicit SHA-256 identity")
    path = path.absolute()
    try:
        with _directory(path.parent) as parent:
            descriptor = os.open(
                path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
            )
        with os.fdopen(descriptor, "rb") as source:
            initial = os.fstat(source.fileno())
            if not stat.S_ISREG(initial.st_mode) or not 0 < initial.st_size <= MAX_IMAGE_BYTES:
                raise SkillEvalError("image archive must be a bounded regular file")
            digest = hashlib.sha256()
            total = 0
            copied = copy_to.open("xb") if copy_to is not None else None
            try:
                while chunk := source.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_IMAGE_BYTES:
                        raise SkillEvalError("image archive size limit exceeded")
                    digest.update(chunk)
                    if copied is not None:
                        copied.write(chunk)
            finally:
                if copied is not None:
                    copied.close()
                    assert copy_to is not None
                    copy_to.chmod(0o600)
            if digest.hexdigest() != expected or total != initial.st_size:
                raise SkillEvalError("image archive digest changed or mismatched")
            source.seek(0)
            details = _docker_save(source)
            final = os.fstat(source.fileno())
            if any(
                getattr(initial, key) != getattr(final, key)
                for key in ("st_dev", "st_ino", "st_size", "st_mode", "st_mtime_ns", "st_ctime_ns")
            ):
                raise SkillEvalError("image archive changed during inspection")
        return {"path": str(path), "sha256": expected, "size": total, **details}
    except (OSError, tarfile.TarError):
        raise SkillEvalError("cannot inspect regular Docker-save image archive") from None


def _workspace_identity(bundle: Path, digest: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="skilleval-plan-", dir=Path(tempfile.gettempdir()).resolve()
    ) as scratch:
        receipt = prepare_review(bundle, digest, Path(scratch) / "task")
    return {key: value for key, value in receipt.items() if key != "path"}


def plan_qualification(
    bundle: Path,
    image: str | None,
    commands: list[list[str]],
    cpus: int = 2,
    memory_mib: int = 2048,
    *,
    image_archive: Path | None = None,
    image_sha256: str | None = None,
    reconstruct_git: bool = False,
) -> dict[str, Any]:
    """Bind a historical import to explicit VM-only project controls.

    Only ``case/source`` and optional freshly generated Git metadata enter the VM.
    Hidden adjudication, historical reports, and provenance remain outside the
    solver filesystem. Local image archives require a caller-supplied SHA-256.
    """
    archive_identity = None
    if image_archive is not None:
        if image is not None:
            raise SkillEvalError("choose either a registry image or a local image archive")
        archive_identity = _image_archive(image_archive, image_sha256 or "")
    elif image_sha256 is not None:
        raise SkillEvalError("image_sha256 requires a local image archive")
    elif image is None or not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9./_-]*@sha256:[a-f0-9]{64}", image
    ):
        raise SkillEvalError("image must be pinned by OCI sha256 digest, without a tag")
    if type(reconstruct_git) is not bool:
        raise SkillEvalError("reconstruct_git must be an explicit boolean")
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
        "image_archive": archive_identity,
        "reconstruct_git": reconstruct_git,
        "workspace": _workspace_identity(bundle, bundle_digest) if reconstruct_git else None,
        "runtime": {
            "binary": "smolvm",
            "required_version": RUNTIME_VERSION,
            "executable_image_supported": archive_identity is not None
            or bool(image and image.startswith("docker.io/")),
            "image_route": "local-docker-save" if archive_identity else "docker-hub-oci-only",
        },
        "cpus": cpus,
        "memory_mib": memory_mib,
        "policy": {"guest_network": False, "host_mounts": [], "secrets": []},
        "commands": [{"argv": argv.copy(), "timeout_seconds": 120} for argv in commands],
        "pending": [
            item
            for item in PENDING
            if not (
                reconstruct_git and item == "native Git base-to-candidate history reconstruction"
            )
        ],
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
    plan: dict[str, Any],
    output: Path,
    runtime_binary: str = "smolvm",
    *,
    capture_identity: bool = False,
) -> dict[str, Any]:
    """Run the actual controller with catchable SIGTERM cleanup and durable recovery."""
    from skilleval.lifecycle import interrupt_scope

    with interrupt_scope():
        return _run_offline_qualification(
            plan, output, runtime_binary, capture_identity=capture_identity
        )


def _run_offline_qualification(
    plan: dict[str, Any],
    output: Path,
    runtime_binary: str = "smolvm",
    *,
    capture_identity: bool = False,
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
            image_archive=Path(plan["image_archive"]["path"])
            if plan.get("image_archive")
            else None,
            image_sha256=plan["image_archive"]["sha256"] if plan.get("image_archive") else None,
            reconstruct_git=plan.get("reconstruct_git", False),
        )
    except (KeyError, TypeError) as error:
        raise SkillEvalError("invalid qualification plan") from error
    if rebuilt != plan:
        raise SkillEvalError("qualification plan or captured bundle changed; replan explicitly")
    manifest = verify(bundle, plan["bundle_sha256"])
    # v1.32.3 create --image otherwise auto-restores registry packs, inheriting
    # their network and mount policy. The literal Docker Hub route bypasses
    # that detector before fetching or interpreting a packed manifest; existing
    # local image archive paths bypass registry pack resolution entirely.
    if not plan["image_archive"] and not plan["image"].startswith("docker.io/"):
        raise SkillEvalError(
            "image is plan-only: executable qualification requires explicit docker.io/ "
            "OCI references; other registries may restore packed runtime policy"
        )
    resolved = shutil.which(runtime_binary)
    if resolved is None:
        raise SkillEvalError(f"runtime binary unavailable: {runtime_binary}; nothing launched")
    binary = str(Path(resolved).resolve())
    # Legacy/offline probes remain usable with alternate trusted executables.
    # Admission requires this explicit complete-distribution capture route.
    from skilleval.identity import file_digest, snapshot_identity
    from skilleval.lifecycle import Lifecycle, LifecycleInterruptedError

    identity = snapshot_identity(binary) if capture_identity else None
    output = output.resolve()
    captured_root = bundle.resolve()
    if output == captured_root or captured_root in output.parents:
        raise SkillEvalError("qualification output must be outside captured evidence")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    state = output / "runtime-state"
    state.mkdir(mode=0o700)
    for directory in ("home", "config", "data", "cache", "run", "tmp", "docker"):
        (state / directory).mkdir(mode=0o700)
    # macOS uses HOME/Library/Caches for its 103-byte Unix socket paths, ignoring
    # XDG_CACHE_HOME. Keep the actual runtime home shallow regardless of output.
    short_parent = "/private/tmp" if sys.platform == "darwin" else "/tmp"
    runtime_home = Path(tempfile.mkdtemp(prefix="ske-", dir=short_parent))
    for directory in ("cache", "run"):
        (runtime_home / directory).mkdir(mode=0o700)
    env = {
        "HOME": str(runtime_home),
        "XDG_CONFIG_HOME": str(state / "config"),
        "XDG_DATA_HOME": str(state / "data"),
        "XDG_CACHE_HOME": str(runtime_home / "cache"),
        "XDG_RUNTIME_DIR": str(runtime_home / "run"),
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
        "runtime_home": str(runtime_home),
        "runtime_home_removed": False,
        "steps": [],
        "cleanup": [],
        "pending": plan["pending"].copy(),
        "limits": [
            "offline controls do not qualify the provider/tool loop or effective isolation",
            *(
                []
                if plan["image_archive"]
                else ["OCI acquisition may contact registries from the host"]
            ),
        ],
    }
    attempted_create = False
    session = None
    if identity is not None:
        report["identity"] = identity
        session = Lifecycle.register(
            output,
            state,
            runtime_home,
            env,
            name,
            Path(binary),
            plan["image_archive"],
            {"cpus": plan["cpus"], "memory_mib": plan["memory_mib"], **plan["policy"]},
            identity=identity,
        )

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
        if plan["reconstruct_git"]:
            workspace = state / "workspace"
            prepared = prepare_review(bundle, plan["bundle_sha256"], workspace)
            identity = {key: value for key, value in prepared.items() if key != "path"}
            if identity != plan["workspace"]:
                raise SkillEvalError("reconstructed workspace identity changed; replan explicitly")
            report["workspace"] = identity
            with _directory(workspace) as source, tarfile.open(archive, "w") as task_archive:
                for path, data, executable in _walk(source):
                    item = tarfile.TarInfo(path)
                    item.size = len(data)
                    item.mode = 0o755 if executable else 0o644
                    task_archive.addfile(item, io.BytesIO(data))
        else:
            _archive_task(bundle, manifest, archive)
        image = plan["image"]
        if plan["image_archive"]:
            image = str(state / "image.tar")
            identity = _image_archive(
                Path(plan["image_archive"]["path"]), plan["image_archive"]["sha256"], Path(image)
            )
            if identity != plan["image_archive"]:
                raise SkillEvalError("local image archive changed; replan explicitly")
            # Inspect the actual immutable-path copy supplied to the runtime too.
            _image_archive(Path(image), plan["image_archive"]["sha256"])
        attempted_create = True
        if session is not None:
            session.mark_create_attempted()
        if not step(
            "create",
            [
                "machine",
                "create",
                "--name",
                name,
                "--image",
                image,
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
        if session is not None and name not in session.observe("machine_observer_positive"):
            report["error"] = "machine inventory did not detect the known-present machine"
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
        if plan["reconstruct_git"]:
            expected = plan["workspace"]
            if not guest(
                "git_history",
                ["git", "rev-parse", "HEAD", "HEAD^", "candidate^{tree}", "base^{tree}"],
            ):
                return report
            if report["steps"][-1]["stdout"].splitlines() != [
                expected["candidate_commit"],
                expected["base_commit"],
                expected["candidate_tree"],
                expected["base_tree"],
            ]:
                report["error"] = "guest native Git history differs from prepared workspace"
                return report
            if not guest("git_worktree", ["git", "status", "--porcelain"]):
                return report
            if report["steps"][-1]["stdout"].strip():
                report["error"] = "guest native Git candidate worktree differs from captured source"
                return report
            if not guest("git_objects", ["git", "fsck", "--no-reflogs"]):
                return report
        passed = True
        for index, command in enumerate(plan["commands"]):
            passed = guest(f"project_control_{index}", command["argv"]) and passed
        guest("codex_version", ["codex", "--version"], optional=True)
        if passed:
            report["status"] = "offline_passed"
        return report
    except LifecycleInterruptedError:
        report["status"] = "partial"
        report["error"] = "controller interrupted; inspect cleanup and recovery ticket"
        return report
    finally:
        if session is not None:
            ticket = session.cleanup()
            for key in ("cleanup", "cleanup_complete", "runtime_home_removed", "identity_changed"):
                report[key] = ticket.get(key)
            report["machine_observer_positive"] = ticket["observer_positive"]
            report["machine_observed_absent"] = ticket.get("observed_absent", False)
            report["artifacts"] = {"recovery.json": file_digest(output / "recovery.json")}
            if not ticket["cleanup_complete"] or ticket.get("identity_changed"):
                report["status"] = "partial"
        elif attempted_create:
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
        if session is None and (not attempted_create or report.get("cleanup_complete")):
            try:
                shutil.rmtree(runtime_home)
                report["runtime_home_removed"] = True
            except OSError:
                report["status"] = "partial"
                report["error"] = "runtime home cleanup failed; inspect retained state"
        (output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
