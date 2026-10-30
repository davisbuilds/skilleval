"""Trusted controller copied into a disposable guest; never runs imported code on host.

All output from this program remains guest-authored evidence. The host validates
and stores its archive without extracting it. This is not an isolation probe.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
import resource
import selectors
import signal
import stat
import subprocess
import sys
import tarfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

if __package__:
    from skilleval import context_guard, solver_boundary
else:
    import context_guard  # type: ignore[no-redef,import-not-found]
    import solver_boundary  # type: ignore[no-redef,import-not-found]


# Preserve the vendored file verbatim; presentation is owned by this launcher.
RELAY_PROGRAM = """import runpy, sys
gateway = runpy.run_path('/opt/skilleval/openbench_gateway.py')
gateway['_Handler'].server_version = 'LocalGateway'
raise SystemExit(gateway['main'](sys.argv[1:]))
"""
# The inherited cwd is the untrusted task. Exclude it from privileged imports.
RELAY_PREFIX = ("python3", "-I", "-c", RELAY_PROGRAM)


def _stop(process: subprocess.Popen[bytes]) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=3)
    # The leader may exit on TERM while its descendants ignore it. Always kill
    # the original group before collecting mutable guest artifacts.
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=3)


def _execute_native(
    argv: list[str],
    environment: dict[str, str],
    prompt: Path,
    work: Path,
    evidence: Path,
    timeout: int,
    *,
    stream_limit_bytes: int | None = None,
) -> dict[str, Any]:
    """Keep controller file descriptors outside the child; stream bounded pipes.

    Namespace-wide teardown is the boundary launcher's responsibility. The
    process-group fallback here also serves the trusted relay and host tests.
    """
    with (
        (evidence / "events.jsonl").open("wb") as stdout,
        (evidence / "stderr.log").open("wb") as stderr,
        selectors.DefaultSelector() as selector,
    ):
        pending = memoryview(prompt.read_bytes())
        started = time.monotonic()
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=work,
            env=environment,
            start_new_session=True,
            close_fds=True,
        )
        assert process.stdin and process.stdout and process.stderr
        for stream in (process.stdin, process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
        selector.register(process.stdin, selectors.EVENT_WRITE, None)
        selector.register(process.stdout, selectors.EVENT_READ, stdout)
        selector.register(process.stderr, selectors.EVENT_READ, stderr)
        timed_out = False
        truncated = False
        drain_incomplete = False
        stopped_at = None
        try:
            while selector.get_map():
                now = time.monotonic()
                if stopped_at is None and (process.poll() is not None or now - started >= timeout):
                    timed_out = process.poll() is None
                    _stop(process)
                    stopped_at = time.monotonic()
                if stopped_at is not None and now - stopped_at > 1:
                    drain_incomplete = True
                    break
                for key, _ in selector.select(timeout=0.05):
                    if key.data is None:
                        if pending:
                            try:
                                pending = pending[os.write(key.fd, pending[:4096]) :]
                            except BrokenPipeError:
                                pending = memoryview(b"")
                        if not pending:
                            selector.unregister(key.fileobj)
                            process.stdin.close()
                        continue
                    block = os.read(key.fd, 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    output = key.data
                    if stream_limit_bytes is not None:
                        available = max(0, stream_limit_bytes - output.tell())
                        truncated |= len(block) > available
                        block = block[:available]
                    output.write(block)
            # A process can close all streams before it exits.
            if stopped_at is None:
                try:
                    process.wait(timeout=max(0.001, timeout - (time.monotonic() - started)))
                except subprocess.TimeoutExpired:
                    timed_out = True
        finally:
            _stop(process)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
    return {
        "returncode": process.returncode,
        "timed_out": timed_out,
        "agent_elapsed_seconds": time.monotonic() - started,
        "output_limit_reached": truncated,
        "pipe_drain_incomplete": drain_incomplete,
    }


def _archive(root: Path, work: Path, maximum: int, count: int) -> dict[str, Any]:
    """Keep regular bytes and link metadata; never follow task-created links."""
    entries: list[tuple[str, Path]] = []
    links: list[dict[str, str]] = []
    total = 0

    def traversal_error(error: OSError) -> None:
        raise error

    for prefix, directory in (
        ("evidence", root / "evidence"),
        ("task", work),
        ("home", root / "home"),
        ("codex-home", root / "codex-home"),
    ):
        if directory.is_symlink():
            raise ValueError("unsafe collection root")
        for current, directories, files in os.walk(
            directory, followlinks=False, onerror=traversal_error
        ):
            for name in sorted([*directories, *files]):
                path = Path(current) / name
                relative = prefix + "/" + path.relative_to(directory).as_posix()
                observed_info = path.lstat()
                if stat.S_ISLNK(observed_info.st_mode):
                    links.append({"path": relative, "kind": "symlink", "target": os.readlink(path)})
                elif stat.S_ISREG(observed_info.st_mode):
                    entries.append((relative, path))
                    total += observed_info.st_size
                elif not stat.S_ISDIR(observed_info.st_mode):
                    links.append({"path": relative, "kind": "special"})
                if len(entries) + len(links) >= count or total > maximum:
                    return {"status": "limit-exceeded", "bytes": total, "files": len(entries)}
    metadata = json.dumps(links, sort_keys=True).encode()
    # Conservative PAX/name allowance plus tar block padding before opening output.
    if total + len(metadata) + (len(entries) + 1) * 4096 + 10240 > maximum:
        return {"status": "limit-exceeded", "bytes": total, "files": len(entries)}
    archive = root / "artifacts.tar"
    with tarfile.open(archive, "w", format=tarfile.USTAR_FORMAT) as target:
        for name, path in entries:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as source:
                before = os.fstat(source.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError("artifact changed type")
                info = tarfile.TarInfo(name)
                info.size = before.st_size
                info.mode = 0o755 if before.st_mode & 0o111 else 0o644
                target.addfile(info, source)
                after = os.fstat(source.fileno())
                if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ):
                    raise ValueError("artifact changed while collecting")
        info = tarfile.TarInfo("evidence/filesystem-links.json")
        info.size, info.mode = len(metadata), 0o600
        target.addfile(info, io.BytesIO(metadata))
    if archive.stat().st_size > maximum:
        archive.unlink()
        return {"status": "limit-exceeded"}
    return {"status": "complete", "bytes": archive.stat().st_size, "files": len(entries) + 1}


def run(root: Path, work: Path) -> int:
    """Run one native CLI process with streamed logs and a hard process deadline."""
    settings = json.loads((root / "settings.json").read_text())
    evidence = root / "evidence"
    evidence.mkdir(mode=0o700)
    home, codex_home = root / "home", root / "codex-home"
    home.mkdir(mode=0o700)
    codex_home.mkdir(mode=0o700, exist_ok=True)
    codex_home.chmod(0o700)
    (codex_home / "config.toml").write_bytes((root / "config.toml").read_bytes())
    environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "CODEX_HOME": str(codex_home),
        "LANG": "C.UTF-8",
    }
    argv = [
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--color",
        "never",
        "--output-last-message",
        str(home / "final.txt"),
        "-",
    ]
    relay: subprocess.Popen[bytes] | None = None
    boundary_started = False
    safe_to_collect = True
    result: dict[str, Any] = {"returncode": None, "timed_out": False, "argv": argv}
    result["environment"] = environment
    result["cwd"] = str(work)
    result["config_sha256"] = hashlib.sha256((codex_home / "config.toml").read_bytes()).hexdigest()
    for name in ("events.jsonl", "stderr.log", "final.txt"):
        (evidence / name).touch(exist_ok=False)
    try:
        # Synthetic negative control is never available on the authenticated route.
        if settings.get("context_control") == "dirty-system":
            contaminated = Path("/etc/codex")
            contaminated.mkdir(exist_ok=True)
            (contaminated / "hooks.json").write_text('{"hooks": {}}')
        result["context_preflight"] = context_guard.preflight(
            root, work, settings["expected_codex_home"]
        )
        version = subprocess.run(
            ["codex", "--version"], capture_output=True, env=environment, timeout=15, check=False
        )
        result["codex_version"] = version.stdout.decode("utf-8", errors="replace").strip()
        result["toolchain_sha256"] = {
            name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
            for name in (
                "/usr/local/bin/codex",
                "/usr/local/bin/codex-code-mode-host",
                "/opt/skilleval/openbench_gateway.py",
            )
        }
        with (
            (evidence / "relay-stdout.jsonl").open("wb") as stdout,
            (evidence / "relay-stderr.log").open("wb") as stderr,
        ):
            relay = subprocess.Popen(
                [
                    *RELAY_PREFIX,
                    "relay",
                    "--socket",
                    "/run/inference.sock",
                    "--port",
                    "8765",
                    "--max-body-bytes",
                    str(settings["max_body_bytes"]),
                    "--timeout-seconds",
                    str(settings["request_timeout_seconds"]),
                ],
                stdout=stdout,
                stderr=stderr,
                env=environment,
                start_new_session=True,
            )
        ready = False
        for _ in range(100):
            try:
                connection = http.client.HTTPConnection("127.0.0.1", 8765, timeout=1)
                connection.request("GET", "/responses")
                response = connection.getresponse()
                ready = response.status == 405
                response.read()
                connection.close()
                if ready:
                    break
            except OSError:
                pass
            time.sleep(0.05)
        result["relay_ready"] = ready
        if not ready:
            raise ValueError("guest relay unavailable")
        # Apply to the child and descendants; logs are streamed, not buffered in RAM.
        maximum = settings["max_artifact_bytes"]
        resource.setrlimit(resource.RLIMIT_FSIZE, (maximum, maximum))
        boundary_started = True
        safe_to_collect = False
        result.update(
            _execute_native(
                ["python3", str(root / "solver_boundary.py"), str(root), str(work), "--", *argv],
                environment,
                root / "prompt.txt",
                work,
                evidence,
                settings["native_timeout_seconds"],
            )
        )
        boundary = json.loads((root / "boundary.json").read_bytes())
        result["boundary"] = boundary
        safe_to_collect = boundary.get("namespace_reaped") is True
        if not solver_boundary.valid_receipt(boundary):
            raise ValueError("solver boundary failed")
        result["launcher_elapsed_seconds"] = result["agent_elapsed_seconds"]
        result["agent_elapsed_seconds"] = boundary["native_elapsed_seconds"]
        # The solver never holds a controller evidence FD. Read its final result
        # only after the kernel has terminated every member of its PID namespace.
        final_path = home / "final.txt"
        if final_path.exists() or final_path.is_symlink():
            descriptor = os.open(final_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
                    raise ValueError("invalid native final output")
                (evidence / "final.txt").write_bytes(source.read(maximum + 1))
        result["context_skills_after"] = context_guard.inventory(codex_home / "skills")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result["error"] = type(error).__name__
        result["context_error"] = str(error)[:1024]
    finally:
        if relay is not None:
            _stop(relay)
        (evidence / "process.json").write_text(json.dumps(result, sort_keys=True))
        try:
            if boundary_started and not safe_to_collect:
                raise ValueError("solver namespace termination unconfirmed")
            collection = _archive(
                root, work, settings["max_artifact_bytes"], settings["max_artifact_files"]
            )
        except (OSError, ValueError, tarfile.TarError):
            collection = {"status": "failed"}
        (root / "collection.json").write_text(json.dumps(collection, sort_keys=True))
    return 0 if collection["status"] == "complete" else 24


if __name__ == "__main__":
    raise SystemExit(run(Path(sys.argv[1]), Path(sys.argv[2])))
