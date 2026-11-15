"""Independent task-environment lane execution and explicitly pinned reuse."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from skilleval import repair
from skilleval.errors import SkillEvalError
from skilleval.identity import file_digest
from skilleval.lifecycle import LifecycleInterruptedError
from skilleval.task_environment import _run_case
from skilleval.task_package import _outside, load_package


def validate_environment(
    package: Path,
    digest: str,
    output: Path,
    *,
    runtime: Path,
    reuse: Path | None = None,
    reuse_sha256: str | None = None,
) -> dict[str, Any]:
    """Run each missing lane in its own disposable VM; retain prior receipts verbatim."""
    from skilleval import lane_readiness

    spec, plan = lane_readiness.plan_validation(package, digest, runtime, reuse, reuse_sha256)
    _outside(spec, output)
    if output.resolve().is_relative_to(runtime.resolve().parent):
        raise SkillEvalError("validation output must be outside runtime inventory")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    cases = {case.case_id: case for case in spec.validation}
    selected: list[dict[str, Any]] = []
    for item in plan:
        source = item["source"]
        stop = False
        if item["action"] == "execute":
            case = cases[item["case_id"]]
            lane = next(lane for lane in case.lanes if lane.lane_id == item["lane_id"])
            directory = output / "executions" / case.case_id / lane.lane_id
            try:
                row = _run_case(
                    spec,
                    case.model_copy(update={"lanes": (lane,)}),
                    directory,
                    package=package,
                    digest=digest,
                    runtime=runtime,
                )
            except (
                SkillEvalError,
                OSError,
                ValueError,
                KeyError,
                LifecycleInterruptedError,
            ) as error:
                # Preparation can fail before a lifecycle ticket exists. Do not
                # invent a successful cleanup or execution identity for that case.
                row = {
                    "case_id": case.case_id,
                    "role": case.role,
                    "input_sha256": case.input_sha256,
                    "status": "incomplete",
                    "lanes": [],
                    "cleanup_complete": False,
                    "error": str(error)
                    if isinstance(error, SkillEvalError)
                    else type(error).__name__,
                }
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            receipt = directory / "lane-execution.json"
            repair._publish(
                receipt,
                lane_readiness.execution_receipt(spec, package, digest, case, lane, row),
            )
            source = {"receipt": str(receipt.absolute()), "sha256": file_digest(receipt)}
            stop = (
                row.get("error") == "LifecycleInterruptedError"
                or row.get("cleanup_complete") is not True
            )
        elif item["action"] != "reuse":
            raise SkillEvalError("unknown lane validation action")
        selected.append(
            {
                "case_id": item["case_id"],
                "lane_id": item["lane_id"],
                "action": item["action"],
                "reason": item["reason"],
                "source": source,
            }
        )
        if stop:
            break
    # Recheck the selected package and captured inputs after both executed and
    # reused selections. On drift, immutable execution receipts remain available
    # but no aggregate selection is published against changed inputs.
    load_package(package, digest)
    report = lane_readiness.validation_report(spec, package, digest, selected)
    repair._publish(output / "task-validation.json", report)
    return report
