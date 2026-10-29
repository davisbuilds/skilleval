"""Explicit repair assessment selection joined to immutable study attempts."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from skilleval.contracts import Digest, Identifier, Record
from skilleval.errors import SkillEvalError
from skilleval.grading import _read_regular
from skilleval.identity import file_digest
from skilleval.repair_assessment import RepairCheckSource, checked_report, read_assessment, source


class AssessmentReference(Record):
    path: str = Field(min_length=1)
    sha256: Digest


class RepairOutcomeEntry(Record):
    cell_id: Identifier
    attempt: int = Field(ge=1)
    checks: tuple[RepairCheckSource, ...] = Field(min_length=1, max_length=16)
    assessment: AssessmentReference | None = None

    @model_validator(mode="after")
    def unique_checks(self) -> RepairOutcomeEntry:
        ids = [(c.task_sha256, c.result_sha256, c.report_sha256) for c in self.checks]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate repair check selection")
        return self


class RepairOutcomeSelection(Record):
    study_plan_sha256: Digest
    entries: tuple[RepairOutcomeEntry, ...] = Field(max_length=10000)

    @model_validator(mode="after")
    def unique_attempts(self) -> RepairOutcomeSelection:
        ids = [(e.cell_id, e.attempt) for e in self.entries]
        if len(set(ids)) != len(ids):
            raise ValueError("select each attempt only once")
        return self


def attach_outcomes(
    report: dict[str, Any], plan_digest: str, selection_path: Path, selection_digest: str
) -> dict[str, Any]:
    raw = _read_regular(selection_path, 16 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != selection_digest:
        raise SkillEvalError("repair outcome selection digest differs")
    selected = RepairOutcomeSelection.model_validate_json(raw)
    if selected.study_plan_sha256 != plan_digest:
        raise SkillEvalError("repair selection belongs to another study")
    cells = {cell["cell_id"]: cell for cell in report["cells"]}
    rows = {}
    for entry in selected.entries:
        cell = cells.get(entry.cell_id)
        if cell is None or entry.attempt > len(cell["attempts"]):
            raise SkillEvalError("repair selection references an unknown attempt")
        attempt = cell["attempts"][entry.attempt - 1]
        native = Path(attempt["path"]) / "native"
        native_digest = attempt.get("native_receipt_sha256")
        if native_digest is None or file_digest(native / "native-run.json") != native_digest:
            raise SkillEvalError("repair outcome requires bound native attempt evidence")
        checks = []
        for check in entry.checks:
            _, result, _ = source(check)
            origin = result["provenance"]
            if (
                origin.get("kind") != "native-repair"
                or origin.get("native_receipt_sha256") != native_digest
                or Path(origin["native_run"]).absolute() != native.absolute()
            ):
                raise SkillEvalError("repair result belongs to another attempt")
            observed = checked_report(check)
            checks.append(
                {
                    "task_sha256": check.task_sha256,
                    "repair_result_sha256": check.result_sha256,
                    "candidate_sha256": result["candidate_sha256"],
                    "report_sha256": check.report_sha256,
                    "status": observed["status"],
                    "accepted_by_checks": observed["accepted_by_checks"],
                    "probe_count": len(observed["checks"]),
                }
            )
        if len({c["candidate_sha256"] for c in checks}) != 1:
            raise SkillEvalError("selected checks disagree about candidate identity")
        semantic: dict[str, Any] = {"status": "unassessed", "independent_audit": "required-pending"}
        judge_usage = None
        if entry.assessment is not None:
            record, spec = read_assessment(Path(entry.assessment.path), entry.assessment.sha256)
            _, result, _ = source(spec)
            origin = result["provenance"]
            if (
                origin.get("native_receipt_sha256") != native_digest
                or record["candidate_sha256"] != checks[0]["candidate_sha256"]
            ):
                raise SkillEvalError("semantic assessment belongs to another attempt")
            expected_checks = {
                (c.task_sha256, c.result_sha256, c.report_sha256) for c in entry.checks
            }
            assessed_checks = {
                (c.task_sha256, c.result_sha256, c.report_sha256) for c in spec.checks
            }
            if expected_checks != assessed_checks:
                raise SkillEvalError("semantic assessment used a different check selection")
            semantic = record["assessment"] | {
                "result_id": record["result_id"],
                "record_sha256": entry.assessment.sha256,
            }
            from skilleval.study import _document

            judge_native = _document(Path(entry.assessment.path) / "native-run.json")
            judge_usage = {
                k: judge_native.get(k) for k in ("usage_accounting", "timings", "request_count")
            }
        rows[(entry.cell_id, entry.attempt)] = {
            "checks": checks,
            "semantic": semantic,
            "judge_consumption": judge_usage,
            "task_quality": "independent-audit-pending",
            "scored_admitted": False,
        }
    # Include every attempt and pair, even if no repair evidence was selected.
    for cell in report["cells"]:
        for number, attempt in enumerate(cell["attempts"], 1):
            attempt["repair_outcome"] = rows.get((cell["cell_id"], number))
        cell["primary_repair_outcome"] = (
            rows.get((cell["cell_id"], 1)) if cell["primary"] is not None else None
        )
    for pair in report["pairs"]:
        pair["repair_outcomes"] = {
            cell["arm"]: cell["primary_repair_outcome"]
            for cell in report["cells"]
            if cell["pair_id"] == pair["pair_id"]
        }
    report["repair_selection"] = {
        "sha256": selection_digest,
        "selected_attempts": len(rows),
        "policy": "explicit-pinned-selection; no automatic latest or favorable result",
        "independent_audit": "required-pending",
    }
    report["limits"].append(
        "Repair checks and semantic judgments remain separate; "
        "neither grants audited correctness or skill benefit."
    )
    return report
