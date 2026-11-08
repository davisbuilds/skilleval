"""Noninteractive, explicit-file CLI. Help and inspection have no runtime effects."""

import argparse
import json
import math
import os
import stat
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from skilleval import __version__
from skilleval.acceptance import AcceptanceMapSpec
from skilleval.accounting import PriceSchedule
from skilleval.calibration import CalibrationResult
from skilleval.contracts import Assessment, Attempt, Experiment, Record, Task
from skilleval.errors import SkillEvalError
from skilleval.judge import AgenticCalibrationResult, AgenticReviewResult
from skilleval.reliability import ReliabilityObservations, ReliabilitySuite
from skilleval.repair import RepairSpec
from skilleval.repair_assessment import (
    DocumentAssessment,
    DocumentAssessmentSpec,
    RepairAssessment,
    RepairAssessmentSpec,
    parse_spec,
)
from skilleval.repair_outcomes import RepairOutcomeSelection
from skilleval.study import StudySpec
from skilleval.task_package import TaskPackageSpec

RECORDS: dict[str, type[Record]] = {
    "task": Task,
    "experiment": Experiment,
    "attempt": Attempt,
    "assessment": Assessment,
    "calibration-result": CalibrationResult,
    "agentic-calibration-result": AgenticCalibrationResult,
    "agentic-review-result": AgenticReviewResult,
    "reliability-suite": ReliabilitySuite,
    "reliability-observations": ReliabilityObservations,
    "study-spec": StudySpec,
    "price-schedule": PriceSchedule,
    "repair-spec": RepairSpec,
    "repair-assessment-spec": RepairAssessmentSpec,
    "repair-assessment": RepairAssessment,
    "repair-outcomes": RepairOutcomeSelection,
    "artifact-spec": RepairSpec,
    "document-assessment-spec": DocumentAssessmentSpec,
    "document-assessment": DocumentAssessment,
    "task-package-spec": TaskPackageSpec,
    "acceptance-map-spec": AcceptanceMapSpec,
}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="skilleval", description=__doc__)
    root.add_argument("--version", action="version", version=f"skilleval {__version__}")
    commands = root.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture", help="Copy and inventory an explicit evidence tree")
    capture.add_argument("source", type=Path)
    capture.add_argument("--output", type=Path, required=True)
    source = commands.add_parser("source", help="Capture pinned source without upstream history")
    source_commands = source.add_subparsers(dest="operation", required=True)
    source_capture = source_commands.add_parser(
        "capture", help="Freeze a Git revision with declared context exclusions"
    )
    source_capture.add_argument("source", type=Path)
    source_capture.add_argument("--revision", required=True, help="Full source commit object ID")
    source_capture.add_argument("--exclusions", type=Path, help="Explicit source-exclusions policy")
    source_capture.add_argument("--output", type=Path, required=True, help="Fresh output directory")
    importer = commands.add_parser("import-review", help="Import one historical review case")
    importer.add_argument("source", type=Path)
    importer.add_argument("--case", required=True)
    importer.add_argument("--output", type=Path, required=True)
    for name in ("verify", "inspect"):
        command = commands.add_parser(name, help=f"{name.title()} captured evidence")
        command.add_argument("bundle", type=Path)
        command.add_argument("--sha256", help="Expected manifest identity retained separately")
    schema = commands.add_parser("schema", help="Print a versioned record's JSON Schema")
    schema.add_argument("kind", choices=(*RECORDS, "source-exclusions"))
    validate = commands.add_parser("validate", help="Validate record structure, not its truth")
    validate.add_argument("kind", choices=(*RECORDS, "source-exclusions"))
    validate.add_argument("file", type=Path)
    review = commands.add_parser("review", help="Prepare evidence or record an assessment")
    review_commands = review.add_subparsers(dest="operation", required=True)
    prepare = review_commands.add_parser("prepare")
    prepare.add_argument("bundle", type=Path)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--include-reference", action="store_true")
    record = review_commands.add_parser("record")
    record.add_argument("bundle", type=Path)
    record.add_argument("assessment", type=Path)
    record.add_argument("--output", type=Path, required=True)
    calibration = commands.add_parser(
        "calibration", help="Prepare semantic controls or compare explicit judge results"
    )
    calibration_commands = calibration.add_subparsers(dest="operation", required=True)
    calibration_prepare = calibration_commands.add_parser("prepare")
    calibration_prepare.add_argument("bundle", type=Path)
    calibration_prepare.add_argument("--output", type=Path, required=True)
    calibration_record = calibration_commands.add_parser("record")
    calibration_record.add_argument("bundle", type=Path)
    calibration_record.add_argument("result", type=Path)
    calibration_record.add_argument("--output", type=Path, required=True)
    calibration_compare = calibration_commands.add_parser("compare")
    calibration_compare.add_argument("bundle", type=Path)
    calibration_compare.add_argument("results", type=Path, nargs="*", help="Explicit result files")
    reliability = commands.add_parser(
        "reliability", help="Prepare and compare judge stability controls"
    )
    reliability_commands = reliability.add_subparsers(dest="operation", required=True)
    reliability_prepare = reliability_commands.add_parser("prepare")
    reliability_prepare.add_argument("bundle", type=Path)
    reliability_prepare.add_argument("--output", type=Path, required=True)
    reliability_prepare.add_argument("--static", action="store_true")
    reliability_compare = reliability_commands.add_parser("compare")
    reliability_compare.add_argument("prepared", type=Path)
    reliability_compare.add_argument("observations", type=Path)
    study = commands.add_parser(
        "study", help="Frozen sequential development studies; no scored runs"
    )
    study_commands = study.add_subparsers(dest="operation", required=True)
    study_prepare = study_commands.add_parser("prepare")
    study_prepare.add_argument("spec", type=Path)
    study_prepare.add_argument("--output", type=Path, required=True)
    for name in ("run", "report", "reconcile"):
        operation = study_commands.add_parser(name)
        operation.add_argument("plan", type=Path)
        operation.add_argument("--sha256", required=True, help="Externally retained plan digest")
        operation.add_argument("--output", type=Path, required=True, help="Study output directory")
        if name == "report":
            operation.add_argument(
                "--review-output", type=Path, help="Write a fresh Markdown evidence index"
            )
            operation.add_argument(
                "--outcomes", type=Path, help="Explicit repair outcome selection"
            )
            operation.add_argument("--outcomes-sha256", help="Retained selection digest")
        if name == "run":
            operation.add_argument("--auth-file", type=Path, required=True)
            operation.add_argument(
                "--development",
                action="store_true",
                help="Explicit unscored development execution; required",
            )
    workspace = commands.add_parser(
        "workspace", help="Reconstruct source-only native review history"
    )
    workspace_commands = workspace.add_subparsers(dest="operation", required=True)
    workspace_prepare = workspace_commands.add_parser("prepare")
    workspace_prepare.add_argument("bundle", type=Path)
    workspace_prepare.add_argument(
        "--sha256", required=True, help="Expected manifest identity retained separately"
    )
    workspace_prepare.add_argument("--output", type=Path, required=True)
    qualification = commands.add_parser("qualification", help="Plan or run offline VM controls")
    qualification_commands = qualification.add_subparsers(dest="operation", required=True)
    plan = qualification_commands.add_parser("plan")
    plan.add_argument("bundle", type=Path)
    images = plan.add_mutually_exclusive_group(required=True)
    images.add_argument("--image", help="OCI reference pinned with @sha256:")
    images.add_argument("--image-archive", type=Path, help="Local Docker-save archive")
    plan.add_argument("--image-sha256", help="Required digest for a local image archive")
    plan.add_argument(
        "--reconstruct-git", action="store_true", help="Rebuild captured review history"
    )
    plan.add_argument("--commands", type=Path, required=True, help="JSON array of argv arrays")
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--cpus", type=int, default=2)
    plan.add_argument("--memory-mib", type=int, default=2048)
    run = qualification_commands.add_parser("run")
    run.add_argument("plan", type=Path)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--runtime", default="smolvm", help="Explicit trusted smolvm executable")
    run.add_argument(
        "--capture-identity",
        action="store_true",
        help="Bind complete local runtime and recovery evidence for admission",
    )
    recovery = qualification_commands.add_parser("recover", help="Recover one retained VM ticket")
    recovery.add_argument("directory", type=Path, help="Original qualification output directory")
    storage = commands.add_parser(
        "storage", help="Locate durable data and prune disposable staging"
    )
    storage_commands = storage.add_subparsers(dest="operation", required=True)
    initialize = storage_commands.add_parser("init", help="Create a private store outside Git")
    initialize.add_argument(
        "--root", type=Path, help="Override SKILLEVAL_DATA_HOME/platform default"
    )
    locate = storage_commands.add_parser("path", help="Print a stable path without creating output")
    locate.add_argument("group", choices=("experiments", "captures", "images", "tools", "audits"))
    locate.add_argument(
        "name", nargs="?", help="Optional single-component experiment or asset name"
    )
    locate.add_argument("--root", type=Path, help="Override SKILLEVAL_DATA_HOME/platform default")
    locate.add_argument("--plain", action="store_true", help="Print only the path for shell use")
    prune = storage_commands.add_parser("prune", help="Preview verified copies in completed runs")
    prune.add_argument("directories", type=Path, nargs="+", help="Explicit run output directories")
    prune.add_argument(
        "--apply", action="store_true", help="Delete eligible copies; default is dry-run"
    )
    staging = storage_commands.add_parser(
        "prune-staging", help="Preview completed-run checker, validation and native tooling copies"
    )
    staging.add_argument(
        "directories", type=Path, nargs="+", help="Explicit run output directories"
    )
    staging.add_argument(
        "--apply", action="store_true", help="Delete eligible copies; default is dry-run"
    )
    admission = commands.add_parser("admission", help="Seal or check bounded environment evidence")
    admission_commands = admission.add_subparsers(dest="operation", required=True)
    seal = admission_commands.add_parser("seal")
    seal.add_argument("--plan", type=Path, required=True)
    seal.add_argument("--offline", type=Path, required=True, help="Offline task report")
    seal.add_argument("--native", type=Path, required=True, help="Authenticated native report")
    seal.add_argument("--lifecycle", type=Path, required=True, help="Adversarial lifecycle report")
    seal.add_argument("--catalog", type=Path, required=True)
    seal.add_argument("--runtime", required=True)
    seal.add_argument("--output", type=Path, required=True)
    admission_check = admission_commands.add_parser("check")
    admission_check.add_argument("snapshot", type=Path)
    admission_check.add_argument(
        "--sha256", required=True, help="Previously retained snapshot digest"
    )
    native = commands.add_parser("native", help="Explicit native development workloads")
    native_commands = native.add_subparsers(dest="operation", required=True)
    native_run = native_commands.add_parser(
        "qualify", help="Run a development review, repair or judge"
    )
    native_run.add_argument("bundle", type=Path, help="Frozen source-only snapshot")
    native_run.add_argument("--sha256", required=True)
    native_run.add_argument("--prompt", type=Path, required=True)
    native_run.add_argument("--mode", choices=("review", "repair", "judge"), required=True)
    native_run.add_argument("--judge-policy", choices=("static", "investigative"))
    native_run.add_argument("--runtime", type=Path, required=True)
    native_run.add_argument("--image-archive", type=Path, required=True)
    native_run.add_argument("--image-sha256", required=True)
    native_run.add_argument("--catalog", type=Path, required=True)
    native_run.add_argument("--auth-file", type=Path, required=True)
    native_run.add_argument("--output", type=Path, required=True)
    native_run.add_argument(
        "--timeout-seconds",
        type=int,
        help="Native deadline (default/maximum: tasks 1800s, judges 900s)",
    )
    native_run.add_argument("--max-requests", type=int, default=32)
    native_run.add_argument("--max-artifact-bytes", type=int, default=64 * 1024 * 1024)
    native_run.add_argument("--max-artifact-files", type=int, default=10_000)
    native_run.add_argument("--skill-bundle", type=Path)
    native_run.add_argument("--skill-sha256")
    judge = commands.add_parser("judge", help="Prepare or record native semantic assessments")
    judge_commands = judge.add_subparsers(dest="operation", required=True)
    for kind in ("calibration", "review"):
        prepare_judge = judge_commands.add_parser("prepare-" + kind)
        prepare_judge.add_argument("bundle", type=Path)
        if kind == "review":
            prepare_judge.add_argument("report", type=Path)
        prepare_judge.add_argument("--output", type=Path, required=True)
        prepare_judge.add_argument(
            "--static", action="store_true", help="Source-only prompt policy"
        )
        record_judge = judge_commands.add_parser("record-" + kind)
        record_judge.add_argument("bundle", type=Path)
        if kind == "review":
            record_judge.add_argument("report", type=Path)
        record_judge.add_argument("packet", type=Path)
        record_judge.add_argument("run", type=Path)
        record_judge.add_argument("--output", type=Path, required=True)
    treatment = commands.add_parser("treatment", help="Capture a pinned skill with attribution")
    treatment_commands = treatment.add_subparsers(dest="operation", required=True)
    skill = treatment_commands.add_parser("capture")
    skill.add_argument("source", type=Path)
    skill.add_argument("--license", type=Path, help="Existing root license or notice, if supplied")
    skill.add_argument("--source-url", required=True)
    skill.add_argument("--source-revision", required=True)
    skill.add_argument("--output", type=Path, required=True)
    package = commands.add_parser(
        "task-package", help="Prepare task context and acceptance; validate environments in VMs"
    )
    package_commands = package.add_subparsers(dest="operation", required=True)
    package_prepare = package_commands.add_parser("prepare")
    package_prepare.add_argument("spec", type=Path)
    package_prepare.add_argument("--output", type=Path, required=True)
    acceptance_prepare = package_commands.add_parser(
        "prepare-acceptance", help="Freeze an operator acceptance map without execution"
    )
    acceptance_prepare.add_argument("spec", type=Path)
    acceptance_prepare.add_argument("--output", type=Path, required=True)
    acceptance_inspect = package_commands.add_parser(
        "inspect-acceptance", help="Verify and render an acceptance map; validity stays unassessed"
    )
    acceptance_inspect.add_argument("map", type=Path)
    acceptance_inspect.add_argument("--sha256", required=True)
    acceptance_inspect.add_argument("--review-output", type=Path)
    package_validate = package_commands.add_parser("validate-environment")
    package_validate.add_argument("package", type=Path)
    package_validate.add_argument("--sha256", required=True)
    package_validate.add_argument("--runtime", type=Path, required=True)
    package_validate.add_argument("--output", type=Path, required=True)
    repair = commands.add_parser(
        "repair", aliases=["artifact"], help="Collect file artifacts and run offline checks"
    )
    repair_commands = repair.add_subparsers(dest="operation", required=True)
    prepare_repair = repair_commands.add_parser("prepare", help="Freeze source, scope and checks")
    prepare_repair.add_argument("spec", type=Path)
    prepare_repair.add_argument("--output", type=Path, required=True)
    for action in ("prepare-assessment", "record-assessment", "prepare-citation-correction"):
        command = repair_commands.add_parser(action)
        command.add_argument("spec", type=Path)
        command.add_argument("--output", type=Path, required=True)
        if action in ("record-assessment", "prepare-citation-correction"):
            command.add_argument("--packet", type=Path, required=True)
            command.add_argument("--run", type=Path, required=True)
        if action == "record-assessment":
            command.add_argument("--correction-packet", type=Path)
            command.add_argument("--correction-run", type=Path)
        if action == "prepare-citation-correction":
            command.add_argument(
                "--timeout-seconds",
                type=int,
                default=180,
                help="Declared correction deadline (default: 180s; maximum: 900s)",
            )
            command.add_argument(
                "--max-requests",
                type=int,
                default=4,
                help="Declared correction request ceiling (default: 4)",
            )
    for repair_operation in ("collect", "control", "check"):
        command = repair_commands.add_parser(repair_operation)
        command.add_argument("task", type=Path)
        command.add_argument("--task-sha256", required=True)
        command.add_argument("--output", type=Path, required=True)
        if repair_operation == "collect":
            command.add_argument("--run", type=Path, required=True)
        elif repair_operation == "control":
            command.add_argument("--candidate", type=Path, required=True)
            command.add_argument("--candidate-sha256", required=True)
        else:
            command.add_argument("--result", type=Path, required=True)
            command.add_argument("--result-sha256", required=True)
            command.add_argument("--runtime", type=Path, required=True)
            command.add_argument("--image-archive", type=Path, required=True)
            command.add_argument("--image-sha256", required=True)
    return root


def read_json(path: Path) -> Any:
    """Read bounded control data; malformed documents must not trigger execution."""
    limit = 4 * 1024 * 1024
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise SkillEvalError("control input must be a regular file at most 4 MiB")
        contents = stream.read(limit + 1)
    if len(contents) > limit:
        raise SkillEvalError("control input exceeds 4 MiB")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise SkillEvalError("duplicate JSON object key")
            result[key] = value
        return result

    def invalid_constant(_: str) -> None:
        raise SkillEvalError("non-finite JSON number")

    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            invalid_constant(value)
        return parsed

    return json.loads(
        contents,
        object_pairs_hook=pairs,
        parse_constant=invalid_constant,
        parse_float=finite_float,
    )


def write_new_json(path: Path, value: object) -> None:
    """Explicit new control file; an existing file is never silently replaced."""
    data = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(data)


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "task-package":
        from skilleval.task_package import prepare_package, validate_environment

        if args.operation == "prepare":
            return prepare_package(
                TaskPackageSpec.model_validate_json(json.dumps(read_json(args.spec))), args.output
            )
        if args.operation == "prepare-acceptance":
            from skilleval.acceptance import prepare_acceptance

            return prepare_acceptance(
                AcceptanceMapSpec.model_validate_json(json.dumps(read_json(args.spec))), args.output
            )
        if args.operation == "inspect-acceptance":
            from skilleval.acceptance import (
                load_acceptance,
                summarize_acceptance,
                write_acceptance_review,
            )

            acceptance_spec = load_acceptance(args.map, args.sha256)
            result = summarize_acceptance(acceptance_spec)
            if args.review_output is not None:
                write_acceptance_review(acceptance_spec, args.review_output)
                result["review_output"] = str(args.review_output.absolute())
            return result
        return validate_environment(args.package, args.sha256, args.output, runtime=args.runtime)
    if args.command in {"repair", "artifact"}:
        from skilleval.repair import collect_control, collect_repair, prepare_repair
        from skilleval.repair_check import check_repair

        if args.operation in (
            "prepare-assessment",
            "record-assessment",
            "prepare-citation-correction",
        ):
            from skilleval.repair_assessment import (
                prepare_assessment,
                prepare_citation_correction,
            )
            from skilleval.repair_assessment import (
                record_assessment as record_repair_assessment,
            )

            if args.operation == "record-assessment" and (
                (args.correction_packet is None) != (args.correction_run is None)
            ):
                raise SkillEvalError(
                    "citation correction requires both --correction-packet and --correction-run"
                )
            assessment_spec = parse_spec(json.dumps(read_json(args.spec)))
            if args.operation == "prepare-assessment":
                return prepare_assessment(assessment_spec, args.output)
            if args.operation == "prepare-citation-correction":
                return prepare_citation_correction(
                    assessment_spec,
                    args.packet,
                    args.run,
                    args.output,
                    timeout_seconds=args.timeout_seconds,
                    max_requests=args.max_requests,
                )
            return record_repair_assessment(
                assessment_spec,
                args.packet,
                args.run,
                args.output,
                correction_packet=args.correction_packet,
                correction_run=args.correction_run,
            )

        if args.operation == "prepare":
            repair_spec = RepairSpec.model_validate_json(json.dumps(read_json(args.spec)))
            return prepare_repair(repair_spec, args.output)
        if args.operation == "collect":
            return collect_repair(args.task, args.task_sha256, args.run, args.output)
        if args.operation == "control":
            return collect_control(
                args.task, args.task_sha256, args.candidate, args.candidate_sha256, args.output
            )
        return check_repair(
            args.task,
            args.task_sha256,
            args.result,
            args.result_sha256,
            args.output,
            runtime=args.runtime,
            image=args.image_archive,
            image_sha256=args.image_sha256,
        )
    if args.command == "reliability":
        from skilleval.reliability import compare_reliability, prepare_reliability

        if args.operation == "prepare":
            return prepare_reliability(args.bundle, args.output, investigation=not args.static)
        return compare_reliability(args.prepared, args.observations)
    if args.command == "study":
        from skilleval.grading import _read_regular
        from skilleval.study import prepare_study, reconcile_study, report_study, run_study

        if args.operation == "prepare":
            spec = StudySpec.model_validate_json(_read_regular(args.spec, 4 * 1024 * 1024))
            return prepare_study(spec, args.output)
        if args.operation == "run":
            return run_study(
                args.plan,
                args.sha256,
                args.output,
                auth_file=args.auth_file,
                development=args.development,
            )
        if args.operation == "reconcile":
            return reconcile_study(args.plan, args.sha256, args.output)
        if (args.outcomes is None) != (args.outcomes_sha256 is None):
            raise SkillEvalError("repair outcomes require both selection and digest")
        study_report = report_study(args.plan, args.sha256, args.output)
        if args.outcomes is not None:
            from skilleval.repair_outcomes import attach_outcomes

            study_report = attach_outcomes(
                study_report, args.sha256, args.outcomes, args.outcomes_sha256
            )
        if args.review_output is not None:
            from skilleval.repair_outcomes import render_review
            from skilleval.runner import _exclusive

            _exclusive(args.review_output, render_review(study_report).encode())
        return study_report
    if args.command == "native":
        from skilleval.budgets import native_deadline
        from skilleval.gateway import native_limits
        from skilleval.grading import _read_regular
        from skilleval.runner import run_native

        if (args.mode == "judge") != (args.judge_policy is not None):
            raise SkillEvalError("judge mode requires --judge-policy; task modes omit it")
        deadline = native_deadline(args.mode, args.timeout_seconds)
        return run_native(
            args.bundle,
            args.sha256,
            _read_regular(args.prompt, 1024 * 1024).decode(),
            args.output,
            runtime=args.runtime.resolve(),
            image=args.image_archive.absolute(),
            image_sha256=args.image_sha256,
            catalog=args.catalog.absolute(),
            auth_file=args.auth_file.absolute(),
            mode=args.mode,
            native_timeout_seconds=deadline,
            max_artifact_bytes=args.max_artifact_bytes,
            max_artifact_files=args.max_artifact_files,
            limits=native_limits(deadline, max_requests=args.max_requests),
            skill_bundle=args.skill_bundle,
            skill_digest=args.skill_sha256,
            investigation=None if args.mode != "judge" else args.judge_policy == "investigative",
        )
    if args.command == "judge":
        from skilleval.judge import (
            prepare_agentic_calibration,
            prepare_agentic_review,
            record_agentic_calibration,
            record_agentic_review,
        )

        if args.operation == "prepare-calibration":
            return prepare_agentic_calibration(
                args.bundle, args.output, investigation=not args.static
            )
        if args.operation == "prepare-review":
            return prepare_agentic_review(
                args.bundle, args.report, args.output, investigation=not args.static
            )
        if args.operation == "record-calibration":
            return record_agentic_calibration(args.bundle, args.packet, args.run, args.output)
        return record_agentic_review(args.bundle, args.report, args.packet, args.run, args.output)
    if args.command == "treatment":
        from skilleval.treatments import capture_skill

        return capture_skill(
            args.source,
            args.license,
            args.output,
            source_url=args.source_url,
            source_revision=args.source_revision,
        )
    if args.command == "source":
        from skilleval.source_capture import SourceExclusions, capture_source

        exclusions = (
            SourceExclusions.model_validate_json(json.dumps(read_json(args.exclusions)))
            if args.exclusions is not None
            else None
        )
        return capture_source(args.source, args.revision, args.output, exclusions=exclusions)
    if args.command in ("schema", "validate"):
        record_type: type[BaseModel]
        if args.kind == "source-exclusions":
            from skilleval.source_capture import SourceExclusions

            record_type = SourceExclusions
        else:
            record_type = RECORDS[args.kind]
        if args.command == "schema":
            return record_type.model_json_schema()
        document = read_json(args.file)
        record = record_type.model_validate_json(json.dumps(document))
        return {"status": "valid-structure", "record": record.model_dump(mode="json")}
    if args.command in ("capture", "import-review", "verify", "inspect"):
        from skilleval.evidence import import_review, snapshot, verify

        if args.command == "capture":
            return snapshot(args.source, args.output)
        if args.command == "import-review":
            return import_review(args.source, args.case, args.output)
        manifest = verify(args.bundle, expected_digest=args.sha256)
        summary = {
            "status": "verified",
            "scope": "captured-byte-integrity",
            "kind": manifest["kind"],
            "file_count": len(manifest["files"]),
            "total_bytes": sum(entry["size"] for entry in manifest["files"]),
            "expected_digest_checked": args.sha256 is not None,
        }
        if args.command == "verify":
            summary["manifest"] = manifest
        return summary
    if args.command == "review":
        from skilleval.grading import prepare_review, record_assessment

        if args.operation == "prepare":
            return prepare_review(
                args.bundle, args.output, include_reference=args.include_reference
            )
        return record_assessment(args.bundle, args.assessment, args.output)
    if args.command == "calibration":
        from skilleval.calibration import (
            compare_calibration,
            prepare_calibration,
            record_calibration,
        )

        if args.operation == "prepare":
            return prepare_calibration(args.bundle, args.output)
        if args.operation == "record":
            return record_calibration(args.bundle, args.result, args.output)
        return compare_calibration(args.bundle, args.results)
    if args.command == "workspace":
        from skilleval.workspace import prepare_review as prepare_workspace

        return prepare_workspace(args.bundle, args.sha256, args.output)
    if args.command == "qualification":
        from skilleval.qualification import plan_qualification, run_offline_qualification

        if args.operation == "recover":
            from skilleval.lifecycle import recover

            ticket = recover(args.directory)
            return {
                "status": "recovered" if ticket["cleanup_complete"] else "partial",
                "admitted": False,
                "ticket": ticket,
            }
        if args.operation == "plan":
            controls = read_json(args.commands)
            if not isinstance(controls, list) or not all(
                isinstance(row, list) and row and all(isinstance(arg, str) for arg in row)
                for row in controls
            ):
                raise SkillEvalError("commands must be a nonempty JSON array of argv arrays")
            plan = plan_qualification(
                args.bundle,
                args.image,
                controls,
                cpus=args.cpus,
                memory_mib=args.memory_mib,
                image_archive=args.image_archive,
                image_sha256=args.image_sha256,
                reconstruct_git=args.reconstruct_git,
            )
            write_new_json(args.output, plan)
            return {"status": "planned", "path": str(args.output), "plan": plan}
        value = read_json(args.plan)
        if not isinstance(value, dict):
            raise SkillEvalError("qualification plan must be a JSON object")
        return run_offline_qualification(
            value, args.output, runtime_binary=args.runtime, capture_identity=args.capture_identity
        )
    if args.command == "storage":
        from skilleval.retention import prune_images, prune_staging_runs
        from skilleval.storage import initialize, storage_path

        if args.operation == "init":
            return initialize(args.root)
        if args.operation == "path":
            return storage_path(args.group, args.name, root=args.root)
        if args.operation == "prune":
            return prune_images(args.directories, apply=args.apply)
        return prune_staging_runs(args.directories, apply=args.apply)
    if args.command == "admission":
        from skilleval.admission import check_admission, seal_admission

        if args.operation == "check":
            return check_admission(args.snapshot, args.sha256)
        return seal_admission(
            args.plan,
            args.offline,
            args.native,
            args.lifecycle,
            args.catalog,
            args.runtime,
            args.output,
        )
    raise SkillEvalError("unknown command")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = dispatch(args)
    except KeyboardInterrupt:
        print(json.dumps({"status": "interrupted"}), file=sys.stderr)
        return 130
    except ValidationError as exc:
        fields = [".".join(str(part) for part in item["loc"]) for item in exc.errors()]
        error = {"status": "error", "error": "invalid record", "fields": fields}
        print(json.dumps(error), file=sys.stderr)
        return 2
    except (SkillEvalError, OSError, ValueError, RecursionError) as exc:
        # Avoid echoing untrusted file content or exception representations.
        message = str(exc) if isinstance(exc, SkillEvalError) else type(exc).__name__
        print(json.dumps({"status": "error", "error": message}), file=sys.stderr)
        return 2
    if args.command == "storage" and args.operation == "path" and args.plain:
        print(result["path"])
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    return (
        1
        if result.get("status")
        in ("failed", "incomplete", "error", "partial", "timed-out", "invalid-scope")
        or result.get("accepted_by_checks") is False
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
