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
import signal
import stat
import subprocess
import sys
import tarfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any


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
) -> dict[str, Any]:
    """Stream both pipes to files; terminate the process group on every exit."""
    with (
        (evidence / "events.jsonl").open("wb") as stdout,
        (evidence / "stderr.log").open("wb") as stderr,
        prompt.open("rb") as stdin,
    ):
        process = subprocess.Popen(
            argv,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            cwd=work,
            env=environment,
            start_new_session=True,
        )
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            _stop(process)
    return {"returncode": process.returncode, "timed_out": timed_out}


def _archive(root: Path, work: Path, maximum: int, count: int) -> dict[str, Any]:
    """Keep regular bytes and link metadata; never follow task-created links."""
    entries: list[tuple[str, Path]] = []
    links: list[dict[str, str]] = []
    total = 0
    for prefix, directory in (
        ("evidence", root / "evidence"),
        ("task", work),
        ("home", root / "home"),
        ("codex-home", root / "codex-home"),
    ):
        for current, directories, files in os.walk(directory, followlinks=False):
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
        str(evidence / "final.txt"),
        "-",
    ]
    relay: subprocess.Popen[bytes] | None = None
    result: dict[str, Any] = {"returncode": None, "timed_out": False, "argv": argv}
    result["environment"] = environment
    result["cwd"] = str(work)
    result["config_sha256"] = hashlib.sha256((codex_home / "config.toml").read_bytes()).hexdigest()
    try:
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
                    "python3",
                    "/opt/skilleval/openbench_gateway.py",
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
        result.update(
            _execute_native(
                argv,
                environment,
                root / "prompt.txt",
                work,
                evidence,
                settings["native_timeout_seconds"],
            )
        )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result["error"] = type(error).__name__
    finally:
        if relay is not None:
            _stop(relay)
        (evidence / "process.json").write_text(json.dumps(result, sort_keys=True))
        try:
            collection = _archive(
                root, work, settings["max_artifact_bytes"], settings["max_artifact_files"]
            )
        except (OSError, ValueError, tarfile.TarError):
            collection = {"status": "failed"}
        (root / "collection.json").write_text(json.dumps(collection, sort_keys=True))
    return 0 if collection["status"] == "complete" else 24


if __name__ == "__main__":
    raise SystemExit(run(Path(sys.argv[1]), Path(sys.argv[2])))
