"""Frozen repair contracts and source artifacts; imported code never executes here."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from skilleval import evidence, runner
from skilleval.contracts import Digest, Identifier, Record
from skilleval.errors import SkillEvalError
from skilleval.grading import _read_regular
from skilleval.identity import file_digest
from skilleval.judge import _run_files
from skilleval.treatments import _inventory, check_task_baseline
from skilleval.workspace import _git, _safe_path


class RepairProbe(Record):
    probe_id: Identifier
    request: dict[str, Any]
    expected: Any


class RepairSpec(Record):
    task_id: Identifier
    input_bundle: str = Field(min_length=1)
    input_sha256: Digest
    prompt: str = Field(min_length=1, max_length=262144)
    editable_paths: tuple[str, ...] = Field(min_length=1, max_length=100)
    worker: str = Field(min_length=1)
    worker_sha256: Digest
    probes: tuple[RepairProbe, ...] = Field(min_length=1, max_length=100)
    timeout_seconds: int = Field(default=60, ge=1, le=180)
    source_url: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)

    @model_validator(mode="after")
    def bounded_contract(self) -> RepairSpec:
        names = list(self.editable_paths)
        for name in names:
            if _safe_path(name) != name or ignored(name) or name == "manifest.json":
                raise ValueError("invalid editable repair path")
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError("duplicate or case-aliased repair paths")
        if len({probe.probe_id for probe in self.probes}) != len(self.probes):
            raise ValueError("duplicate repair probe IDs")
        if (
            len(evidence._json_bytes([p.model_dump(mode="json") for p in self.probes]))
            > 1024 * 1024
        ):
            raise ValueError("repair probe payload exceeds limit")
        return self


def ignored(name: str) -> bool:
    """Only explicit disposable metadata; normal source/config/test bytes count."""
    parts = Path(name).parts
    return parts[0] == ".git" or any(p in {"__pycache__", ".pytest_cache"} for p in parts)


def _publish(path: Path, value: dict[str, Any]) -> str:
    from skilleval.study import _new

    return _new(path, value)


def _inputs(spec: RepairSpec) -> list[tuple[str, bytes, bool]]:
    _, files = runner._snapshot_files(Path(spec.input_bundle), spec.input_sha256)
    check_task_baseline(Path(spec.input_bundle))
    if Path(spec.worker).resolve().is_relative_to(Path(spec.input_bundle).resolve()):
        raise SkillEvalError("repair worker must be outside solver input")
    raw = _read_regular(Path(spec.worker), 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != spec.worker_sha256:
        raise SkillEvalError("repair worker digest differs")
    selected = [row for row in files if not ignored(row[0])]
    # The initial patch adapter supports the same portable path subset as the
    # review exporter. Refuse unsupported context before spending a native run.
    for name, _, _ in selected:
        _safe_path(name)
    return selected


def prepare_repair(spec: RepairSpec, destination: Path) -> dict[str, Any]:
    data = spec.model_dump(mode="json")
    for key in ("input_bundle", "worker"):
        data[key] = os.path.abspath(data[key])
    spec = RepairSpec.model_validate_json(json.dumps(data))
    _inputs(spec)
    if destination.resolve().is_relative_to(Path(spec.input_bundle).resolve()):
        raise SkillEvalError("repair contract must stay outside solver input")
    value = {
        "schema_version": 1,
        "kind": "skilleval-repair-task",
        "spec": spec.model_dump(mode="json"),
    }
    digest = _publish(destination, value)
    return {
        "status": "prepared",
        "path": str(destination.absolute()),
        "sha256": digest,
        "scope": "development-only",
        "task_quality": "unassessed",
    }


def load_task(path: Path, digest: str) -> RepairSpec:
    raw = _read_regular(path, 2 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise SkillEvalError("repair task digest differs")
    value = evidence._parse_json(raw)
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "kind", "spec"}
        or value["schema_version"] != 1
        or value["kind"] != "skilleval-repair-task"
    ):
        raise SkillEvalError("unsupported repair task")
    spec = RepairSpec.model_validate_json(json.dumps(value["spec"]))
    _inputs(spec)
    return spec


def _write(root: Path, files: list[tuple[str, bytes, bool]]) -> None:
    for name, raw, executable in files:
        _safe_path(name)
        path = root / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        runner._exclusive(path, raw)
        path.chmod(0o755 if executable else 0o644)


def _patch(before: list[tuple[str, bytes, bool]], after: list[tuple[str, bytes, bool]]) -> bytes:
    # Hash exact bytes without imported attributes/filters. Separate payload
    # trees also support file/directory replacements without stale directories.
    with tempfile.TemporaryDirectory(prefix="skilleval-repair-diff-") as temporary:
        root = Path(temporary)
        _git(root, "init", "--quiet", "--template=")
        trees: list[str] = []
        for label, files in (("before", before), ("after", after)):
            _write(root / label, files)
            _git(root, "read-tree", "--empty")
            index = bytearray()
            # Bound argv size while avoiding one subprocess per source file.
            for offset in range(0, len(files), 64):
                batch = files[offset : offset + 64]
                hashes = _git(
                    root,
                    "hash-object",
                    "-w",
                    "--no-filters",
                    "--",
                    *[label + "/" + name for name, _, _ in batch],
                ).splitlines()
                for (name, _, executable), digest in zip(batch, hashes, strict=True):
                    index.extend((b"100755" if executable else b"100644") + b" " + digest)
                    index.extend(b"\t" + name.encode("ascii") + b"\0")
            if index:
                _git(root, "update-index", "-z", "--index-info", data=bytes(index))
            trees.append(_git(root, "write-tree").decode().strip())
        return _git(
            root,
            "diff",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-textconv",
            *trees,
            "--",
        )


def _record(
    task: Path,
    task_digest: str,
    spec: RepairSpec,
    files: list[tuple[str, bytes, bool]],
    destination: Path,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    before = _inputs(spec)
    files = sorted(row for row in files if not ignored(row[0]))
    old = {row["path"]: row for row in _inventory(before)}
    new = {row["path"]: row for row in _inventory(files)}
    changed = sorted(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))
    out_of_scope = sorted(set(changed) - set(spec.editable_paths))
    destination = destination.absolute()
    if destination.resolve().is_relative_to(Path(spec.input_bundle).resolve()):
        raise SkillEvalError("repair result must be outside original input")
    with (
        evidence._directory(destination.parent),
        tempfile.TemporaryDirectory(prefix=".repair-", dir=destination.parent) as temporary,
    ):
        stage = Path(temporary)
        source = stage / "source"
        source.mkdir()
        _write(source, files)
        captured = evidence.snapshot(source, stage / "candidate")
        import shutil

        shutil.rmtree(source)
        patch = _patch(before, files)
        runner._exclusive(stage / "changes.patch", patch)
        result = {
            "schema_version": 1,
            "kind": "skilleval-repair-result",
            "status": "collected" if not out_of_scope else "invalid-scope",
            "task_sha256": task_digest,
            "input_sha256": spec.input_sha256,
            "candidate_sha256": captured["manifest_sha256"],
            "patch_sha256": hashlib.sha256(patch).hexdigest(),
            "changed_paths": changed,
            "out_of_scope": out_of_scope,
            "provenance": provenance,
            "task_quality": "unassessed",
            "scope": "development-only",
            "scored_admitted": False,
        }
        digest = _publish(stage / "repair-result.json", result)
        # Recheck originals before publishing; task/worker drift cannot be hidden.
        load_task(task, task_digest)
        evidence._publish(stage, destination)
    return {
        "status": result["status"],
        "path": str(destination),
        "sha256": digest,
        "changed_paths": changed,
        "task_quality": "unassessed",
    }


def collect_repair(task: Path, digest: str, run: Path, destination: Path) -> dict[str, Any]:
    from skilleval.study import _clean_native

    spec = load_task(task, digest)
    receipt, retained, artifacts = _run_files(run, mode="repair")
    if receipt["input_digest"] != spec.input_sha256:
        raise SkillEvalError("repair run used a different input")
    if not (run / "recovery.json").is_file() or not _clean_native(run.absolute()):
        raise SkillEvalError("repair collection requires completed native VM cleanup")
    prompts = {spec.prompt.encode()}
    treatment = receipt.get("skill_treatment", {})
    if isinstance(treatment, dict) and isinstance(treatment.get("skill_name"), str):
        prompts.add((f"Use ${treatment['skill_name']} for this task.\n\n" + spec.prompt).encode())
    if retained.get("prompt.txt") not in prompts:
        raise SkillEvalError("repair run used a different task prompt")
    links = evidence._parse_json(artifacts.get("evidence/filesystem-links.json", b"null"))
    if not isinstance(links, list) or any(
        not isinstance(row, dict) or str(row.get("path", "")).startswith("task/") for row in links
    ):
        raise SkillEvalError("repair task contains links or incomplete link inventory")
    manifest = evidence._parse_json(retained["artifacts.json"])
    modes = {row["path"]: row["executable"] for row in manifest["entries"]}
    files = [
        (name[5:], raw, modes[name]) for name, raw in artifacts.items() if name.startswith("task/")
    ]
    provenance = {
        "kind": "native-repair",
        "native_run": str(run.absolute()),
        "native_receipt_sha256": file_digest(run / "native-run.json"),
        "execution_status": receipt["status"],
        "native_cleanup_complete": receipt.get("cleanup_complete") is True,
        "cleanup_complete": True,
        "recovery_sha256": file_digest(run / "recovery.json"),
    }
    return _record(task, digest, spec, files, destination, provenance)


def collect_control(
    task: Path, digest: str, candidate: Path, candidate_digest: str, destination: Path
) -> dict[str, Any]:
    spec = load_task(task, digest)
    _, files = runner._snapshot_files(candidate, candidate_digest)
    return _record(
        task,
        digest,
        spec,
        files,
        destination,
        {
            "kind": "authored-control",
            "candidate_input_sha256": candidate_digest,
            "model_attempt": False,
        },
    )


def result_files(
    spec: RepairSpec, task_digest: str, result_dir: Path, digest: str
) -> tuple[dict[str, Any], list[tuple[str, bytes, bool]]]:
    raw = _read_regular(result_dir / "repair-result.json", 2 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise SkillEvalError("repair result digest differs")
    result = evidence._parse_json(raw)
    if not isinstance(result, dict) or result.get("schema_version") != 1:
        raise SkillEvalError("unsupported repair result")
    if (
        result.get("kind") != "skilleval-repair-result"
        or result.get("task_sha256") != task_digest
        or result.get("input_sha256") != spec.input_sha256
    ):
        raise SkillEvalError("repair result belongs to another task")
    if result.get("status") != "collected" or result.get("out_of_scope") != []:
        raise SkillEvalError("repair result changed files outside the frozen scope")
    _, files = runner._snapshot_files(result_dir / "candidate", result["candidate_sha256"])
    before = {row["path"]: row for row in _inventory(_inputs(spec))}
    after = {row["path"]: row for row in _inventory(files)}
    changed = sorted(
        name for name in before.keys() | after.keys() if before.get(name) != after.get(name)
    )
    if (
        changed != result.get("changed_paths")
        or set(changed) - set(spec.editable_paths)
        or file_digest(result_dir / "changes.patch") != result.get("patch_sha256")
    ):
        raise SkillEvalError("repair changes differ from retained evidence")
    return result, files
