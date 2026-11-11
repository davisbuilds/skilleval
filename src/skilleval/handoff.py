"""Explicit frozen spec selection for a fresh, vanilla implementation stage.

Preparation copies bytes only. Artifact presence is not semantic acceptance.
Writer conversation, treatment metadata and lineage stay outside solver input;
free-form document text is preserved exactly and is not claimed to be blinded.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from skilleval import evidence, repair, runner
from skilleval.accounting import PriceSchedule, estimate_api_equivalent_cost
from skilleval.contracts import Digest, Record
from skilleval.efficiency import efficiency_ledger
from skilleval.errors import SkillEvalError
from skilleval.grading import _read_regular
from skilleval.identity import file_digest
from skilleval.judge import _native_audit, _run_files
from skilleval.study import _clean_native, _plan, report_study
from skilleval.treatments import check_task_baseline
from skilleval.workspace import _safe_path


class SpecHandoffSpec(Record):
    task: str = Field(min_length=1)
    task_sha256: Digest
    result: str = Field(min_length=1)
    result_sha256: Digest
    spec_path: str = Field(min_length=1)
    spec_sha256: Digest

    @field_validator("spec_path")
    @classmethod
    def selected_markdown(cls, value: str) -> str:
        name = PurePosixPath(value)
        if (
            _safe_path(value) != value
            or not value.startswith("docs/specs/")
            or name.suffix.lower() not in {".md", ".markdown"}
            or repair._directory_metadata(value)
        ):
            raise ValueError("selected spec must be Markdown below docs/specs")
        return value


def implementation_prompt(original_prompt: str, spec_path: str) -> str:
    """Preserve the original request as context while requesting implementation."""
    return (
        f"Implement the feature described in the specification at {spec_path}. "
        "Follow the repository conventions, verify the resulting behavior, and report "
        "what changed and any remaining limitations. Use your judgment for choices not "
        "prescribed, keeping the requested scope. If a material conflict prevents "
        "implementation, explain the blocker.\n\n"
        "Original specification-writing request (context; your task is to implement "
        f"the feature):\n{original_prompt}"
    )


def _normalized(spec: SpecHandoffSpec) -> SpecHandoffSpec:
    value = spec.model_dump(mode="json")
    for key in ("task", "result"):
        value[key] = os.path.abspath(value[key])
    return SpecHandoffSpec.model_validate_json(json.dumps(value))


def _writer(
    spec: SpecHandoffSpec,
) -> tuple[repair.RepairSpec, dict[str, Any], dict[str, Any], bytes]:
    task = repair.load_task(Path(spec.task), spec.task_sha256)
    result, files = repair.result_files(
        task, spec.task_sha256, Path(spec.result), spec.result_sha256
    )
    provenance = result.get("provenance")
    if (
        not isinstance(provenance, dict)
        or provenance.get("kind") != "native-repair"
        or not isinstance(provenance.get("native_run"), str)
    ):
        raise SkillEvalError("handoff requires a retained native writer attempt")
    run = Path(provenance["native_run"])
    # Reuse collection to bind the selected result to the actual native archive,
    # prompt, original baseline and recovery receipt. Result readback alone
    # validates candidate bytes but does not prove this native provenance.
    with tempfile.TemporaryDirectory(prefix="skilleval-handoff-writer-") as temporary:
        collected = repair.collect_repair(
            Path(spec.task), spec.task_sha256, run, Path(temporary).resolve() / "collected"
        )
        if collected["sha256"] != spec.result_sha256:
            raise SkillEvalError("selected writer result differs from retained native attempt")
    receipt, retained, _ = _run_files(run, mode="repair")
    if "native-audit.json" in retained:
        audit = evidence._parse_json(retained["native-audit.json"])
        if _limits(audit) != _limits(receipt):
            raise SkillEvalError("writer stage limits differ from retained native audit")
    if spec.spec_path not in result["changed_paths"]:
        raise SkillEvalError("selected spec must be an artifact changed by the writer attempt")
    selected = next((raw for name, raw, _ in files if name == spec.spec_path), None)
    if selected is None:
        raise SkillEvalError("selected spec artifact is missing from writer result")
    if hashlib.sha256(selected).hexdigest() != spec.spec_sha256:
        raise SkillEvalError("selected spec artifact digest differs")
    try:
        nonempty = bool(selected.decode("utf-8").strip())
    except UnicodeError:
        nonempty = False
    if not nonempty:
        raise SkillEvalError("selected spec must be a nonempty UTF-8 Markdown artifact")
    return task, result, receipt, selected


def _limits(receipt: dict[str, Any]) -> dict[str, Any]:
    limits = receipt.get("gateway_limits")
    timeout = receipt.get("native_timeout_seconds")
    if (
        not isinstance(limits, dict)
        or type(limits.get("max_requests")) is not int
        or limits["max_requests"] < 1
        or type(timeout) is not int
        or timeout < 1
        or not isinstance(receipt.get("model"), str)
        or not isinstance(receipt.get("effort"), str)
    ):
        raise SkillEvalError("writer attempt lacks explicit stage limits and model identity")
    return {
        "native_timeout_seconds": timeout,
        "max_requests": limits["max_requests"],
        "model": receipt["model"],
        "effort": receipt["effort"],
    }


def _lineage(
    spec: SpecHandoffSpec,
    task: repair.RepairSpec,
    result: dict[str, Any],
    receipt: dict[str, Any],
) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "task_sha256": spec.task_sha256,
        "original_input_sha256": task.input_sha256,
        "original_prompt_sha256": hashlib.sha256(task.prompt.encode()).hexdigest(),
        "source_url": task.source_url,
        "source_revision": task.source_revision,
        "writer_result_sha256": spec.result_sha256,
        "writer_candidate_sha256": result["candidate_sha256"],
        "writer_native_receipt_sha256": result["provenance"]["native_receipt_sha256"],
        "writer_execution_status": receipt["status"],
        "spec_path": spec.spec_path,
        "spec_sha256": spec.spec_sha256,
    }


def _input_files(
    task: repair.RepairSpec, spec: SpecHandoffSpec, selected: bytes
) -> list[tuple[str, bytes, bool]]:
    _, baseline = runner._snapshot_files(Path(task.input_bundle), task.input_sha256)
    files = {name: (raw, executable) for name, raw, executable in baseline}
    files[spec.spec_path] = (selected, False)
    return [(name, *value) for name, value in sorted(files.items())]


def prepare_handoff(spec: SpecHandoffSpec, destination: Path) -> dict[str, Any]:
    """Freeze original baseline plus one explicitly selected writer Markdown file."""
    spec = _normalized(spec)
    task, result, receipt, selected = _writer(spec)
    stage_limits = _limits(receipt)
    _, destination = evidence._paths(Path(task.input_bundle), destination)
    for dependency in (
        Path(spec.task),
        Path(spec.result),
        Path(result["provenance"]["native_run"]),
    ):
        if destination.is_relative_to(dependency) or dependency.is_relative_to(destination):
            raise SkillEvalError("handoff must stay outside retained writer evidence")
    with tempfile.TemporaryDirectory(prefix=".handoff-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        source = stage / "source"
        source.mkdir()
        writer = evidence._Writer(source)
        for name, raw, executable in _input_files(task, spec, selected):
            writer.add(name, raw, executable)
        check_task_baseline(source)
        captured = evidence.snapshot(source, stage / "input")
        shutil.rmtree(source)
        value = {
            "schema_version": 1,
            "kind": "skilleval-spec-handoff",
            "source_spec": spec.model_dump(mode="json"),
            "lineage": _lineage(spec, task, result, receipt),
            "input_sha256": captured["manifest_sha256"],
            "prompt": implementation_prompt(task.prompt, spec.spec_path),
            "stage_limits": stage_limits,
            "implementation_skill_policy": "absent",
            "semantic_acceptance": "unassessed",
            "scope": "development-only",
            "scored_admitted": False,
        }
        digest = repair._publish(stage / "handoff.json", value)
        # Recheck the external source immediately before atomic publication.
        _writer(spec)
        evidence._publish(stage, destination)
    return {
        "status": "prepared",
        "path": str(destination),
        "sha256": digest,
        "input_bundle": str(destination / "input"),
        "input_digest": captured["manifest_sha256"],
        "prompt": implementation_prompt(task.prompt, spec.spec_path),
        "stage_limits": stage_limits,
        "implementation_skill_policy": "absent",
        "semantic_acceptance": "unassessed",
    }


def load_handoff(path: Path, digest: str) -> dict[str, Any]:
    """Verify both retained lineage and exact implementer input before consumption."""
    raw = _read_regular(path / "handoff.json", 2 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise SkillEvalError("handoff digest differs")
    value = evidence._parse_json(raw)
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("kind") != "skilleval-spec-handoff"
    ):
        raise SkillEvalError("unsupported spec handoff")
    spec = SpecHandoffSpec.model_validate_json(json.dumps(value.get("source_spec")))
    task, result, receipt, selected = _writer(spec)
    if (
        value.get("lineage") != _lineage(spec, task, result, receipt)
        or value.get("stage_limits") != _limits(receipt)
        or value.get("prompt") != implementation_prompt(task.prompt, spec.spec_path)
        or value.get("implementation_skill_policy") != "absent"
        or value.get("semantic_acceptance") != "unassessed"
        or value.get("scope") != "development-only"
        or value.get("scored_admitted") is not False
    ):
        raise SkillEvalError("handoff lineage, prompt or stage policy differs")
    _, files = runner._snapshot_files(path / "input", value["input_sha256"])
    # Snapshot traversal groups directories; compare globally sorted path/bytes/mode tuples.
    if sorted(files) != _input_files(task, spec, selected):
        raise SkillEvalError("handoff input differs from original baseline plus selected spec")
    check_task_baseline(path / "input")
    return value


def check_implementation(path: Path, digest: str, run: Path) -> dict[str, Any]:
    """Bind a selected fresh implementation run to its frozen vanilla input."""
    value = load_handoff(path, digest)
    receipt, retained, _ = _run_files(run, mode="repair")
    audit = (
        evidence._parse_json(retained["native-audit.json"])
        if "native-audit.json" in retained
        else receipt
    )
    if (
        receipt.get("input_digest") != value["input_sha256"]
        or retained.get("prompt.txt") != value["prompt"].encode()
    ):
        raise SkillEvalError("implementation run differs from handoff input or prompt")
    absent = {"arm": "absent", "skill_bundle": None}
    if receipt.get("skill_treatment") != absent or audit.get("skill_treatment") != absent:
        raise SkillEvalError("implementation stage requires absent selected skill")
    if _limits(receipt) != value["stage_limits"] or _limits(audit) != value["stage_limits"]:
        raise SkillEvalError("implementation stage limits differ from writer stage")
    if not (run / "recovery.json").is_file() or not _clean_native(run.absolute()):
        raise SkillEvalError("implementation stage requires completed native cleanup")
    return {
        "handoff_sha256": digest,
        "native_run": str(run.absolute()),
        "native_receipt_sha256": file_digest(run / "native-run.json"),
        "status": receipt["status"],
        "receipt": receipt,
        "semantic_acceptance": "unassessed",
    }


class WriterStudyReference(Record):
    plan: str = Field(min_length=1)
    plan_sha256: Digest
    output: str = Field(min_length=1)
    cell_id: str = Field(min_length=1)


class NativeRunReference(Record):
    path: str = Field(min_length=1)
    sha256: Digest | None = None


class HandoffReportSpec(Record):
    handoff: str = Field(min_length=1)
    handoff_sha256: Digest
    writer_study: WriterStudyReference
    implementation_runs: tuple[NativeRunReference, ...] = Field(max_length=100)
    judge_runs: tuple[NativeRunReference, ...] = Field(default=(), max_length=100)
    price_schedule: PriceSchedule | None = None

    @model_validator(mode="after")
    def distinct_runs(self) -> HandoffReportSpec:
        paths = [os.path.abspath(row.path) for row in (*self.implementation_runs, *self.judge_runs)]
        if len(paths) != len(set(paths)):
            raise ValueError("select each native run only once")
        return self


def _consumption(
    reference: NativeRunReference,
    schedule: PriceSchedule | None,
    *,
    handoff: dict[str, Any] | None,
) -> dict[str, Any]:
    run = Path(os.path.abspath(reference.path))
    receipt_path = run / "native-run.json"
    if reference.sha256 is None:
        if os.path.lexists(receipt_path):
            raise SkillEvalError("selected native receipt requires its retained digest")
        return {
            "path": str(run),
            "status": "unfinished",
            "cleanup_complete": False,
            "binding": "unverified-no-native-receipt",
            "usage_accounting": None,
            "cost": None,
        }
    raw = _read_regular(receipt_path, 16 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != reference.sha256:
        raise SkillEvalError("selected consumption receipt digest differs")
    receipt = evidence._parse_json(raw)
    mode: Literal["repair", "judge"] = "repair" if handoff is not None else "judge"
    if (
        not isinstance(receipt, dict)
        or receipt.get("kind") != "skilleval-native-run"
        or receipt.get("mode") != mode
        or receipt.get("status") not in {"completed", "failed", "timed-out", "partial"}
    ):
        raise SkillEvalError("invalid selected consumption native receipt")
    audit_raw = _native_audit(run, receipt)
    audit = evidence._parse_json(audit_raw) if audit_raw is not None else receipt
    model = receipt.get("model")
    if not isinstance(model, str):
        raise SkillEvalError("native consumption lacks model identity")
    if audit.get("metadata_complete") is False:
        # The supported overflow receipt deliberately omits stage limits and
        # treatment metadata. Keep it as an unverified attempt, without claiming
        # that its input or budget satisfies the implementation-stage contract.
        return {
            "path": str(run),
            "native_receipt_sha256": reference.sha256,
            "status": receipt["status"],
            "cleanup_complete": (run / "recovery.json").is_file() and _clean_native(run),
            "binding": "unverified-incomplete-native-metadata",
            "artifacts_verified": False,
            "evidence_status": "incomplete-native-metadata",
            "audit_overflow": receipt["audit_overflow"],
            "usage_accounting": None,
            "reported_usage_accounting": receipt.get("usage_accounting"),
            "timings": None,
            "request_count": None,
            "cost": estimate_api_equivalent_cost(
                {"model": model, "status": "unknown"},
                schedule if schedule is None or schedule.model == model else None,
                model=model,
            ),
        }
    if handoff is not None:
        if (
            receipt.get("input_digest") != handoff["input_sha256"]
            or receipt.get("prompt_sha256")
            != hashlib.sha256(handoff["prompt"].encode()).hexdigest()
            or _limits(receipt) != handoff["stage_limits"]
            or _limits(audit) != handoff["stage_limits"]
        ):
            raise SkillEvalError(
                "implementation consumption differs from handoff input, brief or limits"
            )
        absent = {"arm": "absent", "skill_bundle": None}
        for metadata in (receipt, audit):
            treatment = metadata.get("skill_treatment")
            if treatment != absent and not (treatment is None and receipt["status"] != "completed"):
                raise SkillEvalError("implementation consumption requires absent selected skill")
    # Incomplete attempts may fail before any archive exists. Keep those rows and
    # their unknown usage; complete evidence always takes the full archive route.
    artifacts_verified = False
    evidence_status = "incomplete-no-artifacts"
    if receipt.get("artifacts") is not None or receipt["status"] == "completed":
        try:
            _run_files(run, mode=mode)
            artifacts_verified = True
            evidence_status = "verified-artifacts"
        except SkillEvalError:
            evidence_status = "invalid-artifacts"
    usage = receipt.get("usage_accounting") if evidence_status != "invalid-artifacts" else None
    return {
        "path": str(run),
        "native_receipt_sha256": reference.sha256,
        "status": receipt["status"],
        "cleanup_complete": (run / "recovery.json").is_file() and _clean_native(run),
        "binding": "verified-native-receipt",
        "artifacts_verified": artifacts_verified,
        "evidence_status": evidence_status,
        "usage_accounting": usage,
        "reported_usage_accounting": receipt.get("usage_accounting"),
        "timings": receipt.get("timings"),
        "request_count": receipt.get("request_count"),
        "cost": estimate_api_equivalent_cost(
            usage or {"model": model, "status": "unknown"},
            schedule if schedule is None or schedule.model == model else None,
            model=model,
        ),
    }


def _ledger(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    # Reuse the existing all-attempt accounting without reinterpretation of
    # native cumulative usage or treating an absent observation as zero.
    result: dict[str, Any] = efficiency_ledger([{"arm": "absent", "attempts": attempts}])["arms"][
        "absent"
    ]
    return result


def report_handoff(spec: HandoffReportSpec) -> dict[str, Any]:
    """Replay selected retained evidence; never launch a VM, agent or judge."""
    value = load_handoff(Path(spec.handoff), spec.handoff_sha256)
    reference = spec.writer_study
    study_report = report_study(Path(reference.plan), reference.plan_sha256, Path(reference.output))
    cell = next((row for row in study_report["cells"] if row["cell_id"] == reference.cell_id), None)
    if cell is None:
        raise SkillEvalError("selected writer study cell is missing")
    _, study_spec = _plan(Path(reference.plan), reference.plan_sha256)
    task = next(row for row in study_spec.tasks if row.task_id == cell["task_id"])
    lineage = value["lineage"]
    if (
        task.mode != "repair"
        or task.task_id != lineage["task_id"]
        or task.input_sha256 != lineage["original_input_sha256"]
        or hashlib.sha256(task.prompt.encode()).hexdigest() != lineage["original_prompt_sha256"]
    ):
        raise SkillEvalError(
            "selected writer study differs from handoff original baseline or brief"
        )
    selected = [
        index
        for index, row in enumerate(cell["attempts"], 1)
        if row.get("native_receipt_sha256") == lineage["writer_native_receipt_sha256"]
    ]
    if len(selected) != 1:
        raise SkillEvalError(
            "handoff writer attempt is absent or duplicated in selected study cell"
        )
    writer_attempts = cell["attempts"]
    implementations = [
        _consumption(row, spec.price_schedule, handoff=value) for row in spec.implementation_runs
    ]
    judges = [_consumption(row, spec.price_schedule, handoff=None) for row in spec.judge_runs]
    return {
        "schema_version": 1,
        "kind": "skilleval-spec-handoff-report",
        "scope": "explicit-selected-stage-attempts",
        "handoff_sha256": spec.handoff_sha256,
        "source_spec": spec.model_dump(mode="json"),
        "writer": {
            "selected_attempt": selected[0],
            "primary_attempt": 1,
            "primary_status": cell["primary_status"],
            "attempts": writer_attempts,
            "consumption": _ledger(writer_attempts),
        },
        "implementation": {
            "attempts": implementations,
            "primary_attempt": None,
            "primary_policy": "not-inferred-from-selection-order",
            "consumption": _ledger(implementations),
        },
        "combined_agents": _ledger([*writer_attempts, *implementations]),
        "judge_overhead": {"attempts": judges, "consumption": _ledger(judges)},
        "semantic_acceptance": "unassessed",
        "scored_admitted": False,
        "limits": [
            "Writer retries never replace its first attempt as primary.",
            "Implementation and judge selections do not enumerate all external attempts.",
            "Missing receipts retain unfinished rows with unknown usage and unverified lineage.",
            "Judge overhead is separate; selecting spend does not validate a judgment.",
            "Combined costs require complete estimates with identical price schedules.",
        ],
    }
