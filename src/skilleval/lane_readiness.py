"""Flat, immutable execution sources for independently reusable environment checks."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from skilleval import evidence
from skilleval.errors import SkillEvalError
from skilleval.grading import _read_regular
from skilleval.identity import environment_identity, object_digest, snapshot_identity
from skilleval.task_package import (
    MAX_VALIDATION_BYTES,
    TaskPackageSpec,
    ValidationCase,
    ValidationLane,
    _profile_matches,
    compare_lane,
    execution_contract,
    load_package,
    read_lane,
    read_package,
)


def lane_contract(
    spec: TaskPackageSpec, case: ValidationCase, lane: ValidationLane
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "validation_isolation": "lane-v1",
        "image_sha256": spec.environment.image_sha256,
        "profile": spec.environment.profile,
        "case_id": case.case_id,
        "role": case.role,
        "input_sha256": case.input_sha256,
        "lane_id": lane.lane_id,
        "argv": list(lane.argv),
        "timeout_seconds": lane.timeout_seconds,
    }


def execution_receipt(
    spec: TaskPackageSpec,
    package: Path,
    digest: str,
    case: ValidationCase,
    lane: ValidationLane,
    row: dict[str, Any],
) -> dict[str, Any]:
    if spec.environment.validation_isolation != "lane-v1":
        raise SkillEvalError("independent execution requires validation_isolation lane-v1")
    contract = lane_contract(spec, case, lane)
    return {
        "schema_version": 1,
        "kind": "skilleval-task-validation-lane",
        "package": str(package.absolute()),
        "package_sha256": digest,
        "execution_contract": contract,
        "execution_contract_sha256": object_digest(contract),
        "case": row,
    }


def _read(path: Path, digest: object = None) -> dict[str, Any]:
    raw = _read_regular(path, MAX_VALIDATION_BYTES)
    if digest is not None and hashlib.sha256(raw).hexdigest() != digest:
        raise SkillEvalError("validation evidence digest differs")
    value = evidence._parse_json(raw)
    if not isinstance(value, dict):
        raise SkillEvalError("validation evidence must be an object")
    return value


def _original(value: dict[str, Any]) -> TaskPackageSpec:
    locator, digest = value.get("package"), value.get("package_sha256")
    if (
        not isinstance(locator, str)
        or not Path(locator).is_absolute()
        or not isinstance(digest, str)
    ):
        raise SkillEvalError("validation original package binding missing")
    spec = read_package(Path(locator), digest)
    if spec.environment.validation_isolation != "lane-v1":
        raise SkillEvalError("independent evidence requires validation_isolation lane-v1")
    return spec


def read_execution(
    source: object,
) -> tuple[TaskPackageSpec, ValidationCase, ValidationLane, dict[str, Any]]:
    """Check the original producer and bytes, allowing honest incomplete diagnostics."""
    from skilleval.study import _clean_native

    if (
        not isinstance(source, dict)
        or set(source) != {"receipt", "sha256"}
        or not isinstance(source["receipt"], str)
        or not Path(source["receipt"]).is_absolute()
        or not isinstance(source["sha256"], str)
    ):
        raise SkillEvalError("invalid lane execution source")
    path = Path(source["receipt"])
    value = _read(path, source["sha256"])
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or value.get("kind") != "skilleval-task-validation-lane"
        or not isinstance(value.get("execution_contract"), dict)
    ):
        raise SkillEvalError("unsupported lane execution receipt")
    spec = _original(value)
    contract = value["execution_contract"]
    pairs = [
        (case, lane)
        for case in spec.validation
        for lane in case.lanes
        if case.case_id == contract.get("case_id") and lane.lane_id == contract.get("lane_id")
    ]
    if len(pairs) != 1:
        raise SkillEvalError("lane execution case or lane missing")
    case, lane = pairs[0]
    if contract != lane_contract(spec, case, lane) or value.get(
        "execution_contract_sha256"
    ) != object_digest(contract):
        raise SkillEvalError("lane execution contract differs")
    row = value.get("case")
    if (
        not isinstance(row, dict)
        or row.get("case_id") != case.case_id
        or row.get("role") != case.role
        or row.get("input_sha256") != case.input_sha256
        or row.get("status") not in {"matched", "unexpected", "incomplete"}
        or type(row.get("cleanup_complete")) is not bool
        or not isinstance(row.get("lanes"), list)
        or len(row["lanes"]) > 1
    ):
        raise SkillEvalError("lane execution case binding differs")
    directory = path.parent
    saved = directory / "validation.json"
    if saved.exists() and _read(saved) != row:
        raise SkillEvalError("lane execution saved case differs")
    if row.get("identity") is not None:
        recovery = _read(directory / "recovery.json")
        if (
            not saved.exists()
            or row["identity"] != recovery.get("identity")
            or row.get("image") != recovery.get("image")
            or not isinstance(row.get("image"), dict)
            or row["image"].get("sha256") != spec.environment.image_sha256
            or row.get("readiness_identity")
            != environment_identity(row["identity"], spec.environment.profile, isolation="lane-v1")
        ):
            raise SkillEvalError("lane execution identity or image binding differs")
    elif row["status"] != "incomplete" or row["lanes"] or row["cleanup_complete"]:
        raise SkillEvalError("lane execution identity missing")
    if row["cleanup_complete"] and not _clean_native(directory):
        raise SkillEvalError("lane execution cleanup binding differs")
    if row["status"] != "incomplete" and (not row["cleanup_complete"] or len(row["lanes"]) != 1):
        raise SkillEvalError("lane execution completion evidence missing")
    if row["lanes"]:
        observed = row["lanes"][0]
        if (
            not isinstance(observed, dict)
            or observed.get("lane_id") != lane.lane_id
            or observed.get("expected") != lane.expected
            or observed.get("expected_exit_code") != lane.expected_exit_code
            or not isinstance(observed.get("process"), dict)
            or observed["process"] != read_lane(directory / f"lane-{lane.lane_id}.json")
        ):
            raise SkillEvalError("lane execution original observation differs")
    return spec, case, lane, row


def _complete(spec: TaskPackageSpec, lane: ValidationLane, row: dict[str, Any]) -> bool:
    return (
        row["status"] in {"matched", "unexpected"}
        and row["cleanup_complete"] is True
        and len(row["lanes"]) == 1
        and complete_process(spec, lane, row["lanes"][0]["process"])
    )


def complete_process(spec: TaskPackageSpec, lane: ValidationLane, process: dict[str, Any]) -> bool:
    """Physical execution completeness is independent of the selected parser."""
    return (
        process.get("timed_out") is False
        and process.get("output_limit_reached") is False
        and type(process.get("returncode")) is int
        and process.get("argv") == list(lane.argv)
        and _profile_matches(process, spec.environment.profile)
        and process.get("source_changes") == []
    )


def _comparison(spec: TaskPackageSpec, lane: ValidationLane, row: dict[str, Any]) -> dict[str, Any]:
    process = row["lanes"][0]["process"] if row["lanes"] else {}
    return compare_lane(lane, process, spec.environment.profile)


def _case_status(case: ValidationCase, lanes: list[dict[str, Any]]) -> str:
    return (
        "matched"
        if len(lanes) == len(case.lanes)
        and all(
            lane["execution_complete"] and lane["comparison"]["status"] == "matched"
            for lane in lanes
        )
        else "incomplete"
    )


def validation_report(
    spec: TaskPackageSpec,
    package: Path,
    digest: str,
    selected: list[dict[str, Any]],
) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for case in spec.validation:
        lanes = []
        for lane in case.lanes:
            items = [
                i for i in selected if (i["case_id"], i["lane_id"]) == (case.case_id, lane.lane_id)
            ]
            if not items:
                continue
            if len(items) != 1:
                raise SkillEvalError("duplicate selected lane")
            item = items[0]
            original, old_case, old_lane, row = read_execution(item["source"])
            if lane_contract(spec, case, lane) != lane_contract(original, old_case, old_lane):
                raise SkillEvalError("selected lane execution contract differs")
            lanes.append(
                {
                    "lane_id": lane.lane_id,
                    "action": item["action"],
                    "reason": item["reason"],
                    "source": item["source"],
                    "comparison": _comparison(spec, lane, row),
                    "readiness_identity": row.get("readiness_identity"),
                    "execution_complete": _complete(spec, lane, row),
                }
            )
        cases.append(
            {
                "case_id": case.case_id,
                "role": case.role,
                "input_sha256": case.input_sha256,
                "status": _case_status(case, lanes),
                "cleanup_complete": len(lanes) == len(case.lanes)
                and all(read_execution(lane["source"])[3]["cleanup_complete"] for lane in lanes),
                "lanes": lanes,
            }
        )
    contract = execution_contract(spec)
    return {
        "schema_version": 3,
        "kind": "skilleval-task-validation",
        "package": str(package.absolute()),
        "package_sha256": digest,
        "execution_contract": contract,
        "execution_contract_sha256": object_digest(contract),
        "family": spec.family,
        "cases": cases,
        "status": "matched" if all(c["status"] == "matched" for c in cases) else "incomplete",
        "coverage_gaps": list(spec.environment.coverage_gaps),
        "limitations": list(spec.limitations),
        "inference_requests": 0,
        "scored_admitted": False,
    }


def read_report(path: Path, digest: str) -> tuple[TaskPackageSpec, dict[str, Any]]:
    """Verify flat sources and recorded interpretation, without replaying an old parser."""
    value = _read(path, digest)
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != 3
        or value.get("kind") != "skilleval-task-validation"
    ):
        raise SkillEvalError("selective reuse requires an independent schema-3 validation report")
    spec = _original(value)
    contract = execution_contract(spec)
    if (
        value.get("execution_contract") != contract
        or value.get("execution_contract_sha256") != object_digest(contract)
        or value.get("family") != spec.family
        or value.get("coverage_gaps") != list(spec.environment.coverage_gaps)
        or value.get("limitations") != list(spec.limitations)
        or value.get("scored_admitted") is not False
        or value.get("inference_requests") != 0
        or not isinstance(value.get("cases"), list)
        or len(value["cases"]) != len(spec.validation)
    ):
        raise SkillEvalError("independent validation package binding differs")
    for case, entry in zip(spec.validation, value["cases"], strict=True):
        if (
            not isinstance(entry, dict)
            or entry.get("case_id") != case.case_id
            or entry.get("role") != case.role
            or entry.get("input_sha256") != case.input_sha256
            or not isinstance(entry.get("lanes"), list)
        ):
            raise SkillEvalError("independent validation case binding differs")
        seen = set()
        cleanup = True
        for item in entry["lanes"]:
            if not isinstance(item, dict) or not isinstance(item.get("lane_id"), str):
                raise SkillEvalError("independent validation lane binding differs")
            matches = [lane for lane in case.lanes if lane.lane_id == item["lane_id"]]
            if len(matches) != 1 or item["lane_id"] in seen:
                raise SkillEvalError("unknown or duplicate independent validation lane")
            seen.add(item["lane_id"])
            lane = matches[0]
            original, old_case, old_lane, row = read_execution(item.get("source"))
            comparison = item.get("comparison")
            process = row["lanes"][0]["process"] if row["lanes"] else {}
            if (
                lane_contract(spec, case, lane) != lane_contract(original, old_case, old_lane)
                or item.get("action") not in {"execute", "reuse"}
                or not isinstance(item.get("reason"), str)
                or not item["reason"]
                or item.get("readiness_identity") != row.get("readiness_identity")
                or item.get("execution_complete") is not _complete(spec, lane, row)
                or not isinstance(comparison, dict)
                or comparison.get("lane_id") != lane.lane_id
                or comparison.get("expected") != lane.expected
                or comparison.get("expected_exit_code") != lane.expected_exit_code
                or comparison.get("process") != process
                or comparison.get("status") not in {"matched", "unexpected", "incomplete"}
            ):
                raise SkillEvalError("independent validation observation binding differs")
            cleanup = cleanup and row["cleanup_complete"]
        if entry.get("status") != _case_status(case, entry["lanes"]) or entry.get(
            "cleanup_complete"
        ) is not (cleanup and len(seen) == len(case.lanes)):
            raise SkillEvalError("independent validation completion binding differs")
    status = "matched" if all(c["status"] == "matched" for c in value["cases"]) else "incomplete"
    if value.get("status") != status:
        raise SkillEvalError("independent validation status differs")
    return spec, value


def plan_validation(
    package: Path,
    digest: str,
    runtime: Path,
    reuse: Path | None = None,
    reuse_sha256: str | None = None,
) -> tuple[TaskPackageSpec, list[dict[str, Any]]]:
    spec = load_package(package, digest)
    if spec.environment.validation_isolation != "lane-v1":
        raise SkillEvalError("selective validation requires validation_isolation lane-v1")
    if (reuse is None) != (reuse_sha256 is None):
        raise SkillEvalError("--reuse and --reuse-sha256 must be supplied together")
    candidates = {}
    if reuse is not None and reuse_sha256 is not None:
        _, previous = read_report(reuse, reuse_sha256)
        candidates = {
            (case["case_id"], lane["lane_id"]): lane
            for case in previous["cases"]
            for lane in case["lanes"]
        }
    current = environment_identity(
        snapshot_identity(str(runtime)), spec.environment.profile, isolation="lane-v1"
    )
    plan = []
    for case in spec.validation:
        for lane in case.lanes:
            source = None
            reason = "no prior execution"
            candidate = candidates.get((case.case_id, lane.lane_id))
            if candidate is not None:
                original, old_case, old_lane, row = read_execution(candidate["source"])
                if lane_contract(original, old_case, old_lane) != lane_contract(spec, case, lane):
                    reason = "execution conditions changed"
                elif row.get("readiness_identity") != current:
                    reason = "execution identity changed"
                elif not _complete(spec, lane, row):
                    reason = "prior execution incomplete or changed source"
                else:
                    reason, source = "compatible retained execution", candidate["source"]
            plan.append(
                {
                    "case_id": case.case_id,
                    "lane_id": lane.lane_id,
                    "action": "reuse" if source else "execute",
                    "reason": reason,
                    "source": source,
                }
            )
    return spec, plan


def checked_report(spec: TaskPackageSpec, path: Path, digest: str) -> dict[str, Any]:
    original, report = read_report(path, digest)
    if execution_contract(spec) != execution_contract(original):
        raise SkillEvalError("task environment execution contract changed; run new validation")
    for case, row in zip(spec.validation, report["cases"], strict=True):
        if len(row["lanes"]) != len(case.lanes):
            raise SkillEvalError("task validation missing independent lanes")
        lanes = {item["lane_id"]: item for item in row["lanes"]}
        for lane in case.lanes:
            item = lanes[lane.lane_id]
            if (
                not item["execution_complete"]
                or compare_lane(lane, item["comparison"]["process"], spec.environment.profile)[
                    "status"
                ]
                != "matched"
            ):
                raise SkillEvalError(
                    "task validation observations do not match selected expectations"
                )
    return report


def selection_summary(
    spec: TaskPackageSpec,
    selected_digest: str,
    report: dict[str, Any],
) -> dict[str, Any]:
    sources = []
    for case in report["cases"]:
        for item in case["lanes"]:
            original, _, _, row = read_execution(item["source"])
            sources.append(
                {
                    "case_id": case["case_id"],
                    "lane_id": item["lane_id"],
                    "source": item["source"],
                    "action": item["action"],
                    "original_status": row["status"],
                    "original_coverage_gaps": list(original.environment.coverage_gaps),
                    "original_limitations": list(original.limitations),
                }
            )
    return {
        "policy": "task-environment-lanes-v1",
        "original_package": report["package"],
        "original_package_sha256": report["package_sha256"],
        "selected_package_sha256": selected_digest,
        "original_status": report["status"],
        "selected_expectations_status": "matched",
        "sources": sources,
        "original_coverage_gaps": report["coverage_gaps"],
        "original_limitations": report["limitations"],
        "selected_coverage_gaps": list(spec.environment.coverage_gaps),
        "selected_limitations": list(spec.limitations),
    }
