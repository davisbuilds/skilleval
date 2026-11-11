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
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

if __package__:
    from skilleval import app_server, context_guard, native_process, solver_boundary
else:
    import app_server  # type: ignore[no-redef,import-not-found]
    import context_guard  # type: ignore[no-redef,import-not-found]
    import native_process  # type: ignore[no-redef,import-not-found]
    import solver_boundary  # type: ignore[no-redef,import-not-found]


NATIVE_PAX_PATH_BYTES = native_process.NATIVE_PAX_PATH_BYTES
_pax_path_size = native_process._pax_path_size
_stop = native_process._stop

# Preserve the vendored file verbatim; presentation is owned by this launcher.
RELAY_PROGRAM = """import runpy, sys
gateway = runpy.run_path('/opt/skilleval/openbench_gateway.py')
gateway['_Handler'].server_version = 'LocalGateway'
raise SystemExit(gateway['main'](sys.argv[1:]))
"""
# The inherited cwd is the untrusted task. Exclude it from privileged imports.
RELAY_PREFIX = ("python3", "-I", "-c", RELAY_PROGRAM)


def _execute_native(
    argv: list[str],
    environment: dict[str, str],
    prompt: Path,
    work: Path,
    evidence: Path,
    timeout: int,
    *,
    stream_limit_bytes: int | None = None,
    protocol: app_server.Session | None = None,
) -> dict[str, Any]:
    """Execute the shared process collector and project native protocol evidence."""
    result = native_process._execute_native(
        argv,
        environment,
        prompt,
        work,
        evidence,
        timeout,
        stream_limit_bytes=stream_limit_bytes,
        protocol=protocol,
        stdout_name="app-server-rpc.jsonl" if protocol else "events.jsonl",
        input_name="app-server-input.jsonl" if protocol else None,
    )
    if protocol:
        protocol_error = result.pop("protocol_error")
        (evidence / "events.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in protocol.events)
        )
        complete = protocol.complete and protocol_error is None and not result["timed_out"]
        (evidence / "final.txt").write_text(protocol.final_text if complete else "")
        result["protocol"] = {
            "transport": "stdio-jsonrpc",
            "harness": "codex-app-server-jsonrpc-v2",
            "adapter": "app-server-v2-to-canonical-v1",
            "capture_policy": app_server.CAPTURE_POLICY,
            "thread_id": protocol.thread_id,
            "turn_id": protocol.turn_id,
            "rollout_path": protocol.rollout_path,
            "thread_ephemeral_requested": False,
            "thread_persistence_confirmed": protocol.persistence_confirmed,
            "reasoning_summary_requested": "auto",
            "reasoning": protocol.reasoning_coverage,
            "complete": complete,
            "permissions_confirmed": protocol.permissions_confirmed,
            "error": protocol_error,
            "errors": protocol.errors,
        }
    return result


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
                    if _pax_path_size(relative) > NATIVE_PAX_PATH_BYTES:
                        raise ValueError("artifact path exceeds native PAX metadata limit")
                    entries.append((relative, path))
                    total += observed_info.st_size
                elif not stat.S_ISDIR(observed_info.st_mode):
                    links.append({"path": relative, "kind": "special"})
                if len(entries) + len(links) >= count or total > maximum:
                    return {"status": "limit-exceeded", "bytes": total, "files": len(entries)}
    metadata = json.dumps(links, sort_keys=True).encode()
    # Preserve the existing short-name allowance; larger bounded PAX headers
    # also need their actual header bytes plus worst-case file block padding.
    headers = sum(
        max(4096, len(tarfile.TarInfo(name).tobuf(format=tarfile.PAX_FORMAT)) + 511)
        for name, _path in entries
    )
    if total + len(metadata) + headers + 4096 + 10240 > maximum:
        return {"status": "limit-exceeded", "bytes": total, "files": len(entries)}
    archive = root / "artifacts.tar"
    with tarfile.open(archive, "w", format=tarfile.PAX_FORMAT) as target:
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
    argv = ["codex", "app-server"]
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
                stream_limit_bytes=maximum,
                protocol=app_server.Session((root / "prompt.txt").read_text(), str(work)),
            )
        )
        boundary = json.loads((root / "boundary.json").read_bytes())
        result["boundary"] = boundary
        safe_to_collect = boundary.get("namespace_reaped") is True
        if not solver_boundary.valid_receipt(boundary):
            raise ValueError("solver boundary failed")
        result["launcher_elapsed_seconds"] = result["agent_elapsed_seconds"]
        result["agent_elapsed_seconds"] = boundary["native_elapsed_seconds"]
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
        except (OSError, ValueError, tarfile.TarError) as error:
            collection = {
                "status": "failed",
                "error_type": type(error).__name__,
                "error": str(error)[:1024],
            }
        (root / "collection.json").write_text(json.dumps(collection, sort_keys=True))
    return 0 if collection["status"] == "complete" else 24


if __name__ == "__main__":
    raise SystemExit(run(Path(sys.argv[1]), Path(sys.argv[2])))
