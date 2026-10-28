"""Opt-in native Codex tasks in fresh network-off smolVMs.

The source input is a pinned, source-only ``evidence.snapshot``. This route
executes imported bytes only inside the VM and cannot grant scored admission.
Guest files are untrusted evidence, retained as a bounded archive and never
extracted on the host. Ordinary checks never call this entry point.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import sys
import tarfile
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from skilleval import evidence, guest_runner
from skilleval._vendor import openbench_gateway as vendor
from skilleval.errors import SkillEvalError
from skilleval.gateway import (
    EFFORT,
    MODEL,
    GatewayLimits,
    broker_scope,
    codex_provider_config,
    load_chatgpt_auth,
)
from skilleval.harnesses.codex import codex_version_identity, parse_codex_jsonl
from skilleval.identity import file_digest
from skilleval.lifecycle import (
    Lifecycle,
    LifecycleInterruptedError,
    interrupt_scope,
    successful,
)
from skilleval.qualification import _invoke, _plain_tar_headers

CATALOG_SHA256 = "943ca7fe1d19ed019054158303f0dbbd80b3bef43b3aa425a71aa7cb3fb52c2b"
DEFAULT_LIMITS = GatewayLimits(max_requests=32, timeout_seconds=90, lifetime_seconds=900)
GUEST_ROOT = "/run/native"
GUEST_WORK = "/workspace/task"
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_ARTIFACT_FILES = 10_000
MAX_NATIVE_AUDIT_BYTES = evidence.MAX_FILE_BYTES

_PREPARE_TASK_PROGRAM = """
import os,pathlib,stat,sys,tarfile
archive,task,native=map(pathlib.Path,sys.argv[1:])
for target in (task,native):
    if any(parent.is_symlink() for parent in target.parents):
        raise ValueError('guest preparation ancestors must be real directories')
    if os.path.lexists(target):
        if not stat.S_ISDIR(target.lstat().st_mode) or any(target.iterdir()):
            raise ValueError('guest preparation requires an empty real directory')
    elif not target.parent.is_dir():
        raise ValueError('guest preparation requires an existing parent')
for target in (task,native):
    target.mkdir(mode=0o700,exist_ok=True)
    target.chmod(0o700)
with tarfile.open(archive) as source:
    for member in source:
        if not member.isfile() or not member.name.startswith(('task/','native/')):
            raise ValueError('unsupported guest preparation archive member')
        source.extract(member,task.parent if member.name.startswith('task/') else native.parent,
                       filter='data')
"""


def preparation_argv(archive: Path, task: Path, native: Path) -> list[str]:
    """Exact trusted guest bootstrap; arguments are paths, never shell text."""
    return ["python3", "-c", _PREPARE_TASK_PROGRAM, str(archive), str(task), str(native)]


def protocol_shape(raw: bytes) -> dict[str, Any]:
    """Bounded field/type diagnostics; never retain prompt or history values."""
    fields = {
        "type",
        "role",
        "content",
        "summary",
        "encrypted_content",
        "id",
        "status",
        "name",
        "namespace",
        "call_id",
        "arguments",
        "input",
        "output",
        "text",
        "annotations",
        "logprobs",
    }

    def shape(value: Any, depth: int = 0) -> Any:
        if depth > 3:
            return type(value).__name__
        if isinstance(value, dict):
            return {
                "fields": {
                    key: shape(item, depth + 1) for key, item in value.items() if key in fields
                },
                "unknown_field_count": sum(key not in fields for key in value),
            }
        if isinstance(value, list):
            return {
                "length": len(value),
                "items": [shape(item, depth + 1) for item in value[:8]],
                "items_omitted": max(0, len(value) - 8),
            }
        return type(value).__name__

    try:
        value = json.loads(raw)
        result = shape(value.get("input", []) if isinstance(value, dict) else value)
        diagnostics = {
            "shape": result,
            "shape_limits": {"max_depth": 3, "list_items": 8},
            "request_structure": _request_structure(value),
        }
        if len(_json(diagnostics)) > 16_384:
            diagnostics["shape"] = "limit-exceeded"
        return diagnostics
    except (ValueError, RecursionError):
        return {"shape": "invalid-json"}


def _request_structure(value: Any) -> dict[str, Any]:
    """Count all top-level input kinds; only fixed field names and enum values escape."""
    result: dict[str, Any] = {"schema_version": 1, "root_type": type(value).__name__}
    if not isinstance(value, dict):
        return result
    fields = {
        "model",
        "instructions",
        "input",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "reasoning",
        "text",
        "store",
        "stream",
        "include",
        "prompt_cache_key",
        "max_output_tokens",
        "service_tier",
        "metadata",
        "previous_response_id",
    }
    result["fields"] = {key: type(item).__name__ for key, item in value.items() if key in fields}
    result["unknown_root_field_count"] = sum(key not in fields for key in value)
    if "input" not in value:
        result["input"] = {"type": "missing"}
        return result
    items = value["input"]
    summary: dict[str, Any] = {"type": type(items).__name__}
    result["input"] = summary
    if not isinstance(items, list):
        return result
    kinds = {
        "message",
        "reasoning",
        "function_call",
        "function_call_output",
        "custom_tool_call",
        "custom_tool_call_output",
        "item_reference",
        "compaction",
        "tool_search_call",
        "tool_search_output",
    }
    roles = {"system", "developer", "user", "assistant", "tool"}
    summary.update(item_count=len(items), non_object_count=0, kind_counts={}, role_counts={})
    for item in items:
        if not isinstance(item, dict):
            summary["non_object_count"] += 1
            continue
        for field, allowed, destination in (
            ("type", kinds, "kind_counts"),
            ("role", roles, "role_counts"),
        ):
            token = item.get(field)
            bucket = (
                "missing"
                if field not in item
                else token
                if isinstance(token, str) and token in allowed
                else "unknown"
            )
            counts = summary[destination]
            counts[bucket] = counts.get(bucket, 0) + 1
    return result


def _json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def _content_identity(value: object) -> dict[str, Any]:
    content = value.encode() if isinstance(value, str) else _json(value)
    return {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def _scalar(value: Any) -> Any:
    if isinstance(value, str):
        return value[:256]
    if value is None or type(value) is bool:
        return value
    if type(value) is int and -(2**63) <= value < 2**63:
        return value
    return None


def _usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    return {
        key: item
        for key, item in value.items()
        if key in {"input_tokens", "cached_input_tokens", "output_tokens", "total_tokens"}
        and type(item) is int
        and 0 <= item < 2**63
    }


def _execution_metadata(parsed: dict[str, Any]) -> dict[str, Any]:
    """Keep interpretation bounded; complete observations remain in events.jsonl."""
    result = {
        key: _scalar(parsed.get(key))
        for key in (
            "schema_version",
            "harness",
            "execution_status",
            "task_quality",
            "skill_consultation",
            "thread_id",
        )
    }
    result["usage"] = _usage(parsed["usage"])
    for key in ("usage_observations", "item_outcomes", "errors", "parse_errors"):
        values = parsed[key]
        result[key + "_count"] = len(values)
        result[key + "_omitted"] = max(0, len(values) - 64)
        if key == "usage_observations":
            result[key] = [_usage(value) for value in values[:64]]
        elif key == "item_outcomes":
            result[key] = [
                {
                    field: _scalar(value.get(field))
                    for field in ("id", "type", "status", "exit_code")
                }
                for value in values[:64]
            ]
        elif key == "parse_errors":
            result[key] = [
                {"line": _scalar(value.get("line")), "error": _scalar(value.get("error"))}
                for value in values[:64]
            ]
        else:
            result[key] = [_content_identity(value) for value in values[:64]]
    return result


def _command_metadata(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    commands: list[dict[str, Any]] = []
    count = 0
    for index, event in enumerate(events):
        item = event.get("item")
        if (
            event.get("type") != "item.completed"
            or not isinstance(item, dict)
            or item.get("type") != "command_execution"
        ):
            continue
        count += 1
        if len(commands) >= 256:
            continue
        command, output = (
            _content_identity(item.get("command")),
            _content_identity(item.get("aggregated_output")),
        )
        commands.append(
            {
                "event_index": index,
                **{key: _scalar(item.get(key)) for key in ("id", "status", "exit_code")},
                "command_bytes": command["bytes"],
                "command_sha256": command["sha256"],
                "output_bytes": output["bytes"],
                "output_sha256": output["sha256"],
            }
        )
    return commands, count


def _step_metadata(value: dict[str, Any]) -> dict[str, Any]:
    return {
        **{
            key: _scalar(value.get(key))
            for key in (
                "label",
                "action",
                "returncode",
                "timed_out",
                "output_truncated",
                "pipe_drain_incomplete",
            )
            if key in value
        },
        **{
            key + "_identity": _content_identity(value[key])
            for key in ("argv", "stdout", "stderr", "error")
            if key in value
        },
    }


def _publish_receipt(output: Path, report: dict[str, Any]) -> None:
    """Retain full audit metadata separately from the bounded consumer receipt."""
    audit = _json(report)
    report["audit"] = {
        "path": "native-audit.json",
        "bytes": len(audit),
        "sha256": hashlib.sha256(audit).hexdigest(),
    }
    report["identity"] = {**_content_identity(report["identity"]), "audit_field": "identity"}
    copied = report["copied_input"]
    copied["file_count"] = len(copied.pop("files"))
    copied["inventory_audit_field"] = "copied_input.files"
    treatment = report.get("skill_treatment", {})
    if "files" in treatment:
        treatment["file_count"] = len(treatment.pop("files"))
        treatment["inventory_audit_field"] = "skill_treatment.files"
    changes = report.get("input_changes", {})
    for key in ("modified_or_removed", "created"):
        if key in changes:
            values = changes[key]
            changes[key + "_count"] = len(values)
            changes[key + "_omitted"] = max(0, len(values) - 64)
            changes[key] = values[:64]
    for key in ("steps", "cleanup"):
        report[key] = [_step_metadata(value) for value in report.get(key, [])[:64]]
    consultation = report.get("skill_consultation")
    if isinstance(consultation, dict) and "observations" in consultation:
        observations = consultation["observations"]
        consultation["observation_count"] = len(observations)
        consultation["observations_omitted"] = max(0, len(observations) - 64)
        consultation["observations"] = observations[:64]
    if len(audit) > MAX_NATIVE_AUDIT_BYTES or len(_json(report)) > 4 * 1024 * 1024:
        # Preserve oversized private evidence for the operator, but do not claim
        # a successful record that the append-only grader cannot retain.
        _exclusive(output / "native-audit-overflow.json", audit)
        overflow = {
            "path": "native-audit-overflow.json",
            "bytes": len(audit),
            "sha256": hashlib.sha256(audit).hexdigest(),
        }
        critical = (
            "schema_version",
            "kind",
            "mode",
            "investigation",
            "scope",
            "admitted",
            "scored_admitted",
            "model",
            "effort",
            "machine_name",
            "input_digest",
            "original_input",
            "original_input_unchanged",
            "copied_input",
            "prompt_sha256",
            "prompt",
            "final_text",
            "events",
            "artifacts",
            "cleanup_complete",
            "recovery",
        )
        kept = {key: report[key] for key in critical if key in report}
        report.clear()
        report.update(kept)
        report.update(
            {
                "status": "partial",
                "error": "native metadata recording limit exceeded",
                "audit_overflow": overflow,
            }
        )
        audit = _json({**report, "metadata_complete": False})
    report["audit"] = {
        "path": "native-audit.json",
        "bytes": len(audit),
        "sha256": hashlib.sha256(audit).hexdigest(),
    }
    _exclusive(output / "native-audit.json", audit)
    _exclusive(output / "native-run.json", _json(report))


def _exclusive(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        target.write(content)
        target.flush()
        os.fsync(target.fileno())


def snapshot_digest(bundle: Path) -> str:
    """Return the manifest digest of an existing generic source-only snapshot."""
    manifest = evidence.verify(bundle)
    if manifest["kind"] != "snapshot":
        raise SkillEvalError("native input must be a source-only generic snapshot")
    with evidence._directory(bundle) as root:
        raw, _ = evidence._read_file(root, "manifest.json", evidence.MAX_MANIFEST_BYTES)
    return hashlib.sha256(raw).hexdigest()


def _snapshot_files(
    bundle: Path, expected: str
) -> tuple[dict[str, Any], list[tuple[str, bytes, bool]]]:
    manifest = evidence.verify(bundle, expected)
    if manifest["kind"] != "snapshot":
        raise SkillEvalError("native input must be a source-only generic snapshot")
    expected_files = {entry["path"]: entry for entry in manifest["files"]}
    files = []
    with evidence._directory(bundle) as root:
        for name, data, executable in evidence._walk(root):
            if name == "manifest.json":
                continue
            entry = expected_files.get(name)
            if (
                entry is None
                or entry["size"] != len(data)
                or entry["sha256"] != hashlib.sha256(data).hexdigest()
                or entry["executable"] != executable
            ):
                raise SkillEvalError("native source changed before copy")
            files.append((name, data, executable))
    if {name for name, _, _ in files} != set(expected_files):
        raise SkillEvalError("native source inventory changed before copy")
    return manifest, files


def _tar(path: Path, entries: list[tuple[str, bytes, bool]]) -> None:
    with tarfile.open(path, "x", format=tarfile.USTAR_FORMAT) as target:
        for name, data, executable in entries:
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755 if executable else 0o644
            target.addfile(info, io.BytesIO(data))
    path.chmod(0o600)


def validate_artifacts(archive: Path, maximum: int, count: int) -> dict[str, Any]:
    """Validate all archive members before any consumer reads their content.

    Links, sparse/PAX extensions, aliases and noncanonical names are refused.
    Even rejected raw archives remain private evidence; nothing is extracted.
    """
    if archive.is_symlink() or not archive.is_file() or archive.stat().st_size > maximum:
        raise SkillEvalError("native artifact archive exceeds its regular-file limit")
    entries = []
    seen: set[str] = set()
    total = 0
    try:
        descriptor = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as raw:
            if not stat.S_ISREG(os.fstat(raw.fileno()).st_mode):
                raise SkillEvalError("native artifact archive must be regular")
            _plain_tar_headers(raw)
        with tarfile.open(archive, "r:") as source:
            for member in source:
                name = member.name
                parts = PurePosixPath(name).parts
                alias = name.casefold()
                if (
                    not member.isreg()
                    or member.pax_headers
                    or member.sparse is not None
                    or not parts
                    or parts[0] not in {"task", "home", "codex-home", "evidence"}
                    or name != "/".join(parts)
                    or name.startswith("/")
                    or any(part in {"", ".", ".."} for part in parts)
                    or "\\" in name
                    or any(ord(char) < 32 for char in name)
                    or alias in seen
                    or member.mode & 0o7000
                    or member.size < 0
                ):
                    raise SkillEvalError("unsafe native artifact archive member")
                seen.add(alias)
                total += member.size
                if len(seen) > count or total > maximum:
                    raise SkillEvalError("native artifact inventory limit exceeded")
                stream = source.extractfile(member)
                if stream is None:
                    raise SkillEvalError("native artifact member unavailable")
                digest = hashlib.sha256()
                observed = 0
                while block := stream.read(1024 * 1024):
                    observed += len(block)
                    digest.update(block)
                if observed != member.size:
                    raise SkillEvalError("incomplete native artifact bytes")
                entries.append(
                    {
                        "path": name,
                        "size": observed,
                        "sha256": digest.hexdigest(),
                        "executable": bool(member.mode & 0o111),
                    }
                )
    except (OSError, ValueError, tarfile.TarError):
        raise SkillEvalError("invalid native artifact archive") from None
    return {
        "schema_version": 1,
        "kind": "skilleval-native-artifacts",
        "sha256": file_digest(archive),
        "entries": entries,
    }


def _member(archive: Path, name: str, maximum: int) -> bytes:
    descriptor = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as raw:
        if os.fstat(raw.fileno()).st_size > MAX_ARTIFACT_BYTES:
            raise SkillEvalError("native artifact archive exceeds maximum size")
        _plain_tar_headers(raw)
    with tarfile.open(archive, "r:") as source:
        info = source.getmember(name)
        if info.size > maximum:
            raise SkillEvalError("native evidence member size limit exceeded")
        stream = source.extractfile(info)
        if stream is None:
            raise SkillEvalError("native evidence member unavailable")
        return stream.read(maximum + 1)


def _copy_artifacts(session: Lifecycle, destination: Path, maximum: int) -> dict[str, Any]:
    # Bound writes made by the host copy client, before inspecting hostile bytes.
    # This trusted controller fragment never imports or executes guest artifacts.
    wrapper = (
        "import os,resource,sys; n=int(sys.argv[1]); "
        "resource.setrlimit(resource.RLIMIT_FSIZE,(n,n)); os.execv(sys.argv[2],sys.argv[2:])"
    )
    result = _invoke(
        [
            sys.executable,
            "-c",
            wrapper,
            str(maximum),
            session.ticket["identity"]["runtime"]["binary"],
            "machine",
            "cp",
            session.machine + ":/run/native/artifacts.tar",
            str(destination),
        ],
        Path(session.ticket["runtime_state"]),
        session.ticket["env"],
        60,
    )
    session.ticket["steps"].append({"label": "collect_artifacts_bounded", **result})
    session.save()
    return result


def _required(result: dict[str, Any], label: str) -> None:
    if not successful(result) or result.get("output_truncated"):
        raise SkillEvalError(f"native runtime step failed: {label}")


def _read_native(archive: Path, maximum: int) -> tuple[dict[str, Any], bytes, bytes]:
    try:
        process = json.loads(_member(archive, "evidence/process.json", 128 * 1024))
        try:
            events = _member(archive, "evidence/events.jsonl", maximum)
        except KeyError:
            events = b""
        try:
            final = _member(archive, "evidence/final.txt", maximum)
        except KeyError:
            final = b""
        if not isinstance(process, dict):
            raise ValueError
        return process, events, final
    except (ValueError, KeyError, tarfile.TarError, UnicodeError):
        raise SkillEvalError("native task evidence is incomplete") from None


@interrupt_scope()
def run_native(
    input_bundle: Path,
    expected_digest: str,
    prompt: str,
    output: Path,
    *,
    runtime: Path,
    image: Path,
    image_sha256: str,
    catalog: Path,
    auth_file: Path,
    mode: Literal["review", "judge"] = "review",
    limits: GatewayLimits = DEFAULT_LIMITS,
    native_timeout_seconds: int = 600,
    max_artifact_bytes: int = 64 * 1024 * 1024,
    max_artifact_files: int = 10_000,
    skill_bundle: Path | None = None,
    skill_digest: str | None = None,
    investigation: bool | None = None,
) -> dict[str, Any]:
    """Execute explicitly authorized development work; never creates admission.

    ``input_bundle`` must already exclude hidden labels and unrelated context.
    Prompt contents are retained privately. Neither auth bytes nor host settings
    are mounted/copied. Every call needs a fresh, non-nested output directory.
    """
    if (
        mode not in {"review", "judge"}
        or not isinstance(prompt, str)
        or not prompt.strip()
        or len(prompt.encode()) > 1024 * 1024
        or not re.fullmatch(r"[0-9a-f]{64}", expected_digest)
        or type(native_timeout_seconds) is not int
        or not 1 <= native_timeout_seconds <= 780
        or native_timeout_seconds + 60 > limits.lifetime_seconds
        or type(max_artifact_bytes) is not int
        or not 1024 * 1024 <= max_artifact_bytes <= MAX_ARTIFACT_BYTES
        or type(max_artifact_files) is not int
        or not 1 <= max_artifact_files <= MAX_ARTIFACT_FILES
        or (skill_bundle is None) != (skill_digest is None)
        or (mode == "judge" and type(investigation) is not bool)
        or (mode == "review" and investigation is not None)
        or (mode == "judge" and skill_bundle is not None)
    ):
        raise SkillEvalError("invalid native workload, deadline, or artifact limit")
    input_bundle, output = evidence._paths(input_bundle, output)
    manifest, files = _snapshot_files(input_bundle, expected_digest)
    if mode == "review":
        from skilleval.treatments import check_task_baseline

        check_task_baseline(input_bundle)
    if file_digest(catalog) != CATALOG_SHA256:
        raise SkillEvalError("native model catalog differs from pinned source")
    auth = load_chatgpt_auth(auth_file, minimum_validity_seconds=limits.lifetime_seconds + 30)
    session = Lifecycle.prepare(output, runtime, image, image_sha256)
    state = Path(session.ticket["runtime_state"])
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "skilleval-native-run",
        "investigation": investigation,
        "mode": mode,
        "status": "partial",
        "scope": "development-only",
        "admitted": False,
        "scored_admitted": False,
        "identity": session.ticket["identity"],
        "input_digest": expected_digest,
        "original_input": {"path": str(input_bundle), "sha256": expected_digest},
        "copied_input": {"sha256": expected_digest, "files": manifest["files"]},
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "model": MODEL,
        "effort": EFFORT,
        "gateway_limits": asdict(limits),
        "native_timeout_seconds": native_timeout_seconds,
        "artifact_limits": {"bytes": max_artifact_bytes, "files": max_artifact_files},
        "image": session.ticket["image"],
        "machine_name": session.machine,
        "catalog_sha256": CATALOG_SHA256,
        "task_quality": "unassessed",
        "skill_consultation": "unknown",
        "usage": None,
        "artifact_provenance": "guest-authored, not trusted grading labels",
        "recovery": "recovery.json",
        "prompt": "prompt.txt",
    }
    broker = None
    request_records: list[dict[str, Any]] = []
    protocol_rejections: list[dict[str, Any]] = []
    skill_detector: Callable[[bytes], dict[str, Any]] | None = None
    guest_skill_path: str | None = None

    def observe_request(raw: bytes) -> None:
        if len(request_records) >= 32:
            raise SkillEvalError("native request observation limit exceeded")
        record: dict[str, Any] = {
            "index": len(request_records) + 1,
            "input_sha256": hashlib.sha256(raw).hexdigest(),
            "input_bytes": len(raw),
            **protocol_shape(raw),
        }
        if skill_detector is not None:
            record.update(skill_detector(raw))
        request_records.append(record)
        try:
            vendor.validate_body(
                raw,
                vendor.GatewayConfig(
                    MODEL,
                    EFFORT,
                    limits.max_requests,
                    limits.max_body_bytes,
                    limits.timeout_seconds,
                    limits.max_concurrent_requests,
                ),
            )
        except ValueError:
            if len(protocol_rejections) < 4:
                protocol_rejections.append(
                    {"request_index": record["index"], **protocol_shape(raw)}
                )

    try:
        config = codex_provider_config(8765, Path(GUEST_ROOT) / "models.json")
        settings = {
            "native_timeout_seconds": native_timeout_seconds,
            "request_timeout_seconds": limits.timeout_seconds,
            "max_body_bytes": limits.max_body_bytes,
            "max_artifact_bytes": max_artifact_bytes,
            "max_artifact_files": max_artifact_files,
        }
        entries = [("task/" + name, data, executable) for name, data, executable in files]
        entries += [
            ("native/models.json", catalog.read_bytes(), False),
            ("native/config.toml", config.encode(), False),
            ("native/prompt.txt", prompt.encode(), False),
            ("native/settings.json", _json(settings), False),
            ("native/runner.py", Path(guest_runner.__file__).read_bytes(), False),
        ]
        if skill_bundle is not None and skill_digest is not None:
            from skilleval.treatments import install_skill, request_observer

            skill_home = state / "skill-home"
            report["skill_treatment"] = install_skill(skill_bundle, skill_digest, skill_home)
            guest_skill_path = (
                GUEST_ROOT
                + "/codex-home/skills/"
                + report["skill_treatment"]["skill_name"]
                + "/SKILL.md"
            )
            skill_detector = request_observer(skill_bundle, skill_digest, guest_skill_path)
            with evidence._directory(skill_home) as root:
                entries += [
                    ("native/codex-home/" + name, data, executable)
                    for name, data, executable in evidence._walk(root)
                ]
        else:
            report["skill_treatment"] = {"arm": "absent", "skill_bundle": None}
        tooling = state / "tooling.tar"
        _tar(tooling, entries)
        report["tooling_sha256"] = file_digest(tooling)
        report["native_config"] = config
        report["native_config_sha256"] = hashlib.sha256(config.encode()).hexdigest()
        _exclusive(output / "prompt.txt", prompt.encode())
        # Recheck both authority-bearing copied inputs before the first VM mutation.
        evidence.verify(input_bundle, expected_digest)
        with broker_scope(
            auth,
            Path(session.ticket["runtime_home"]) / "b",
            limits=limits,
            request_observer=observe_request,
        ) as broker:
            session.ticket["config"].update(
                {
                    "observer_socket": str(broker.socket_path),
                    "guest_observer_socket": "/run/inference.sock",
                }
            )
            session.ticket["config_sha256"] = hashlib.sha256(
                json.dumps(session.ticket["config"], sort_keys=True).encode()
            ).hexdigest()
            session.save()
            report["vm_config"] = session.ticket["config"].copy()
            session.create()
            _required(session.guest("codex_version", ["codex", "--version"]), "codex_version")
            report["codex_identity"] = codex_version_identity(session.ticket["steps"][-1]["stdout"])
            _required(
                session.invoke(
                    "copy_tooling",
                    ["machine", "cp", str(tooling), session.machine + ":/run/tooling.tar"],
                    60,
                ),
                "copy_tooling",
            )
            _required(
                session.guest(
                    "prepare_task",
                    preparation_argv(Path("/run/tooling.tar"), Path(GUEST_WORK), Path(GUEST_ROOT)),
                    60,
                ),
                "prepare_task",
            )
            native = session.guest(
                "native_task",
                ["python3", GUEST_ROOT + "/runner.py", GUEST_ROOT, GUEST_WORK],
                native_timeout_seconds + 30,
            )
            report["native_controller"] = native
            collection = session.guest(
                "collection_status", ["cat", GUEST_ROOT + "/collection.json"]
            )
            report["collection"] = collection
            _required(
                _copy_artifacts(session, output / "artifacts.tar", max_artifact_bytes),
                "collect_artifacts_bounded",
            )
            archive = output / "artifacts.tar"
            artifact_manifest = validate_artifacts(archive, max_artifact_bytes, max_artifact_files)
            _exclusive(output / "artifacts.json", _json(artifact_manifest))
            report["artifacts"] = {
                "archive": "artifacts.tar",
                "sha256": artifact_manifest["sha256"],
                "manifest": "artifacts.json",
                "manifest_sha256": file_digest(output / "artifacts.json"),
            }
            process, raw, final = _read_native(archive, max_artifact_bytes)
            _exclusive(output / "events.jsonl", raw)
            _exclusive(output / "final.txt", final)
            report.update({"process": process, "final_text": "final.txt", "events": "events.jsonl"})
            parsed = parse_codex_jsonl(raw.decode("utf-8"))
            report["execution"] = _execution_metadata(parsed)
            report["usage"] = _usage(parsed["usage"])
            if (
                skill_bundle is not None
                and skill_digest is not None
                and guest_skill_path is not None
            ):
                from skilleval.treatments import observe_consultation

                report["skill_consultation"] = observe_consultation(
                    parsed["events"], skill_bundle, skill_digest, guest_skill_path
                )
            report["commands"], report["command_count"] = _command_metadata(parsed["events"])
            report["commands_omitted"] = report["command_count"] - len(report["commands"])
            original = {entry["path"]: entry for entry in manifest["files"]}
            after = {
                entry["path"].removeprefix("task/"): entry
                for entry in artifact_manifest["entries"]
                if entry["path"].startswith("task/")
            }
            report["input_changes"] = {
                "basis": "guest-authored archive compared with pinned input",
                "modified_or_removed": sorted(
                    name
                    for name, entry in original.items()
                    if name not in after
                    or any(
                        entry[key] != after[name][key] for key in ("size", "sha256", "executable")
                    )
                ),
                "created": sorted(set(after) - set(original)),
            }
            report["status"] = "timed-out" if process.get("timed_out") else "failed"
            requests = [row for row in broker.receipts if row.get("event") == "request"]
            provider_complete = bool(requests) and all(
                row.get("outcome") == "complete" and row.get("upstream_status") == 200
                for row in requests
            )
            report["provider_complete"] = provider_complete
            if (
                successful(native)
                and process.get("returncode") == 0
                and process.get("timed_out") is False
                and parsed["execution_status"] == "completed"
                and not parsed["errors"]
                and not parsed["parse_errors"]
                and final.strip()
                and provider_complete
            ):
                report["status"] = "completed"
            evidence.verify(input_bundle, expected_digest)
            report["original_input_unchanged"] = True
        report["broker_clean"] = broker.clean
    except (SkillEvalError, LifecycleInterruptedError, OSError, ValueError, KeyError) as error:
        report["error"] = str(error) if isinstance(error, SkillEvalError) else type(error).__name__
    finally:
        try:
            evidence.verify(input_bundle, expected_digest)
            report["original_input_unchanged"] = True
        except (SkillEvalError, OSError):
            report["original_input_unchanged"] = False
            report["error"] = "original pinned input changed during native execution"
        if broker is not None:
            report["broker_receipts"] = broker.receipts
            report["broker_clean"] = broker.clean
            report["request_count"] = sum(row.get("event") == "request" for row in broker.receipts)
        report["request_observations"] = request_records
        report["protocol_rejections"] = protocol_rejections
        ticket = session.cleanup()
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
        if (
            not ticket["cleanup_complete"]
            or report.get("broker_clean") is not True
            or report.get("error")
        ):
            report["status"] = "partial"
        _publish_receipt(output, report)
    return report
