"""Offline root-profile validation supervisor. Execute only inside the guest VM."""

import hashlib
import json
import os
import resource
import signal
import subprocess
import sys
import tempfile
from contextlib import suppress
from pathlib import Path

LIMIT = 8 * 1024 * 1024
LOG_LIMIT = 1024 * 1024


def inventory(root: Path) -> dict[str, str]:
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in {"__pycache__", ".pytest_cache"}]
        for name in files + [d for d in dirs if (Path(directory) / d).is_symlink()]:
            path = Path(directory) / name
            key = path.relative_to(root).as_posix()
            if path.is_symlink() or not path.is_file():
                result[key] = "nonregular"
            else:
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                result[key] = digest.hexdigest() + (":x" if path.stat().st_mode & 0o111 else "")
    return result


def restrict() -> None:
    resource.setrlimit(resource.RLIMIT_FSIZE, (LIMIT, LIMIT))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main() -> None:
    spec = json.loads(sys.argv[1])
    work = Path("/workspace/task")
    env = {
        "HOME": "/var/tmp/native/home",
        "CODEX_HOME": "/var/tmp/native/codex-home",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name in ("HOME", "CODEX_HOME"):
        Path(env[name]).mkdir(mode=0o700, exist_ok=True)
    os.setgroups([])
    if os.getuid() != 0 or os.getgid() != 0:
        raise ValueError("native-root-v1 requires root")
    before = inventory(work)
    with tempfile.TemporaryDirectory(prefix="validation-") as temporary:
        root = Path(temporary)
        with (root / "out").open("wb") as out, (root / "err").open("wb") as err:
            process = subprocess.Popen(
                spec["argv"],
                cwd=work,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                start_new_session=True,
                preexec_fn=restrict,
            )
            timed_out = False
            try:
                process.wait(timeout=spec["timeout_seconds"])
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        with (root / "out").open("rb") as stream:
            stdout = stream.read(LOG_LIMIT + 1)
        with (root / "err").open("rb") as stream:
            stderr = stream.read(LOG_LIMIT + 1)
        after = inventory(work)
        Path(spec["report_path"]).write_text(
            json.dumps(
                {
                    "returncode": process.returncode,
                    "timed_out": timed_out,
                    "stdout": stdout[:LOG_LIMIT].decode(errors="replace"),
                    "stderr": stderr[:LOG_LIMIT].decode(errors="replace"),
                    "output_limit_reached": len(stdout) > LOG_LIMIT or len(stderr) > LOG_LIMIT,
                    "retained_log_limit_bytes": LOG_LIMIT,
                    "profile": {
                        "uid": os.getuid(),
                        "gid": os.getgid(),
                        "groups": os.getgroups(),
                        "cwd": str(work),
                    },
                    "environment": env,
                    "argv": spec["argv"],
                    "source_changes": sorted(
                        n
                        for n in before.keys() | after.keys()
                        if n != ".git/index" and before.get(n) != after.get(n)
                    ),
                    "metadata_changes": [".git/index"]
                    if before.get(".git/index") != after.get(".git/index")
                    else [],
                    "file_size_limit_bytes": LIMIT,
                }
            )
        )
        print(json.dumps({"status": "recorded"}))


if __name__ == "__main__":
    main()
