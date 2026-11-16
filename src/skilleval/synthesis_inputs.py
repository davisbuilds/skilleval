"""Explicit source selection and authoritative offline synthesis projections."""

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from skilleval import handoff, repair_assessment
from skilleval.accounting import PriceSchedule
from skilleval.contracts import Digest, Identifier, Record
from skilleval.errors import SkillEvalError
from skilleval.grading import _parse_json, _read_regular
from skilleval.review_results import SOURCE_LIMIT, Text

TOTAL_SOURCE_LIMIT = 64 * 1024 * 1024


class SourceSpec(Record):
    id: Identifier
    label: Text
    role: Literal[
        "request",
        "artifact",
        "context",
        "validation",
        "observation",
        "trace",
        "control",
        "prior-response",
        "other",
    ]
    path: Text
    sha256: Digest


class FileReference(Record):
    path: Text
    sha256: Digest


class AssessmentReference(Record):
    id: Identifier
    stage: Literal["specification", "implementation"]
    directory: Text
    sha256: Digest


class ArmSelection(Record):
    id: Identifier
    label: Text
    handoff_report_spec: FileReference
    assessments: tuple[AssessmentReference, ...] = Field(default=(), max_length=100)


class SelectionCoverage(Record):
    selection_policy: Text
    known_omissions: tuple[Text, ...] = Field(max_length=100)
    limitations: tuple[Text, ...] = Field(min_length=1, max_length=100)


class SynthesisSelection(Record):
    title: Text
    objective: Text
    sources: tuple[SourceSpec, ...] = Field(min_length=1, max_length=100)
    arms: tuple[ArmSelection, ...] = Field(min_length=1, max_length=20)
    coverage: SelectionCoverage
    price_schedule: PriceSchedule | None = None

    @model_validator(mode="after")
    def identities(self) -> "SynthesisSelection":
        if not any(source.role == "request" for source in self.sources):
            raise ValueError("selection requires an original request source")
        ids = [source.id for source in self.sources]
        if len(ids) != len(set(ids)) or any(name.startswith("derived-") for name in ids):
            raise ValueError("source IDs must be unique and cannot use the derived- prefix")
        arm_ids = [arm.id for arm in self.arms]
        if len(arm_ids) != len(set(arm_ids)):
            raise ValueError("duplicate arm IDs")
        generated: list[str] = []
        for arm in self.arms:
            generated.extend((f"derived-{arm.id}-accounting", f"derived-{arm.id}-selected-spec"))
            assessment_ids = [ref.id for ref in arm.assessments]
            if len(assessment_ids) != len(set(assessment_ids)):
                raise ValueError("duplicate assessment IDs")
            generated.extend(f"derived-{arm.id}-{ref.id}" for ref in arm.assessments)
        if len(generated) != len(set(generated)) or any(len(name) > 128 for name in generated):
            raise ValueError("generated source IDs collide or exceed identifier limit")
        if len(ids) + len(generated) > 100:
            raise ValueError("selection exceeds 100 sources including derived sources")
        return self


def load_selection(path: Path) -> SynthesisSelection:
    spec = SynthesisSelection.model_validate_json(_read_regular(path, SOURCE_LIMIT))
    data = spec.model_dump(mode="json")
    for source in data["sources"]:
        source["path"] = os.path.abspath(path.parent / source["path"])
    for arm in data["arms"]:
        ref = arm["handoff_report_spec"]
        ref["path"] = os.path.abspath(path.parent / ref["path"])
        for assessment in arm["assessments"]:
            assessment["directory"] = os.path.abspath(path.parent / assessment["directory"])
    return SynthesisSelection.model_validate_json(json.dumps(data))


def compile_selection(spec: SynthesisSelection) -> dict[str, Any]:
    """Read retained sources only; no VM, imported task code, or model dispatch."""
    payloads: dict[str, bytes] = {}
    sources: list[dict[str, Any]] = []
    arms: list[dict[str, Any]] = []
    total_bytes = 0

    def add_source(source_id: str, label: str, role: str, raw: bytes) -> None:
        nonlocal total_bytes
        if len(raw) > SOURCE_LIMIT:
            raise SkillEvalError(f"synthesis source {source_id} exceeds byte limit")
        total_bytes += len(raw)
        if total_bytes > TOTAL_SOURCE_LIMIT:
            raise SkillEvalError("synthesis sources exceed total byte limit")
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            raise SkillEvalError(f"synthesis source {source_id} must be UTF-8 text") from None
        path = f"case/sources/{source_id}.txt"
        payloads[path] = raw
        sources.append(
            {
                "id": source_id,
                "label": label,
                "role": role,
                "path": path,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
            }
        )

    for source in spec.sources:
        raw = _pinned(Path(source.path), source.sha256, f"synthesis source {source.id}")
        add_source(source.id, source.label, source.role, raw)

    assessment_cache: dict[
        tuple[str, str], tuple[dict[str, Any], repair_assessment.RepairAssessmentSpec]
    ] = {}
    for arm in spec.arms:
        ref = arm.handoff_report_spec
        report_path = Path(ref.path)
        raw = _pinned(report_path, ref.sha256, "handoff report specification")
        report_spec = _report_spec(raw, report_path.parent)
        report = handoff.report_handoff(report_spec)
        bundle = handoff.load_handoff(Path(report_spec.handoff), report_spec.handoff_sha256)
        lineage = bundle["lineage"]
        selected_spec_id = f"derived-{arm.id}-selected-spec"
        add_source(
            selected_spec_id,
            f"Arm {arm.id}: selected specification ({lineage['spec_path']})",
            "artifact",
            _pinned(
                Path(report_spec.handoff) / "input" / lineage["spec_path"],
                lineage["spec_sha256"],
                "selected specification artifact",
            ),
        )
        assessments = []
        for selected in arm.assessments:
            key = (selected.directory, selected.sha256)
            if key not in assessment_cache:
                assessment_cache[key] = repair_assessment.read_assessment(
                    Path(selected.directory), selected.sha256
                )
            record, assessment_spec = assessment_cache[key]
            _assessment_binding(selected, record, assessment_spec, bundle, report)
            provenance = {
                "id": selected.id,
                "stage": selected.stage,
                "record_sha256": selected.sha256,
                "result_id": record["result_id"],
                "packet_sha256": record["packet_sha256"],
                "task_sha256": record["task_sha256"],
                "repair_result_sha256": record["repair_result_sha256"],
                "candidate_sha256": record["candidate_sha256"],
            }
            projection = {
                "schema_version": 1,
                "kind": "skilleval-synthesis-assessment-source",
                "provenance": provenance,
                "assessment": record["assessment"],
                "task_quality": record["task_quality"],
                "scored_admitted": record["scored_admitted"],
                "judgment": _parse_json(
                    _pinned(
                        Path(selected.directory) / "result.json",
                        record["files"]["result.json"]["sha256"],
                        "selected judgment",
                    )
                ),
            }
            source_id = f"derived-{arm.id}-{selected.id}"
            add_source(
                source_id,
                f"Arm {arm.id}: selected {selected.stage} judgment",
                "validation",
                _json_bytes(projection),
            )
            assessments.append(
                {**provenance, "assessment": record["assessment"], "evidence_source_id": source_id}
            )
        accounting_id = f"derived-{arm.id}-accounting"
        display = {
            "status": _status(report, assessments),
            "usage": _usage(report),
            "cost": _cost(report),
            "timing": _timing(report),
        }
        add_source(
            accounting_id,
            f"Arm {arm.id}: canonical selected-stage accounting",
            "validation",
            _json_bytes(
                {
                    "canonical_display": display,
                    "schema_version": 1,
                    "kind": "skilleval-synthesis-accounting-source",
                    "handoff_report_spec_sha256": ref.sha256,
                    "selected_spec": {
                        "path": lineage["spec_path"],
                        "sha256": lineage["spec_sha256"],
                        "evidence_source_id": selected_spec_id,
                    },
                    "report": report,
                    "selected_assessments": assessments,
                }
            ),
        )
        arms.append(
            {
                "id": arm.id,
                "label": arm.label,
                **display,
                "evidence_source_id": accounting_id,
            }
        )
    return {
        "title": spec.title,
        "objective": spec.objective,
        "coverage": spec.coverage.model_dump(mode="json"),
        "price_schedule": (
            spec.price_schedule.model_dump(mode="json") if spec.price_schedule is not None else None
        ),
        "sources": sources,
        "payloads": payloads,
        "arms": arms,
    }


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")


def _pinned(path: Path, digest: str, label: str) -> bytes:
    raw = _read_regular(path, SOURCE_LIMIT)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise SkillEvalError(f"{label} digest differs")
    return raw


def _report_spec(raw: bytes, base: Path) -> handoff.HandoffReportSpec:
    spec = handoff.HandoffReportSpec.model_validate_json(raw)
    data = spec.model_dump(mode="json")
    data["handoff"] = os.path.abspath(base / data["handoff"])
    writer = data["writer_study"]
    for key in ("plan", "output"):
        writer[key] = os.path.abspath(base / writer[key])
    for row in (*data["implementation_runs"], *data["judge_runs"]):
        row["path"] = os.path.abspath(base / row["path"])
    return handoff.HandoffReportSpec.model_validate_json(json.dumps(data))


def _assessment_binding(
    selected: AssessmentReference,
    record: dict[str, Any],
    spec: repair_assessment.RepairAssessmentSpec,
    bundle: dict[str, Any],
    report: dict[str, Any],
) -> None:
    if selected.stage == "specification":
        lineage = bundle["lineage"]
        expected = {
            "task_sha256": lineage["task_sha256"],
            "repair_result_sha256": lineage["writer_result_sha256"],
            "candidate_sha256": lineage["writer_candidate_sha256"],
        }
        if any(record.get(key) != value for key, value in expected.items()):
            raise SkillEvalError("selected specification assessment differs from handoff writer")
        return
    task, result, _ = repair_assessment.source(spec)
    provenance = result.get("provenance", {})
    native_digest = provenance.get("native_receipt_sha256")
    attempts = report["implementation"]["attempts"]
    if (
        provenance.get("kind") != "native-repair"
        or native_digest is None
        or not any(row.get("native_receipt_sha256") == native_digest for row in attempts)
        or task.input_sha256 != bundle["input_sha256"]
        or task.prompt != bundle["prompt"]
    ):
        raise SkillEvalError("selected implementation assessment differs from handoff attempts")


def _status(report: dict[str, Any], assessments: list[dict[str, Any]]) -> str:
    states = (
        ", ".join(
            f"{row['stage']} {row['id']}: {row['assessment']['status']}" for row in assessments
        )
        or "No selected semantic assessment; unassessed"
    )
    implementation = (
        ", ".join(
            f"{key}={value}"
            for key, value in sorted(
                report["implementation"]["consumption"]["status_counts"].items()
            )
        )
        or "not selected"
    )
    selected = report["writer"]["selected_attempt"]
    selected_status = report["writer"]["attempts"][selected - 1]["status"]
    return (
        f"Writer primary: {report['writer']['primary_status']}; "
        f"selected writer attempt {selected}: {selected_status}; "
        f"selected implementation executions: {implementation}. {states}. "
        "Selected model judgments; independent audit pending; scored admission false. "
        "Scope: explicitly selected stage attempts; implementation primary is not inferred."
    )


def _ledgers(report: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        ("Writer", report["writer"]["consumption"]),
        ("Implementation", report["implementation"]["consumption"]),
        ("Combined agents", report["combined_agents"]),
        ("Judge overhead (separate)", report["judge_overhead"]["consumption"]),
    ]


def _aggregate(value: dict[str, Any], unit: str) -> str:
    def display(number: int | float | None) -> str:
        if number is None:
            return "Unknown"
        return f"{number:.3f}".rstrip("0").rstrip(".") if unit == "seconds" else str(number)

    if value["total"] is not None:
        return f"{display(value['total'])} {unit} total"
    subtotal = value["known_subtotal"]
    return (
        f"Unknown {unit} total; known subtotal "
        f"{display(subtotal)} {unit}; "
        f"{value['unknown_attempt_count']} attempt(s) unknown"
    )


def _usage(report: dict[str, Any]) -> list[str]:
    return [
        f"{label} ({ledger['attempt_count']} selected attempt(s)): "
        f"{_aggregate(ledger['tokens']['total_tokens'], 'tokens')}."
        for label, ledger in _ledgers(report)
    ]


def _cost(report: dict[str, Any]) -> list[str]:
    lines = []
    for label, ledger in _ledgers(report):
        cost = ledger["api_equivalent_cost"]
        total = cost["total_usd"]
        if total is None:
            groups = (
                ", ".join(
                    f"USD {row['known_subtotal_usd']} ({row['known_attempt_count']} attempt(s))"
                    for row in cost["schedule_groups"]
                )
                or "Unknown"
            )
            value = (
                f"Unknown total; known schedule-group subtotal(s): {groups}; "
                f"{cost['unknown_attempt_count']} attempt(s) unknown"
            )
        else:
            value = f"USD {total} total"
        lines.append(f"{label}: {value}. Conditional API-equivalent estimate; cash cost Unknown.")
    return lines


def _timing(report: dict[str, Any]) -> list[str]:
    return [
        f"{label}: {_aggregate(ledger['timings_seconds']['total_seconds'], 'seconds')} "
        "summed across selected attempts; not elapsed comparison wall time."
        for label, ledger in _ledgers(report)
    ]
