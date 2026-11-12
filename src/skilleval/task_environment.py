"""Offline task command execution; host interpretation is owned by task_package."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from skilleval import repair, solver_boundary, task_transfer
from skilleval.errors import SkillEvalError
from skilleval.identity import environment_identity, object_digest
from skilleval.lifecycle import successful
from skilleval.task_package import (
    _outside,
    _profile_matches,
    compare_lane,
    execution_contract,
    load_package,
    read_lane,
)


def _required(result: dict[str, Any], label: str) -> None:
    if not successful(result):
        raise SkillEvalError(label + " failed")


def validate_environment(
    package: Path,
    digest: str,
    output: Path,
    *,
    runtime: Path,
) -> dict[str, Any]:
    """Run declared baseline/reference commands only in disposable offline VMs."""
    from skilleval import native_process, task_validation_guest
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
            row["readiness_identity"] = environment_identity(
                row["identity"], spec.environment.profile
            )
            row["image"] = session.ticket["image"]
            try:
                _, files = task_transfer._snapshot_files(Path(case.input_bundle), case.input_sha256)
                archive = Path(session.ticket["runtime_state"]) / "validation-input.tar"
                tooling = [
                    (
                        "native/validation.py",
                        Path(task_validation_guest.__file__).read_bytes(),
                        False,
                    ),
                ]
                if spec.environment.profile == "solver-isolated-v1":
                    for name, module in (
                        ("native_process.py", native_process),
                        ("solver_boundary.py", solver_boundary),
                    ):
                        source = module.__file__
                        if source is None:
                            raise SkillEvalError("validation controller source unavailable")
                        tooling.append(("native/" + name, Path(source).read_bytes(), False))
                task_transfer._tar(
                    archive,
                    [("task/" + n, b, x) for n, b, x in files] + tooling,
                )
                session.create()
                _required(
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
                _required(
                    session.guest(
                        "prepare_validation",
                        task_transfer.preparation_argv(
                            Path("/var/tmp/validation.tar"),
                            Path(task_transfer.GUEST_WORK),
                            Path(task_transfer.GUEST_ROOT),
                        ),
                    ),
                    "prepare validation input",
                )
                for lane in case.lanes:
                    guest_report = task_transfer.GUEST_ROOT + "/lane-" + lane.lane_id + ".json"
                    response = session.guest(
                        "lane-" + lane.lane_id,
                        [
                            "python3",
                            "-I",
                            task_transfer.GUEST_ROOT + "/validation.py",
                            json.dumps(
                                {
                                    "argv": lane.argv,
                                    "timeout_seconds": lane.timeout_seconds,
                                    "report_path": guest_report,
                                    "profile": spec.environment.profile,
                                }
                            ),
                        ],
                        lane.timeout_seconds + 20,
                    )
                    _required(response, "environment validation supervisor")
                    local_report = output / case.case_id / ("lane-" + lane.lane_id + ".json")
                    _required(
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
                    if not _profile_matches(process, spec.environment.profile):
                        raise SkillEvalError("unexpected validation execution profile")
                    row["lanes"].append(compare_lane(lane, process, spec.environment.profile))
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
        "schema_version": 2,
        "kind": "skilleval-task-validation",
        "package": str(package.absolute()),
        "execution_contract": execution_contract(spec),
        "execution_contract_sha256": object_digest(execution_contract(spec)),
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
