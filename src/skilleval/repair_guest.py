"""Root guest supervisor for offline repair observations; never run on the host."""

import ctypes
import json
import os
import resource
import signal
import subprocess
import sys
import tempfile
from contextlib import suppress
from pathlib import Path

LIMIT = 128 * 1024


def restrict() -> None:
    resource.setrlimit(resource.RLIMIT_FSIZE, (LIMIT, LIMIT))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if ctypes.CDLL(None, use_errno=True).prctl(38, 1, 0, 0, 0) != 0:
        raise OSError("cannot disable privilege gain")


def run(root: Path, work: Path) -> None:
    if os.getuid() != 0:
        raise ValueError("offline supervisor requires guest root")
    settings = json.loads((root / "requests.json").read_text())
    program = (root / "worker.py").read_text()
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
            preexec_fn=restrict,
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
    stdout_raw = (root / "stdout.bin").read_bytes()
    stderr_raw = (root / "stderr.bin").read_bytes()
    # The unprivileged child's text is encoded inside this supervisor envelope.
    # Neither stdout text nor a saved verdict can overwrite host comparisons.
    print(
        json.dumps(
            {
                "schema_version": 1,
                "returncode": process.returncode,
                "timed_out": timed_out,
                "output_limit_reached": len(stdout_raw) >= LIMIT or len(stderr_raw) >= LIMIT,
                "stdout": stdout_raw.decode("utf-8", errors="replace"),
                "stderr": stderr_raw.decode("utf-8", errors="replace"),
                "worker_uid": 65534,
                "expected_answers_supplied": False,
            }
        )
    )


if __name__ == "__main__":
    run(Path(sys.argv[1]), Path(sys.argv[2]))
