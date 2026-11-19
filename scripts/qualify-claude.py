#!/usr/bin/env python3
"""Explicit native-subscription/VM control. Calls Claude only when invoked.

A successful receipt covers this host, binary, source and image. It does not
establish semantic grader reliability or arbitrary host-policy equivalence.
"""

from __future__ import annotations

import argparse
import json
import tarfile
import uuid
from pathlib import Path

from skilleval import evidence, solver_boundary
from skilleval.runner import run_native


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--image-archive", type=Path, required=True)
    parser.add_argument("--image-sha256", required=True)
    parser.add_argument("--claude-binary", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()
    root = args.output.absolute()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    source = root / "source"
    source.mkdir(mode=0o700)
    guest_token, host_token = uuid.uuid4().hex, uuid.uuid4().hex
    host_marker = root / "host-only.txt"
    host_marker.write_text(host_token)
    host_marker.chmod(0o600)
    (source / "marker.txt").write_text(guest_token)
    control = """import errno,json,os,pathlib,socket
observed = {
    "uid": os.getuid(),
    "cwd": str(pathlib.Path.cwd()),
    "guest_marker": pathlib.Path("marker.txt").read_text(),
    "host_marker_visible": pathlib.Path(HOST_MARKER).exists(),
    "controller_visible": pathlib.Path("/var/tmp/native/claude_vm_guest.py").exists(),
}
local = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
observed["ordinary_socket_available"] = True
local.settimeout(1)
try:
    local.connect(("1.1.1.1",443))
    observed["external_network_connected"] = True
except OSError:
    observed["external_network_connected"] = False
finally:
    local.close()
try:
    channel = socket.socket(socket.AF_VSOCK,socket.SOCK_STREAM)
    channel.close()
    observed["vsock_denied"] = False
except OSError as error:
    observed["vsock_denied"] = error.errno == errno.EPERM
pathlib.Path("observed.json").write_text(json.dumps(observed,sort_keys=True))
print(json.dumps(observed,sort_keys=True))
""".replace("HOST_MARKER", repr(str(host_marker)))
    (source / "control.py").write_text(control)
    captured = evidence.snapshot(source, root / "input")
    prompt = (
        "Inspect the supplied control.py and run python3 control.py using the VM tool. "
        "Then use a second VM tool call to read observed.json. Preserve the supplied files. "
        "Return exactly one JSON object summarizing whether the control ran and any failures. "
        "Do not guess file contents or ask a question."
    )
    run = run_native(
        root / "input",
        captured["manifest_sha256"],
        prompt,
        root / "native",
        runtime=args.runtime.resolve(),
        image=args.image_archive.absolute(),
        image_sha256=args.image_sha256,
        mode="judge",
        investigation=True,
        judge_profile="claude-opus",
        claude_binary=args.claude_binary,
        native_timeout_seconds=args.timeout_seconds,
    )
    checks = {
        "native_completed": run["status"] == "completed",
        "native_subscription": run.get("authentication", {}).get("authMethod") == "claude.ai",
        "observed_init_admitted": run.get("context", {}).get("init_admitted") is True,
        "cleanup_complete": run.get("cleanup_complete") is True,
        "host_context_clean": run.get("host_context_clean") is True,
        "original_unchanged": run.get("original_input_unchanged") is True,
        "host_marker_unchanged": host_marker.read_text() == host_token,
    }
    observed = None
    boundaries = []
    archive = root / "native/artifacts.tar"
    if archive.is_file() and run.get("artifacts"):
        with tarfile.open(archive, "r:") as retained:
            for member in retained:
                if member.name == "task/observed.json" or (
                    member.name.startswith("evidence/commands/")
                    and member.name.endswith("/boundary.json")
                ):
                    stream = retained.extractfile(member)
                    if stream is None:
                        continue
                    value = json.loads(stream.read())
                    if member.name == "task/observed.json":
                        observed = value
                    else:
                        boundaries.append(value)
    checks["repeated_vm_commands_reaped"] = len(boundaries) >= 2 and all(
        solver_boundary.valid_receipt(row) for row in boundaries
    )
    checks["effective_vm_authority"] = observed == {
        "uid": 61000,
        "cwd": "/workspace/task",
        "guest_marker": guest_token,
        "host_marker_visible": False,
        "controller_visible": False,
        "ordinary_socket_available": True,
        "external_network_connected": False,
        "vsock_denied": True,
    }
    native_stream = root / "native/claude-events.jsonl"
    checks["host_marker_not_in_stream"] = (
        native_stream.is_file() and host_token.encode() not in native_stream.read_bytes()
    )
    summary = {
        "schema_version": 1,
        "kind": "claude-subscription-vm-control",
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "observed": observed,
        "usage": run.get("usage_accounting"),
        "timings": run.get("timings"),
        "native": str(root / "native"),
        "scope": "one native subscription client and VM invocation; not semantic reliability",
    }
    (root / "qualification.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
