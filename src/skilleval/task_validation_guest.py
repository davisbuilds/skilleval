"""Offline task-validation supervisor. Execute only inside the guest VM."""

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
from typing import Any

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


def solver_process(
    spec: dict[str, Any], work: Path
) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    """Use the native launcher and pipe collector with a fresh home for each lane."""
    if __package__:
        from skilleval import guest_runner
    else:
        # The supervisor is invoked with -I. Import only the explicitly staged,
        # trusted controller modules, which the solver's root does not expose.
        sys.path.insert(0, str(Path(__file__).parent))
        import guest_runner  # type: ignore[no-redef,import-not-found]

    controller = Path(__file__).parent
    root = Path(tempfile.mkdtemp(prefix="lane-", dir=controller))
    for name in ("home", "codex-home", "evidence"):
        (root / name).mkdir(mode=0o700)
    (root / "models.json").write_text("{}\n")
    (root / "models.json").chmod(0o644)
    prompt = root / "prompt.txt"
    prompt.write_bytes(b"")
    env = {
        "HOME": str(root / "home"),
        "CODEX_HOME": str(root / "codex-home"),
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    # Apply the task-file limit only to the launcher and its descendants. The
    # controller must still be able to record JSON-escaped bounded logs.
    launch = (
        "import os,resource,sys; "
        f"resource.setrlimit(resource.RLIMIT_FSIZE, ({LIMIT},{LIMIT})); "
        "resource.setrlimit(resource.RLIMIT_CORE, (0,0)); "
        "os.execv(sys.executable, [sys.executable, *sys.argv[1:]])"
    )
    result = guest_runner._execute_native(
        [
            "python3",
            "-I",
            "-c",
            launch,
            str(controller / "solver_boundary.py"),
            str(root),
            str(work),
            "--",
            *spec["argv"],
        ],
        env,
        prompt,
        work,
        root / "evidence",
        spec["timeout_seconds"],
        stream_limit_bytes=LOG_LIMIT,
    )
    try:
        boundary = json.loads((root / "boundary.json").read_bytes())
    except (OSError, ValueError) as error:
        boundary = None
        result["boundary_error"] = type(error).__name__
    result.update(
        boundary=boundary,
        stdout=(root / "evidence/events.jsonl").read_bytes().decode(errors="replace"),
        stderr=(root / "evidence/stderr.log").read_bytes().decode(errors="replace"),
    )
    profile = (
        {name: boundary.get(name) for name in ("uid", "gid", "groups")}
        if isinstance(boundary, dict)
        else {}
    )
    profile["cwd"] = str(work)
    return result, env, profile


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
        raise ValueError("validation supervisor requires guest root")
    before = inventory(work)
    if spec.get("profile", "native-root-v1") == "solver-isolated-v1":
        result, env, profile = solver_process(spec, work)
        boundary = result["boundary"]
        reaped = isinstance(boundary, dict) and boundary.get("namespace_reaped") is True
        # A missing/failed handshake must still yield the bounded logs and
        # receipt. Never inspect mutable task state until teardown is observed.
        after = inventory(work) if reaped else None
        result.update(
            retained_log_limit_bytes=LOG_LIMIT,
            profile=profile,
            environment=env,
            argv=spec["argv"],
            source_changes=sorted(
                n
                for n in before.keys() | after.keys()
                if n != ".git/index" and before.get(n) != after.get(n)
            )
            if after is not None
            else None,
            metadata_changes=[".git/index"]
            if after is not None and before.get(".git/index") != after.get(".git/index")
            else [],
            file_size_limit_bytes=LIMIT,
        )
        Path(spec["report_path"]).write_text(json.dumps(result))
        print(json.dumps({"status": "recorded"}))
        return
    if spec.get("profile", "native-root-v1") != "native-root-v1":
        raise ValueError("unsupported validation execution profile")
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
