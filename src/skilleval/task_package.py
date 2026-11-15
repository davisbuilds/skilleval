"""Task packages bind artifact contracts to explicit, offline environment checks."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from skilleval import evidence, repair, runner, solver_boundary
from skilleval.contracts import Citation, Digest, Identifier, Record
from skilleval.errors import SkillEvalError
from skilleval.grading import _read_regular
from skilleval.identity import environment_identity, object_digest
from skilleval.qualification import _image_archive

# Two 1 MiB log streams can expand sixfold when encoded as JSON. A package
# has at most eight lanes; envelope and expected-observation space is additional.
MAX_LANE_BYTES = 16 * 1024 * 1024
MAX_VALIDATION_BYTES = 160 * 1024 * 1024
ExecutionProfile = Literal["native-root-v1", "solver-isolated-v1"]


def read_lane(path: Path) -> dict[str, Any]:
    value = evidence._parse_json(_read_regular(path, MAX_LANE_BYTES))
    if not isinstance(value, dict):
        raise SkillEvalError("validation observation must be an object")
    return value


class ValidationLane(Record):
    lane_id: Identifier
    purpose: str = Field(min_length=1)
    argv: tuple[str, ...] = Field(min_length=1, max_length=64)
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    parser: Literal["json", "unittest"]
    expected_exit_code: int = Field(ge=0, le=255)
    expected: dict[str, Any]


class ValidationCase(Record):
    case_id: Identifier
    role: Literal["baseline", "reference"]
    input_bundle: str = Field(min_length=1)
    input_sha256: Digest
    lanes: tuple[ValidationLane, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def unique_lanes(self) -> ValidationCase:
        if len({x.lane_id for x in self.lanes}) != len(self.lanes):
            raise ValueError("duplicate validation lane")
        return self


class TaskEnvironment(Record):
    image_archive: str = Field(min_length=1)
    image_sha256: Digest
    profile: ExecutionProfile = "native-root-v1"
    validation_isolation: Literal["case-v1", "lane-v1"] = Field(
        default="case-v1", exclude_if=lambda value: value == "case-v1"
    )
    setup: tuple[str, ...] = Field(min_length=1, max_length=32)
    required_paths: tuple[str, ...] = Field(min_length=1, max_length=100)
    coverage_gaps: tuple[str, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def safe_paths(self) -> TaskEnvironment:
        for name in self.required_paths:
            Citation(path=name, line_start=1, line_end=1)
        return self


class TaskPackageSpec(Record):
    package_id: Identifier
    family: Literal["repair", "document"]
    task: str = Field(min_length=1)
    task_sha256: Digest
    environment: TaskEnvironment
    validation: tuple[ValidationCase, ...] = Field(min_length=1, max_length=8)
    limitations: tuple[str, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def distinct_cases(self) -> TaskPackageSpec:
        if len({c.case_id for c in self.validation}) != len(self.validation):
            raise ValueError("duplicate validation case")
        if sum(c.role == "baseline" for c in self.validation) != 1:
            raise ValueError("package requires exactly one untouched baseline")
        if sum(len(c.lanes) for c in self.validation) > 8:
            raise ValueError("package validation exceeds eight bounded lanes")
        return self


def _inputs(spec: TaskPackageSpec) -> repair.RepairSpec:
    task = repair.load_task(Path(spec.task), spec.task_sha256)
    _image_archive(Path(spec.environment.image_archive), spec.environment.image_sha256)
    baseline = next(c for c in spec.validation if c.role == "baseline")
    if baseline.input_sha256 != task.input_sha256:
        raise SkillEvalError("package baseline differs from artifact task")
    for case in spec.validation:
        _, files = runner._snapshot_files(Path(case.input_bundle), case.input_sha256)
        names = {n for n, _, _ in files}
        for required in spec.environment.required_paths:
            if required not in names and not any(n.startswith(required + "/") for n in names):
                raise SkillEvalError(f"required task context missing: {required}")
    return task


def prepare_package(spec: TaskPackageSpec, output: Path) -> dict[str, Any]:
    data = spec.model_dump(mode="json")
    data["task"] = str(Path(spec.task).absolute())
    data["environment"]["image_archive"] = str(Path(spec.environment.image_archive).absolute())
    for case in data["validation"]:
        case["input_bundle"] = str(Path(case["input_bundle"]).absolute())
    spec = TaskPackageSpec.model_validate_json(json.dumps(data))
    task = _inputs(spec)
    _outside(spec, output)
    value = {"schema_version": 1, "kind": "skilleval-task-package", "spec": data}
    digest = repair._publish(output, value)
    summary = {
        "status": "prepared",
        "path": str(output.absolute()),
        "sha256": digest,
        "family": spec.family,
        "task_id": task.task_id,
        "output_paths": list(task.editable_paths),
        "scored_admitted": False,
    }
    if task.editable_directories:
        summary["output_directories"] = list(task.editable_directories)
    if task.edit_scope == "repository":
        summary["edit_scope"] = task.edit_scope
    return summary


def read_package(path: Path, digest: str) -> TaskPackageSpec:
    """Read original package bytes without requiring its retired grading dependencies."""
    raw = _read_regular(path, 4 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise SkillEvalError("task package digest differs")
    value = evidence._parse_json(raw)
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "kind", "spec"}
        or value["schema_version"] != 1
        or value["kind"] != "skilleval-task-package"
    ):
        raise SkillEvalError("unsupported task package")
    return TaskPackageSpec.model_validate_json(json.dumps(value["spec"]))


def load_package(path: Path, digest: str) -> TaskPackageSpec:
    spec = read_package(path, digest)
    _inputs(spec)
    return spec


def execution_contract(spec: TaskPackageSpec) -> dict[str, Any]:
    """Bind executed conditions; expectations are replayed against retained observations."""
    contract = {
        "schema_version": 1,
        "image_sha256": spec.environment.image_sha256,
        "profile": spec.environment.profile,
        "cases": [
            {
                "case_id": case.case_id,
                "role": case.role,
                "input_sha256": case.input_sha256,
                "lanes": [
                    {
                        "lane_id": lane.lane_id,
                        "argv": list(lane.argv),
                        "timeout_seconds": lane.timeout_seconds,
                    }
                    for lane in case.lanes
                ],
            }
            for case in spec.validation
        ],
    }
    if spec.environment.validation_isolation != "case-v1":
        contract["validation_isolation"] = spec.environment.validation_isolation
    return contract


def _outside(spec: TaskPackageSpec, output: Path) -> None:
    roots = [Path(c.input_bundle) for c in spec.validation]
    roots.append(Path(repair.load_task(Path(spec.task), spec.task_sha256).input_bundle))
    if any(output.resolve().is_relative_to(root.resolve()) for root in roots):
        raise SkillEvalError("package output must be outside captured inputs")


def observations(lane: ValidationLane, process: dict[str, Any]) -> dict[str, Any]:
    """Parse native-tool observations, never substitute a zero-test success."""
    if lane.parser == "json":
        value = evidence._parse_json(process["stdout"].encode())
        if not isinstance(value, dict):
            raise SkillEvalError("validation lane requires a JSON object")
        return value
    text = process["stderr"]
    counts = re.findall(r"^Ran (\d+) tests? in [^\n]+$", text, re.MULTILINE)
    if len(counts) != 1 or int(counts[0]) == 0:
        raise SkillEvalError("unittest observation needs one nonempty completed suite")
    observed: dict[str, Any] = {
        "tests": int(counts[0]),
        "failures": re.findall(r"^FAIL: (.+)$", text, re.MULTILINE),
        "errors": re.findall(r"^ERROR: (.+)$", text, re.MULTILINE),
        "skips": re.findall(r"^(.+) \.\.\. skipped .+$", text, re.MULTILINE),
    }
    summaries = re.findall(r"^(OK|FAILED)(?: \(([^\n]*)\))?$", text, re.MULTILINE)
    if len(summaries) != 1:
        raise SkillEvalError("unittest observation requires one final summary")
    details = summaries[0][1]
    totals = {}
    for item in details.split(", ") if details else []:
        match = re.fullmatch(r"(failures|errors|skipped)=(\d+)", item)
        if match is None or match[1] in totals:
            raise SkillEvalError("unsupported unittest summary; preserve raw observation")
        totals[match[1]] = int(match[2])
    for key, label in (("failures", "failures"), ("errors", "errors"), ("skips", "skipped")):
        if totals.get(label, 0) != len(observed[key]):
            raise SkillEvalError("unittest counts lack complete verbose identities")
    return observed


def _profile_matches(process: dict[str, Any], profile: ExecutionProfile) -> bool:
    uid = 61000 if profile == "solver-isolated-v1" else 0
    if process.get("profile") != {
        "uid": uid,
        "gid": uid,
        "groups": [],
        "cwd": runner.GUEST_WORK,
    }:
        return False
    if profile == "native-root-v1":
        return True
    boundary = process.get("boundary")
    return (
        process.get("pipe_drain_incomplete") is False
        and isinstance(boundary, dict)
        and solver_boundary.valid_receipt(boundary)
        and boundary["native_returncode"] == process.get("returncode")
    )


def compare_lane(
    lane: ValidationLane,
    process: dict[str, Any],
    profile: ExecutionProfile = "native-root-v1",
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "lane_id": lane.lane_id,
        "status": "incomplete",
        "observed": None,
        "expected": lane.expected,
        "expected_exit_code": lane.expected_exit_code,
        "process": process,
    }
    if (
        process.get("timed_out") is not False
        or process.get("output_limit_reached") is not False
        or type(process.get("returncode")) is not int
        or process.get("argv") != list(lane.argv)
        or not _profile_matches(process, profile)
        or not isinstance(process.get("source_changes"), list)
    ):
        return result
    try:
        observed = observations(lane, process)
    except (SkillEvalError, KeyError, TypeError) as error:
        result["error"] = str(error)
        return result
    matched = process["returncode"] == lane.expected_exit_code and object_digest(
        observed
    ) == object_digest(lane.expected)
    if process["source_changes"] != []:
        matched = False
    result.update(status="matched" if matched else "unexpected", observed=observed)
    return result


def validate_environment(
    package: Path,
    digest: str,
    output: Path,
    *,
    runtime: Path,
    reuse: Path | None = None,
    reuse_sha256: str | None = None,
) -> dict[str, Any]:
    """Run declared baseline/reference commands only in disposable offline VMs."""
    from skilleval.task_environment import validate_environment as execute

    spec = read_package(package, digest)
    if spec.environment.validation_isolation == "lane-v1":
        from skilleval.lane_validation import validate_environment as execute_lanes

        return execute_lanes(
            package, digest, output, runtime=runtime, reuse=reuse, reuse_sha256=reuse_sha256
        )
    if reuse is not None or reuse_sha256 is not None:
        raise SkillEvalError("selective reuse requires validation_isolation lane-v1")
    return execute(package, digest, output, runtime=runtime)


def checked_validation(
    package: Path,
    package_digest: str,
    report: Path,
    report_digest: str,
) -> tuple[TaskPackageSpec, dict[str, Any]]:
    """Replay selected expectations; return original evidence without rewriting it."""
    from skilleval.study import _clean_native

    spec = load_package(package, package_digest)
    raw = _read_regular(report, MAX_VALIDATION_BYTES)
    if hashlib.sha256(raw).hexdigest() != report_digest:
        raise SkillEvalError("task validation digest differs")
    value = evidence._parse_json(raw)
    if (
        not isinstance(value, dict)
        or value.get("kind") != "skilleval-task-validation"
        or type(value.get("schema_version")) is not int
        or value["schema_version"] not in {1, 2, 3}
    ):
        raise SkillEvalError("unsupported task validation report")
    if value["schema_version"] == 3:
        from skilleval.lane_readiness import checked_report

        return spec, checked_report(spec, report, report_digest)
    scoped = value["schema_version"] == 2
    original = spec
    if scoped:
        locator = value.get("package")
        original_digest = value.get("package_sha256")
        if (
            not isinstance(locator, str)
            or not Path(locator).is_absolute()
            or not isinstance(original_digest, str)
        ):
            raise SkillEvalError("task validation original package binding missing")
        original = read_package(Path(locator), original_digest)
        contract = execution_contract(original)
        if (
            value.get("execution_contract") != contract
            or value.get("execution_contract_sha256") != object_digest(contract)
            or execution_contract(spec) != contract
        ):
            raise SkillEvalError("task environment execution contract changed; run new validation")
    elif value.get("package_sha256") != package_digest or value.get("status") != "matched":
        raise SkillEvalError("legacy task validation requires its exact original matched package")
    if (
        value.get("status") not in {"matched", "incomplete"}
        or value.get("scored_admitted") is not False
        or not isinstance(value.get("cases"), list)
        or len(value["cases"]) != len(original.validation)
        or value.get("coverage_gaps") != list(original.environment.coverage_gaps)
        or value.get("limitations") != list(original.limitations)
        or value.get("family") != original.family
        or value.get("inference_requests") != 0
    ):
        raise SkillEvalError("task validation is incomplete or belongs to another package")
    for case, selected, row in zip(
        original.validation, spec.validation, value["cases"], strict=True
    ):
        directory = report.parent / case.case_id
        saved = evidence._parse_json(
            _read_regular(directory / "validation.json", MAX_VALIDATION_BYTES)
        )
        recovery = evidence._parse_json(_read_regular(directory / "recovery.json", 4 * 1024 * 1024))
        if (
            not isinstance(row, dict)
            or not isinstance(recovery, dict)
            or row != saved
            or row.get("case_id") != case.case_id
            or row.get("input_sha256") != case.input_sha256
            or row.get("status") not in ({"matched", "unexpected"} if scoped else {"matched"})
            or row.get("identity") != recovery.get("identity")
            or row.get("image") != recovery.get("image")
            or not _clean_native(directory)
            or not isinstance(row.get("lanes"), list)
            or len(row["lanes"]) != len(case.lanes)
        ):
            raise SkillEvalError("task validation case or cleanup binding differs")
        if scoped and (
            row.get("role") != case.role
            or row.get("cleanup_complete") is not True
            or not isinstance(row.get("image"), dict)
            or row["image"].get("sha256") != original.environment.image_sha256
            or row.get("readiness_identity")
            != environment_identity(row.get("identity"), original.environment.profile)
        ):
            raise SkillEvalError("task validation environment identity or case binding differs")
        for lane, selected_lane, observed in zip(
            case.lanes, selected.lanes, row["lanes"], strict=True
        ):
            if not isinstance(observed, dict) or not isinstance(observed.get("process"), dict):
                raise SkillEvalError("task validation observations missing")
            process = observed["process"]
            if scoped:
                # Preserve the old interpretation. Its original expected fields
                # and raw process binding remain checked; today's parser need not
                # reproduce an obsolete interpretation to replay these same bytes.
                if (
                    read_lane(directory / ("lane-" + lane.lane_id + ".json")) != process
                    or observed.get("lane_id") != lane.lane_id
                    or observed.get("expected") != lane.expected
                    or observed.get("expected_exit_code") != lane.expected_exit_code
                ):
                    raise SkillEvalError("task validation original observation binding differs")
            elif compare_lane(lane, process, original.environment.profile) != observed:
                raise SkillEvalError("task validation observations differ")
            if (
                compare_lane(selected_lane, process, spec.environment.profile)["status"]
                != "matched"
            ):
                raise SkillEvalError(
                    "task validation observations do not match selected expectations"
                )
    return spec, value


def check_environment_identity(
    report: dict[str, Any], profile: str, current: dict[str, Any]
) -> None:
    """Reuse only completed evidence whose execution dependencies still agree."""
    if report.get("schema_version") == 3:
        expected = environment_identity(current, profile, isolation="lane-v1")
        if any(
            lane.get("readiness_identity") != expected
            for case in report["cases"]
            for lane in case["lanes"]
        ):
            raise SkillEvalError("task environment execution identity changed; run new validation")
        return
    scoped = report.get("schema_version") == 2
    expected = environment_identity(current, profile) if scoped else current
    key = "readiness_identity" if scoped else "identity"
    if any(case.get(key) != expected for case in report["cases"]):
        raise SkillEvalError("task environment execution identity changed; run new validation")


def readiness_selection(
    spec: TaskPackageSpec, selected_digest: str, report: dict[str, Any]
) -> dict[str, Any]:
    """Keep original disclosures visible alongside this selection's interpretation."""
    if report.get("schema_version") == 3:
        from skilleval.lane_readiness import selection_summary

        return selection_summary(spec, selected_digest, report)
    return {
        "policy": "task-environment-v1",
        "original_package": report["package"],
        "original_package_sha256": report["package_sha256"],
        "selected_package_sha256": selected_digest,
        "original_status": report["status"],
        "selected_expectations_status": "matched",
        "original_coverage_gaps": report["coverage_gaps"],
        "original_limitations": report["limitations"],
        "selected_coverage_gaps": list(spec.environment.coverage_gaps),
        "selected_limitations": list(spec.limitations),
    }
