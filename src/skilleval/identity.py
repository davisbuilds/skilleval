"""Local qualification identities, captured before and after effective controls.

These fingerprints detect changes under a trusted local operator. They are not
signatures, a sandbox against that operator, or an attestation of host integrity.
"""

import errno
import hashlib
import json
import os
import platform
import re
import shutil
import stat
from pathlib import Path
from typing import Any

from skilleval.errors import SkillEvalError

SOURCES = (
    "src/skilleval/identity.py",
    "src/skilleval/lifecycle.py",
    "src/skilleval/retention.py",
    "src/skilleval/study.py",
    "src/skilleval/handoff.py",
    "src/skilleval/acceptance.py",
    "src/skilleval/accounting.py",
    "src/skilleval/efficiency.py",
    "src/skilleval/repair.py",
    "src/skilleval/repair_check.py",
    "src/skilleval/repair_guest.py",
    "src/skilleval/repair_assessment.py",
    "src/skilleval/assessment_observations.py",
    "src/skilleval/citation_correction.py",
    "src/skilleval/repair_outcomes.py",
    "src/skilleval/task_package.py",
    "src/skilleval/task_environment.py",
    "src/skilleval/lane_validation.py",
    "src/skilleval/lane_readiness.py",
    "src/skilleval/task_transfer.py",
    "src/skilleval/native_process.py",
    "src/skilleval/task_validation_guest.py",
    "src/skilleval/reliability.py",
    "src/skilleval/calibration.py",
    "src/skilleval/qualification.py",
    "src/skilleval/gateway.py",
    "src/skilleval/budgets.py",
    "src/skilleval/harnesses/codex.py",
    "src/skilleval/evidence.py",
    "src/skilleval/workspace.py",
    "src/skilleval/contracts.py",
    "src/skilleval/_vendor/openbench_gateway.py",
    "src/skilleval/runner.py",
    "src/skilleval/guest_runner.py",
    "src/skilleval/app_server.py",
    "src/skilleval/solver_boundary.py",
    "src/skilleval/context_guard.py",
    "src/skilleval/judge.py",
    "src/skilleval/treatments.py",
    "scripts/qualify-native.py",
    "scripts/qualify-rollout.py",
    "scripts/qualify-context.py",
    "scripts/qualify-solver.py",
    "scripts/qualify-lifecycle.py",
)


def object_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def file_digest(path: Path, *, sparse: bool = False) -> str:
    """Hash bounded regular bytes, or a named sparse extent representation.

    The release's 30 GiB sparse templates contain only MiB of allocated data.
    Their fingerprint includes logical size, extent offsets, and all allocated
    bytes. A different allocation layout conservatively invalidates evidence.
    """
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > 64 * 1024**3:
            raise SkillEvalError("identity input must be a bounded regular file")
        digest = hashlib.sha256()
        if sparse:
            digest.update(f"skilleval-sparse-extents-v1:{before.st_size}\n".encode())
            offset = 0
            while offset < before.st_size:
                try:
                    start = os.lseek(stream.fileno(), offset, os.SEEK_DATA)
                except OSError as error:
                    if error.errno == errno.ENXIO:
                        break
                    raise SkillEvalError("runtime template extent inspection unavailable") from None
                end = os.lseek(stream.fileno(), start, os.SEEK_HOLE)
                if not offset <= start < end <= before.st_size:
                    raise SkillEvalError("invalid runtime template extent")
                digest.update(f"{start}:{end}\n".encode())
                stream.seek(start)
                remaining = end - start
                while remaining:
                    block = stream.read(min(remaining, 1024 * 1024))
                    if not block:
                        raise SkillEvalError("identity input changed while reading")
                    digest.update(block)
                    remaining -= len(block)
                offset = end
        else:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        after = os.fstat(stream.fileno())
        if any(
            getattr(before, key) != getattr(after, key)
            for key in ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
        ):
            raise SkillEvalError("identity input changed while reading")
    return digest.hexdigest()


def runtime_identity(runtime_binary: str) -> dict[str, Any]:
    """Inventory the explicit unpacked smolvm distribution and current host.

    Only the inspected distribution layout is supported. Guest rootfs symlinks
    are hashed as link text; they are never followed into the host filesystem.
    """
    resolved = shutil.which(runtime_binary)
    if resolved is None:
        raise SkillEvalError("runtime executable unavailable")
    binary = Path(resolved).resolve()
    root = binary.parent
    if binary.name != "smolvm" or any(
        not (root / name).exists()
        for name in (
            "smolvm-bin",
            "lib",
            "agent-rootfs",
            "storage-template.ext4",
            "overlay-template.ext4",
        )
    ):
        raise SkillEvalError("identity requires the complete unpacked smolvm distribution")
    entries: dict[str, Any] = {}

    def traversal_error(_: OSError) -> None:
        raise SkillEvalError("cannot inventory complete runtime distribution")

    for directory, directories, files in os.walk(root, followlinks=False, onerror=traversal_error):
        for name in sorted([*directories, *files]):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            item: dict[str, Any] = {"mode": stat.S_IMODE(info.st_mode)}
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                if not relative.startswith("agent-rootfs/") and not path.resolve().is_relative_to(
                    root
                ):
                    raise SkillEvalError("host runtime symlink escapes distribution")
                item.update(kind="symlink", target=target)
            elif stat.S_ISDIR(info.st_mode):
                item.update(kind="directory")
            elif stat.S_ISREG(info.st_mode):
                sparse = relative in ("storage-template.ext4", "overlay-template.ext4")
                item.update(
                    kind="file",
                    size=info.st_size,
                    digest_kind="sparse-extents-v1" if sparse else "sha256",
                    sha256=file_digest(path, sparse=sparse),
                )
            else:
                raise SkillEvalError("unsupported runtime distribution entry")
            entries[relative] = item
            if len(entries) > 10_000:
                raise SkillEvalError("runtime distribution inventory limit exceeded")
    return {
        "binary": str(binary),
        "version": "1.32.3",
        "files": entries,
        "host": {
            "hostname": platform.node().split(".")[0],
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "version": platform.version(),
        },
    }


def source_identity() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    try:
        return {name: file_digest(root / name) for name in SOURCES}
    except OSError:
        raise SkillEvalError("qualification requires its complete source checkout") from None


def snapshot_identity(runtime_binary: str) -> dict[str, Any]:
    return {
        "runtime": runtime_identity(runtime_binary),
        "sources": source_identity(),
        "controller": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        },
    }


# Execution dependencies for completed, credential-free task-environment evidence.
# Host interpretation is replayed separately; model/grade/report code is not run.
ENVIRONMENT_SOURCES = (
    "src/skilleval/evidence.py",
    "src/skilleval/lifecycle.py",
    "src/skilleval/qualification.py",
    "src/skilleval/task_environment.py",
    "src/skilleval/task_transfer.py",
    "src/skilleval/task_validation_guest.py",
    "src/skilleval/native_process.py",
)
SOLVER_ENVIRONMENT_SOURCES = ("src/skilleval/solver_boundary.py",)


def environment_identity(
    captured: object, execution_profile: str, *, isolation: str = "case-v1"
) -> dict[str, Any]:
    """Project recorded execution proof, never fill missing evidence from live files."""
    if execution_profile not in {"native-root-v1", "solver-isolated-v1"}:
        raise SkillEvalError("unknown environment identity execution profile")
    if isolation not in {"case-v1", "lane-v1"}:
        raise SkillEvalError("unknown validation isolation profile")
    names = ENVIRONMENT_SOURCES + (
        SOLVER_ENVIRONMENT_SOURCES if execution_profile == "solver-isolated-v1" else ()
    )
    if isolation == "lane-v1":
        names += ("src/skilleval/lane_validation.py",)
    if not isinstance(captured, dict) or not isinstance(captured.get("runtime"), dict):
        raise SkillEvalError("missing environment identity runtime proof")
    controller = captured.get("controller")
    if (
        not isinstance(controller, dict)
        or set(controller) != {"implementation", "version"}
        or any(not isinstance(value, str) or not value for value in controller.values())
    ):
        raise SkillEvalError("missing environment identity controller proof")
    sources = captured.get("sources")
    if not isinstance(sources, dict) or any(
        not isinstance(sources.get(name), str)
        or re.fullmatch(r"[a-f0-9]{64}", sources[name]) is None
        for name in names
    ):
        raise SkillEvalError("missing or invalid environment identity source proof")
    return {
        "schema_version": 1,
        "profile": "task-environment-lanes-v1" if isolation == "lane-v1" else "task-environment-v1",
        "execution_profile": execution_profile,
        "runtime": captured["runtime"],
        "controller": controller,
        "sources": {name: sources[name] for name in names},
    }
