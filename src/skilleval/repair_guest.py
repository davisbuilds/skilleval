"""Root guest supervisor for offline repair observations; never run on the host."""

import ctypes
import json
import os
import resource
import signal
import stat
import subprocess
import sys
import tempfile
from contextlib import suppress
from functools import partial
from pathlib import Path

LIMIT = 128 * 1024
# Parallel fixed image interface in solver_boundary.py; this staged supervisor
# cannot import the host package. Neither path is supplied by task/worker data.
NODE_DEPENDENCIES = Path("/usr/local/share/task-dependencies/node_modules")
NODE_DEPENDENCIES_TARGET = Path("/workspace/node_modules")


def _real_dependency_directory(path: Path, *, optional: bool = False) -> bool:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("dependency projection requires absolute directory paths")
    current = Path("/")
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if optional:
                return False
            raise ValueError("dependency projection directory is missing") from None
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("dependency projection directory has a symlink ancestor")
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("dependency projection requires real directories")
        if info.st_uid not in {0, os.getuid()} or (
            info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX
        ):
            raise ValueError("dependency projection directory ancestry is writable or untrusted")
    return True


def project_image_dependencies(work: Path) -> bool:
    """Project only the fixed image dependency tree, outside candidate source."""
    if not _real_dependency_directory(NODE_DEPENDENCIES, optional=True):
        return False
    if work.is_relative_to(NODE_DEPENDENCIES_TARGET) or NODE_DEPENDENCIES_TARGET.is_relative_to(
        work
    ):
        raise ValueError("dependency alias must not overlap task source")
    _real_dependency_directory(work)
    _real_dependency_directory(NODE_DEPENDENCIES_TARGET.parent)
    try:
        existing = NODE_DEPENDENCIES_TARGET.lstat()
    except FileNotFoundError:
        NODE_DEPENDENCIES_TARGET.symlink_to(NODE_DEPENDENCIES, target_is_directory=True)
        return True
    if (
        not stat.S_ISLNK(existing.st_mode)
        or NODE_DEPENDENCIES_TARGET.readlink() != NODE_DEPENDENCIES
    ):
        raise ValueError("unexpected existing image dependency alias")
    if existing.st_uid not in {0, os.getuid()}:
        raise ValueError("untrusted existing image dependency alias owner")
    return True


def resource_limits(profile: str) -> dict[str, int]:
    if profile not in {"python-v1", "node-v1"}:
        raise ValueError("unsupported offline observer resource profile")
    node = profile == "node-v1"
    return {
        "address_space_bytes": 2 * 1024**3 if node else 512 * 1024**2,
        "file_size_bytes": 8 * 1024**2 if node else LIMIT,
        "open_files": 256 if node else 64,
        "processes": 32,
        "core_bytes": 0,
        "output_bytes_per_stream": LIMIT,
    }


def restrict(profile: str = "python-v1") -> None:
    limits = resource_limits(profile)
    for key, name in (
        (resource.RLIMIT_FSIZE, "file_size_bytes"),
        (resource.RLIMIT_AS, "address_space_bytes"),
        (resource.RLIMIT_NOFILE, "open_files"),
        (resource.RLIMIT_NPROC, "processes"),
        (resource.RLIMIT_CORE, "core_bytes"),
    ):
        resource.setrlimit(key, (limits[name], limits[name]))
    if ctypes.CDLL(None, use_errno=True).prctl(38, 1, 0, 0, 0) != 0:
        raise OSError("cannot disable privilege gain")


def _read_output(path: Path) -> tuple[bytes, bool]:
    with path.open("rb") as stream:
        raw = stream.read(LIMIT + 1)
    return raw[:LIMIT], len(raw) >= LIMIT


def run(root: Path, work: Path) -> None:
    if os.getuid() != 0:
        raise ValueError("offline supervisor requires guest root")
    settings = json.loads((root / "requests.json").read_text())
    profile = settings.get("observer_resource_profile", "python-v1")
    limits = resource_limits(profile)
    program = (root / "worker.py").read_text()
    project_image_dependencies(work)
    # Candidate source and checker assets belong to root. Only this separate
    # scratch directory is writable by the observation process.
    for current, _directories, files in os.walk(work):
        Path(current).chmod(0o755)
        for name in files:
            item = Path(current) / name
            item.chmod(0o555 if item.stat().st_mode & 0o111 else 0o444)
    root.chmod(0o700)
    scratch = Path(tempfile.mkdtemp(prefix="repair-worker-"))
    os.chown(scratch, 65534, 65534)
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(scratch),
        "TMPDIR": str(scratch),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    with (root / "stdout.bin").open("wb") as stdout, (root / "stderr.bin").open("wb") as stderr:
        process = subprocess.Popen(
            ["python3", "-I", "-B", "-c", program, str(work)],
            stdin=subprocess.PIPE,
            stdout=stdout,
            stderr=stderr,
            cwd=scratch,
            env=env,
            user=65534,
            group=65534,
            extra_groups=[],
            umask=0o077,
            start_new_session=True,
            preexec_fn=partial(restrict, profile),
        )
        timed_out = False
        try:
            process.communicate(
                json.dumps({"requests": settings["requests"]}).encode(),
                timeout=settings["timeout_seconds"],
            )
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
    stdout_raw, stdout_limit = _read_output(root / "stdout.bin")
    stderr_raw, stderr_limit = _read_output(root / "stderr.bin")
    # The unprivileged child's text is encoded inside this supervisor envelope.
    # Neither stdout text nor a saved verdict can overwrite host comparisons.
    print(
        json.dumps(
            {
                "schema_version": 1,
                "returncode": process.returncode,
                "timed_out": timed_out,
                "output_limit_reached": stdout_limit or stderr_limit,
                "stdout": stdout_raw.decode("utf-8", errors="replace"),
                "stderr": stderr_raw.decode("utf-8", errors="replace"),
                "worker_uid": 65534,
                "expected_answers_supplied": False,
                **(
                    {"observer_resources": {"profile": profile, **limits}}
                    if profile != "python-v1"
                    else {}
                ),
            }
        )
    )


if __name__ == "__main__":
    run(Path(sys.argv[1]), Path(sys.argv[2]))
