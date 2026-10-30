"""Task packages bind artifact contracts to explicit, offline environment checks."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from skilleval import evidence, repair, runner
from skilleval.contracts import Citation, Digest, Identifier, Record
from skilleval.errors import SkillEvalError
from skilleval.grading import _read_regular
from skilleval.identity import object_digest
from skilleval.qualification import _image_archive

# Two 1 MiB log streams can expand sixfold when encoded as JSON. A package
# has at most eight lanes; envelope and expected-observation space is additional.
MAX_LANE_BYTES = 16 * 1024 * 1024
MAX_VALIDATION_BYTES = 160 * 1024 * 1024


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
    profile: Literal["native-root-v1"] = "native-root-v1"
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
    return {
        "status": "prepared",
        "path": str(output.absolute()),
        "sha256": digest,
        "family": spec.family,
        "task_id": task.task_id,
        "output_paths": list(task.editable_paths),
        "scored_admitted": False,
    }


def load_package(path: Path, digest: str) -> TaskPackageSpec:
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
    spec = TaskPackageSpec.model_validate_json(json.dumps(value["spec"]))
    _inputs(spec)
    return spec


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


def compare_lane(lane: ValidationLane, process: dict[str, Any]) -> dict[str, Any]:
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
        or process.get("profile")
        != {
            "uid": 0,
            "gid": 0,
            "groups": [],
            "cwd": runner.GUEST_WORK,
        }
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
) -> dict[str, Any]:
    """Run declared baseline/reference commands only in disposable offline VMs."""
    from skilleval import task_validation_guest
    from skilleval.lifecycle import Lifecycle, LifecycleInterruptedError, interrupt_scope

    spec = load_package(package, digest)
    _outside(spec, output)
    if output.resolve().is_relative_to(runtime.resolve().parent):
        raise SkillEvalError("validation output must be outside runtime inventory")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    cases: list[dict[str, Any]] = []
    for case in spec.validation:
        row: dict[str, Any] = {
            "case_id": case.case_id,
            "role": case.role,
            "input_sha256": case.input_sha256,
            "status": "incomplete",
            "lanes": [],
        }
        cases.append(row)
        interrupted = False
        with interrupt_scope():
            session = Lifecycle.prepare(
                output / case.case_id,
                runtime,
                Path(spec.environment.image_archive),
                spec.environment.image_sha256,
            )
            row["identity"] = session.ticket["identity"]
            row["image"] = session.ticket["image"]
            try:
                _, files = runner._snapshot_files(Path(case.input_bundle), case.input_sha256)
                archive = Path(session.ticket["runtime_state"]) / "validation-input.tar"
                runner._tar(
                    archive,
                    [("task/" + n, b, x) for n, b, x in files]
                    + [
                        (
                            "native/validation.py",
                            Path(task_validation_guest.__file__).read_bytes(),
                            False,
                        ),
                    ],
                )
                session.create()
                runner._required(
                    session.invoke(
                        "copy_validation",
                        [
                            "machine",
                            "cp",
                            str(archive),
                            session.machine + ":/var/tmp/validation.tar",
                        ],
                    ),
                    "copy validation input",
                )
                runner._required(
                    session.guest(
                        "prepare_validation",
                        runner.preparation_argv(
                            Path("/var/tmp/validation.tar"),
                            Path(runner.GUEST_WORK),
                            Path(runner.GUEST_ROOT),
                        ),
                    ),
                    "prepare validation input",
                )
                for lane in case.lanes:
                    guest_report = runner.GUEST_ROOT + "/lane-" + lane.lane_id + ".json"
                    response = session.guest(
                        "lane-" + lane.lane_id,
                        [
                            "python3",
                            "-I",
                            runner.GUEST_ROOT + "/validation.py",
                            json.dumps(
                                {
                                    "argv": lane.argv,
                                    "timeout_seconds": lane.timeout_seconds,
                                    "report_path": guest_report,
                                }
                            ),
                        ],
                        lane.timeout_seconds + 20,
                    )
                    runner._required(response, "environment validation supervisor")
                    local_report = output / case.case_id / ("lane-" + lane.lane_id + ".json")
                    runner._required(
                        session.invoke(
                            "collect-" + lane.lane_id,
                            [
                                "machine",
                                "cp",
                                session.machine + ":" + guest_report,
                                str(local_report),
                            ],
                        ),
                        "collect full validation observation",
                    )
                    process = read_lane(local_report)
                    if not isinstance(process, dict) or process.get("profile") != {
                        "uid": 0,
                        "gid": 0,
                        "groups": [],
                        "cwd": runner.GUEST_WORK,
                    }:
                        raise SkillEvalError("unexpected validation execution profile")
                    row["lanes"].append(compare_lane(lane, process))
                row["status"] = (
                    "matched"
                    if all(r["status"] == "matched" for r in row["lanes"])
                    else "unexpected"
                )
                load_package(package, digest)
            except (
                SkillEvalError,
                OSError,
                ValueError,
                KeyError,
                LifecycleInterruptedError,
            ) as error:
                row["error"] = (
                    str(error) if isinstance(error, SkillEvalError) else type(error).__name__
                )
                row["status"] = "incomplete"
                interrupted = isinstance(error, LifecycleInterruptedError)
            finally:
                ticket = session.cleanup()
                row["cleanup_complete"] = ticket["cleanup_complete"]
                if not ticket["cleanup_complete"]:
                    row["status"] = "incomplete"
                repair._publish(output / case.case_id / "validation.json", row)
        if interrupted or not row["cleanup_complete"]:
            break
    report = {
        "schema_version": 1,
        "kind": "skilleval-task-validation",
        "package_sha256": digest,
        "family": spec.family,
        "cases": cases,
        "status": "matched"
        if len(cases) == len(spec.validation) and all(c["status"] == "matched" for c in cases)
        else "incomplete",
        "coverage_gaps": list(spec.environment.coverage_gaps),
        "limitations": list(spec.limitations),
        "inference_requests": 0,
        "scored_admitted": False,
    }
    repair._publish(output / "task-validation.json", report)
    return report


def checked_validation(
    package: Path,
    package_digest: str,
    report: Path,
    report_digest: str,
) -> tuple[TaskPackageSpec, dict[str, Any]]:
    """Recompute selected validation, retaining exceptions rather than calling them green."""
    from skilleval.study import _clean_native

    spec = load_package(package, package_digest)
    raw = _read_regular(report, MAX_VALIDATION_BYTES)
    if hashlib.sha256(raw).hexdigest() != report_digest:
        raise SkillEvalError("task validation digest differs")
    value = evidence._parse_json(raw)
    if (
        not isinstance(value, dict)
        or value.get("kind") != "skilleval-task-validation"
        or value.get("schema_version") != 1
        or value.get("package_sha256") != package_digest
        or value.get("status") != "matched"
        or value.get("scored_admitted") is not False
        or not isinstance(value.get("cases"), list)
        or len(value["cases"]) != len(spec.validation)
        or value.get("coverage_gaps") != list(spec.environment.coverage_gaps)
        or value.get("limitations") != list(spec.limitations)
        or value.get("family") != spec.family
        or value.get("inference_requests") != 0
    ):
        raise SkillEvalError("task validation is incomplete or belongs to another package")
    for case, row in zip(spec.validation, value["cases"], strict=True):
        directory = report.parent / case.case_id
        saved = evidence._parse_json(
            _read_regular(directory / "validation.json", MAX_VALIDATION_BYTES)
        )
        recovery = evidence._parse_json(_read_regular(directory / "recovery.json", 4 * 1024 * 1024))
        if (
            row != saved
            or row.get("case_id") != case.case_id
            or row.get("input_sha256") != case.input_sha256
            or row.get("status") != "matched"
            or row.get("identity") != recovery.get("identity")
            or row.get("image") != recovery.get("image")
            or not _clean_native(directory)
            or len(row.get("lanes", [])) != len(case.lanes)
        ):
            raise SkillEvalError("task validation case or cleanup binding differs")
        for lane, observed in zip(case.lanes, row["lanes"], strict=True):
            compared = compare_lane(lane, observed["process"])
            if compared != observed or compared["status"] != "matched":
                raise SkillEvalError("task validation observations differ")
    return spec, value
