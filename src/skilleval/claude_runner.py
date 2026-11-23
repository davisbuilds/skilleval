"""Native subscription Claude on the host; every investigation command stays in a VM.

The trusted native client owns authentication. No credential is read by Skilleval,
forwarded by a broker, or mounted in the guest. This is a distinct judge profile,
not a claim of equivalence with the Codex guest-native execution route.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
import tarfile
import tempfile
import time
from collections import Counter
from contextlib import suppress
from pathlib import Path
from typing import Any

from skilleval import evidence, native_process
from skilleval.budgets import native_deadline
from skilleval.errors import SkillEvalError
from skilleval.harnesses.claude import CLAUDE_MODEL, claude_version_identity, parse_claude_jsonl
from skilleval.identity import file_digest
from skilleval.lifecycle import Lifecycle, LifecycleInterruptedError
from skilleval.qualification import _invoke
from skilleval.task_transfer import (
    GUEST_ROOT,
    GUEST_TOOLING,
    GUEST_WORK,
    _snapshot_files,
    _tar,
    preparation_argv,
)

PROFILE = "host-claude-subscription-vm-tools-v1"


def _validate_tool_ledger(
    ledger: list[dict[str, Any]], canonical: list[dict[str, Any]]
) -> dict[str, Any]:
    """Reconcile actual native tool calls with complete trusted executor records."""
    pending: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    observed: Counter[tuple[str, str]] = Counter()
    started = completed = refused = 0

    def arguments(parameters: Any) -> str:
        if (
            not isinstance(parameters, dict)
            or parameters.get("name") != "execute"
            or not isinstance(parameters.get("arguments", {}), dict)
        ):
            raise SkillEvalError("Claude VM tool ledger has invalid command parameters")
        return json.dumps(parameters.get("arguments", {}), sort_keys=True, allow_nan=False)

    for row in ledger:
        if not isinstance(row, dict):
            raise SkillEvalError("Claude VM tool ledger contains a non-object record")
        identity, event = row.get("call_id"), row.get("event")
        if not isinstance(identity, str) or not identity or len(identity) > 128:
            raise SkillEvalError("Claude VM tool ledger has no bounded call identity")
        signature = arguments(row.get("parameters"))
        if event == "command-started":
            if identity in seen:
                raise SkillEvalError("Claude VM tool ledger repeats a command identity")
            seen.add(identity)
            pending[identity] = row
            started += 1
            continue
        result = row.get("result")
        if not isinstance(result, dict):
            raise SkillEvalError("Claude VM tool ledger has no command result")
        if event == "command-refused":
            if identity in seen or result.get("status") != "refused":
                raise SkillEvalError("Claude VM tool ledger has an invalid refusal")
            seen.add(identity)
            refused += 1
        elif event == "command-result":
            intent = pending.pop(identity, None)
            if (
                intent is None
                or arguments(intent.get("parameters")) != signature
                or result.get("status") not in {"completed", "failed"}
            ):
                raise SkillEvalError("Claude VM tool ledger has an unmatched command result")
            completed += 1
        else:
            raise SkillEvalError("Claude VM tool ledger has an unknown record")
        observed[signature, "completed" if result["status"] == "completed" else "failed"] += 1
    if pending:
        raise SkillEvalError("Claude VM tool ledger has an unfinished command intent")
    native: Counter[tuple[str, str]] = Counter()
    native_started: dict[str, str] = {}
    native_settled: set[str] = set()
    for event in canonical:
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "mcp_tool_call":
            continue
        if item.get("name") != "mcp__vm__execute" or not isinstance(item.get("id"), str):
            raise SkillEvalError("native Claude tool differs from VM ledger capability")
        identity = item["id"]
        signature = arguments({"name": "execute", "arguments": item.get("arguments")})
        if event.get("type") == "item.started":
            if identity in native_started:
                raise SkillEvalError("native Claude tool identity repeats")
            native_started[identity] = signature
        elif event.get("type") == "item.completed":
            if (
                native_started.get(identity) != signature
                or identity in native_settled
                or item.get("status") not in {"completed", "failed"}
            ):
                raise SkillEvalError("native Claude tool result has no matching invocation")
            native_settled.add(identity)
            native[signature, item["status"]] += 1
    if native_settled != set(native_started) or native != observed:
        raise SkillEvalError("Claude VM tool ledger differs from actual native tool calls")
    return {
        "status": "matched",
        "count": completed,
        "started_count": started,
        "refused_count": refused,
        "native_call_count": sum(native.values()),
        "ledger_rows": len(ledger),
    }


def _merge_artifacts(
    guest: Path,
    destination: Path,
    additions: dict[str, bytes],
    maximum: int,
    count: int,
) -> dict[str, Any]:
    """Build a new archive from validated bytes, without extracting guest paths."""
    from skilleval.runner import validate_artifacts

    original = validate_artifacts(guest, maximum, count)
    names = {row["path"] for row in original["entries"]}
    if names & set(additions):
        raise SkillEvalError("guest artifacts collide with trusted native evidence")
    if len(names) + len(additions) > count:
        raise SkillEvalError("combined Claude artifacts exceed file bound")
    projected = guest.stat().st_size + sum(len(raw) + 4096 for raw in additions.values())
    if projected > maximum:
        raise SkillEvalError("combined Claude artifacts exceed byte bound")
    with (
        tarfile.open(guest, "r:") as source,
        tarfile.open(destination, "x", format=tarfile.PAX_FORMAT) as target,
    ):
        for member in source:
            stream = source.extractfile(member)
            if stream is None:
                raise SkillEvalError("guest artifact member unavailable")
            with stream:
                target.addfile(member, stream)
        for name, raw in additions.items():
            if not name.startswith("evidence/") or "/../" in name:
                raise SkillEvalError("invalid trusted native artifact name")
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(raw), 0o600
            target.addfile(info, io.BytesIO(raw))
    destination.chmod(0o600)
    return validate_artifacts(destination, maximum, count)


def _input_changes(manifest: dict[str, Any], artifacts: dict[str, Any]) -> dict[str, Any]:
    before = {"task/" + row["path"]: row for row in manifest["files"]}
    after = {row["path"]: row for row in artifacts["entries"] if row["path"].startswith("task/")}
    return {
        "basis": "guest-authored archive compared with pinned input",
        "modified_or_removed": sorted(
            name
            for name, row in before.items()
            if name not in after
            or any(row[key] != after[name][key] for key in ("size", "sha256", "executable"))
        ),
        "created": sorted(set(after) - set(before)),
    }


def run_claude_judge(
    input_bundle: Path,
    expected_digest: str,
    prompt: str,
    output: Path,
    *,
    runtime: Path,
    image: Path,
    image_sha256: str,
    investigation: bool,
    claude_binary: Path | None = None,
    native_timeout_seconds: int | None = None,
    max_artifact_bytes: int = 64 * 1024 * 1024,
    max_artifact_files: int = 10_000,
) -> dict[str, Any]:
    from skilleval import claude_context, claude_vm
    from skilleval.runner import (
        MAX_ARTIFACT_BYTES,
        MAX_ARTIFACT_FILES,
        _copy_artifacts,
        _exclusive,
        _json,
        _publish_receipt,
        _required,
    )
    from skilleval.treatments import check_task_baseline

    started = time.monotonic()
    deadline = native_deadline("judge", native_timeout_seconds)
    if (
        not isinstance(prompt, str)
        or not prompt.strip()
        or len(prompt.encode()) > 1024 * 1024
        or type(investigation) is not bool
        or type(max_artifact_bytes) is not int
        or not 1024 * 1024 <= max_artifact_bytes <= MAX_ARTIFACT_BYTES
        or type(max_artifact_files) is not int
        or not 1 <= max_artifact_files <= MAX_ARTIFACT_FILES
    ):
        raise SkillEvalError("invalid Claude judge invocation or artifact bounds")
    input_bundle, output = evidence._paths(input_bundle, output)
    manifest, files = _snapshot_files(input_bundle, expected_digest)
    check_task_baseline(input_bundle)
    found = str(claude_binary) if claude_binary is not None else shutil.which("claude")
    if not found:
        raise SkillEvalError("existing native Claude Code executable unavailable")
    binary = Path(found).resolve(strict=True)
    binary_digest = file_digest(binary)
    session = Lifecycle.prepare(output, runtime, image, image_sha256)
    state = Path(session.ticket["runtime_state"])
    host_work: Path | None = None
    native_started: float | None = None
    collection_started: float | None = None
    parsed = parse_claude_jsonl("")
    timings: dict[str, Any] = {
        "schema_version": 1,
        "agent_seconds": None,
        "setup_seconds": None,
        "collection_seconds": None,
        "cleanup_seconds": None,
        "total_seconds": None,
        "agent_basis": "host monotonic native Claude subprocess lifetime including VM tools",
        "host_basis": "host monotonic stages through VM cleanup; excludes receipt publication",
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "skilleval-native-run",
        "mode": "judge",
        "investigation": investigation,
        "status": "partial",
        "scope": "development-only",
        "admitted": False,
        "scored_admitted": False,
        "harness": "claude-code",
        "execution_profile": PROFILE,
        "model": CLAUDE_MODEL,
        "effort": "high",
        "effort_observation": "CLI requested; provider setting not independently observed",
        "identity": session.ticket["identity"],
        "image": session.ticket["image"],
        "machine_name": session.machine,
        "vm_config": session.ticket["config"].copy(),
        "input_digest": expected_digest,
        "original_input": {"path": str(input_bundle), "sha256": expected_digest},
        "copied_input": {"sha256": expected_digest, "files": manifest["files"]},
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "prompt": "prompt.txt",
        "native_timeout_seconds": deadline,
        "artifact_limits": {"bytes": max_artifact_bytes, "files": max_artifact_files},
        "capture_policy": "claude-native-stream-and-vm-tool-ledger-v1",
        "native_binary": {"path": str(binary), "sha256": binary_digest},
        "task_quality": "unassessed",
        "skill_consultation": "not-applicable",
        "skill_treatment": {"arm": "absent", "skill_bundle": None},
        "recovery": "recovery.json",
        "artifact_provenance": (
            "guest source and tool evidence plus separately identified host-native streams"
        ),
        "provider_requests": "unknown",
        "provider_reconciliation": "unavailable-native-subscription",
        "authority": "trusted host native client; model-directed commands use isolated VM MCP only",
        "usage": None,
    }
    try:
        _exclusive(output / "prompt.txt", prompt.encode())
        host_work = Path(tempfile.mkdtemp(prefix="skilleval-claude-"))
        (host_work / "work").mkdir(mode=0o700)
        (output / "host-launch").mkdir(mode=0o700)
        settings_path, mcp_path = (
            output / "host-launch/settings.json",
            output / "host-launch/mcp.json",
        )
        tool_config, gate = (
            output / "host-launch/tool-config.json",
            output / "host-launch/init-gate.json",
        )
        tools_log = output / "tools.jsonl"
        mcp = {
            "mcpServers": {
                "vm": {
                    "type": "stdio",
                    "command": sys.executable,
                    "args": ["-I", "-m", "skilleval.claude_vm", str(tool_config)],
                    "env": session.ticket["env"],
                }
            }
        }
        _exclusive(mcp_path, _json(mcp))
        argv, environment = claude_context.launch_context(
            binary, host_work / "work", settings_path, mcp_path, host_work / "plugins"
        )
        claude_context.check_managed_context(Path(environment["HOME"]))
        version = _invoke([str(binary), "--version"], host_work / "work", environment, 20)
        _required(version, "Claude version probe")
        report["claude_identity"] = claude_version_identity(version["stdout"])
        report["authentication"] = claude_context.safe_auth_status(
            binary, environment=environment, settings_path=settings_path, work=host_work / "work"
        )
        _exclusive(output / "host-launch/argv.json", _json({"argv": argv}))
        _exclusive(output / "host-launch/environment.json", _json(environment))
        report["host_launch"] = {
            name: {"sha256": file_digest(output / "host-launch" / name)}
            for name in ("settings.json", "mcp.json", "argv.json", "environment.json")
        }
        entries = [("task/" + name, data, executable) for name, data, executable in files]
        entries += claude_vm.tooling_entries()
        tooling = state / "tooling.tar"
        _tar(tooling, entries)
        report["tooling_sha256"] = file_digest(tooling)
        evidence.verify(input_bundle, expected_digest)
        session.create()
        _required(
            session.invoke(
                "copy_claude_tooling",
                ["machine", "cp", str(tooling), session.machine + ":" + GUEST_TOOLING],
                60,
            ),
            "copy Claude VM tooling",
        )
        _required(
            session.guest(
                "prepare_claude_task",
                preparation_argv(Path(GUEST_TOOLING), Path(GUEST_WORK), Path(GUEST_ROOT)),
                60,
            ),
            "prepare Claude VM task",
        )
        report["vm_initialization"] = claude_vm.initialize(session)
        native_started = time.monotonic()
        timings["setup_seconds"] = native_started - started
        _exclusive(
            tool_config,
            _json(
                {
                    "output": str(output),
                    "deadline_monotonic": native_started + deadline,
                    "gate_path": str(gate),
                    "tools_log": str(tools_log),
                }
            ),
        )
        guard = claude_context.InitGuard(gate)
        process = native_process._execute_native(
            argv,
            environment,
            output / "prompt.txt",
            host_work / "work",
            output,
            deadline,
            stream_limit_bytes=max_artifact_bytes,
            stdout_name="claude-events.jsonl",
            event_observer=guard,
            process_group_grace_seconds=3,
        )
        report["native_process"] = process
        timings["agent_seconds"] = process["agent_elapsed_seconds"]
        raw = (output / "claude-events.jsonl").read_text()
        parsed = parse_claude_jsonl(raw)
        canonical = b"".join(
            json.dumps(row, ensure_ascii=False).encode() + b"\n"
            for row in parsed["canonical_events"]
        )
        final = parsed["final_text"].encode()
        _exclusive(output / "events.jsonl", canonical)
        _exclusive(output / "final.txt", final)
        report.update(events="events.jsonl", final_text="final.txt")
        report["native_capture"] = {
            "surface": "claude-code-stream-json",
            "events": "claude-events.jsonl",
            "sha256": file_digest(output / "claude-events.jsonl"),
            "session_persistence": "disabled; native stream retained",
        }
        report["context"] = {"init_admitted": gate.is_file(), "scope": PROFILE}
        report["native_usage_diagnostics"] = {
            "main_loop": parsed["usage"],
            "model_usage": parsed["model_usage"],
            "client_estimate_usd": parsed["client_estimate_usd"],
        }
        report["execution_status"] = parsed["execution_status"]
        report["parse_errors"] = parsed["parse_errors"]
        # WHY: The MCP process writes the ticket while it runs, so the in-memory session is
        # stale; reload it to recover the latest steps.
        session = Lifecycle.load(output)
        collection_started = time.monotonic()
        report["collection_result"] = claude_vm.collect(
            session, max_artifact_bytes=max_artifact_bytes, max_artifact_files=max_artifact_files
        )
        if report["collection_result"].get("status") != "complete":
            raise SkillEvalError("Claude VM artifact collection incomplete")
        _required(
            _copy_artifacts(session, output / "guest-artifacts.tar", max_artifact_bytes),
            "collect Claude guest artifacts",
        )
        additions = {
            "evidence/events.jsonl": canonical,
            "evidence/final.txt": final,
            "evidence/claude-events.jsonl": raw.encode(),
            "evidence/claude-process.json": _json(process),
            "evidence/claude-stderr.log": (output / "stderr.log").read_bytes(),
        }
        additions.update(
            {
                "evidence/claude-launch/" + name: (output / "host-launch" / name).read_bytes()
                for name in (
                    "settings.json",
                    "mcp.json",
                    "argv.json",
                    "environment.json",
                    "tool-config.json",
                )
            }
        )
        ledger = []
        if tools_log.is_file():
            additions["evidence/claude-tools.jsonl"] = tools_log.read_bytes()
            ledger = [
                json.loads(line) for line in additions["evidence/claude-tools.jsonl"].splitlines()
            ]
            report["tool_calls"] = {
                "path": "tools.jsonl",
                "sha256": file_digest(tools_log),
                "count": sum(row.get("event") == "command-result" for row in ledger),
                "started_count": sum(row.get("event") == "command-started" for row in ledger),
                "ledger_rows": len(ledger),
            }
        artifact_manifest = _merge_artifacts(
            output / "guest-artifacts.tar",
            output / "artifacts.tar",
            additions,
            max_artifact_bytes,
            max_artifact_files,
        )
        _exclusive(output / "artifacts.json", _json(artifact_manifest))
        report["artifacts"] = {
            "archive": "artifacts.tar",
            "sha256": artifact_manifest["sha256"],
            "manifest": "artifacts.json",
            "manifest_sha256": file_digest(output / "artifacts.json"),
        }
        report["input_changes"] = _input_changes(manifest, artifact_manifest)
        report["tool_calls"] = {
            **report.get("tool_calls", {}),
            **_validate_tool_ledger(ledger, parsed["canonical_events"]),
        }
        claude_vm._check_revoked(gate)
        report["status"] = "timed-out" if process["timed_out"] else "failed"
        if (
            process["returncode"] == 0
            and not process["timed_out"]
            and not process["output_limit_reached"]
            and not process["pipe_drain_incomplete"]
            and not process.get("protocol_error")
            and gate.is_file()
            and parsed["execution_status"] == "completed"
            and final.strip()
        ):
            report["status"] = "completed"
        if file_digest(binary) != binary_digest:
            raise SkillEvalError("native Claude executable changed during invocation")
        claude_context.check_managed_context(Path(environment["HOME"]))
    except (SkillEvalError, LifecycleInterruptedError, OSError, ValueError, KeyError) as error:
        report["error"] = str(error) if isinstance(error, SkillEvalError) else type(error).__name__
    finally:
        try:
            evidence.verify(input_bundle, expected_digest)
            report["original_input_unchanged"] = True
        except (SkillEvalError, OSError):
            report["original_input_unchanged"] = False
            report["error"] = "original pinned input changed during Claude invocation"
        cleanup_started = time.monotonic()
        if collection_started is not None:
            timings["collection_seconds"] = cleanup_started - collection_started
        elif native_started is None:
            timings["setup_seconds"] = cleanup_started - started
        # WHY: A failed/interrupted native client can still leave MCP ticket writes.
        with suppress(SkillEvalError, OSError):
            session = Lifecycle.load(output)
        ticket = session.cleanup()
        report["host_context_clean"] = host_work is None
        if host_work is not None:
            try:
                shutil.rmtree(host_work)
                report["host_context_clean"] = True
            except OSError:
                report["error"] = "Claude host context cleanup incomplete"
                report["retained_host_context"] = str(host_work)
        timings["cleanup_seconds"] = time.monotonic() - cleanup_started
        timings["total_seconds"] = time.monotonic() - started
        report["timings"] = timings
        report["usage_accounting"] = parsed["normalized_usage"]
        if report["usage_accounting"].get("model") is None:
            report["usage_accounting"]["model"] = CLAUDE_MODEL
        for key in (
            "cleanup_complete",
            "runtime_home_removed",
            "observed_absent",
            "observer_positive",
            "identity_changed",
            "cleanup",
        ):
            report[key] = ticket.get(key)
        report["steps"] = ticket["steps"]
        if not ticket["cleanup_complete"] or report.get("error"):
            report["status"] = "partial"
        _publish_receipt(output, report)
    return report
