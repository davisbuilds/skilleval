"""Frozen, sequential development studies. No scored dispatch or automatic recovery."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import random
import re
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from skilleval import evidence, runner
from skilleval.accounting import PriceSchedule, estimate_api_equivalent_cost
from skilleval.admission import check_admission
from skilleval.contracts import Digest, Identifier, Record
from skilleval.efficiency import efficiency_ledger, pair_efficiency
from skilleval.errors import SkillEvalError
from skilleval.gateway import EFFORT, MODEL, GatewayLimits
from skilleval.grading import _read_regular
from skilleval.identity import file_digest, object_digest, snapshot_identity
from skilleval.lifecycle import LifecycleInterruptedError, interrupt_scope
from skilleval.qualification import _image_archive
from skilleval.treatments import _verified_skill, check_task_baseline


class StudyTask(Record):
    mode: Literal["review", "repair"] = "review"
    task_id: Identifier
    input_bundle: str = Field(min_length=1)
    input_sha256: Digest
    prompt: str = Field(min_length=1, max_length=262144)


class StudySpec(Record):
    study_id: Identifier
    scope: Literal["development-only"] = "development-only"
    tasks: tuple[StudyTask, ...] = Field(min_length=1, max_length=100)
    skill_bundle: str = Field(min_length=1)
    skill_sha256: Digest
    runtime: str = Field(min_length=1)
    image: str = Field(min_length=1)
    image_sha256: Digest
    catalog: str = Field(min_length=1)
    repetitions: int = Field(default=1, ge=1, le=100)
    order_seed: int = Field(ge=0)
    invocation_policy: Literal["explicit-use", "natural-availability"] = "explicit-use"
    price_schedule: PriceSchedule | None = None
    max_attempts_per_cell: int = Field(default=1, ge=1, le=5)
    native_timeout_seconds: int = Field(default=600, ge=1, le=780)
    max_requests_per_attempt: int = Field(default=32, ge=1, le=32)
    assessment_policy: Literal["unassessed"] = "unassessed"
    primary_attempt_policy: Literal["first-attempt"] = "first-attempt"
    admission: str | None = None
    admission_sha256: Digest | None = None

    @model_validator(mode="after")
    def distinct_inputs(self) -> StudySpec:
        if self.price_schedule is not None and self.price_schedule.model != MODEL:
            raise ValueError("price schedule must cover the study model")
        if len({task.task_id for task in self.tasks}) != len(self.tasks):
            raise ValueError("task IDs must be unique")
        if len({task.input_sha256 for task in self.tasks}) != len(self.tasks):
            raise ValueError("duplicate task inputs belong in repetitions, not independent tasks")
        if (self.admission is None) != (self.admission_sha256 is None):
            raise ValueError("admission requires its independently retained digest")
        if len(self.tasks) * self.repetitions * 2 * self.max_attempts_per_cell > 1000:
            raise ValueError("development study exceeds 1000 reserved attempts")
        return self


def _document(path: Path) -> dict[str, Any]:
    value = evidence._parse_json(_read_regular(path, 16 * 1024 * 1024))
    if not isinstance(value, dict):
        raise SkillEvalError("expected a study JSON object")
    return value


def _new(path: Path, value: dict[str, Any]) -> str:
    """Publish a complete durable record without replacing an earlier record."""
    raw = evidence._json_bytes(value)
    with evidence._directory(path.parent) as directory:
        name = ".study-" + uuid.uuid4().hex
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(
                name, path.name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False
            )
            os.fsync(directory)
        finally:
            os.unlink(name, dir_fd=directory)
    return hashlib.sha256(raw).hexdigest()


def _matrix(spec: StudySpec) -> list[dict[str, Any]]:
    randomizer = random.Random(spec.order_seed)
    blocks = [
        (task.task_id, repetition)
        for repetition in range(1, spec.repetitions + 1)
        for task in spec.tasks
    ]
    randomizer.shuffle(blocks)
    cells: list[dict[str, Any]] = []
    for pair, (task_id, repetition) in enumerate(blocks, 1):
        arms = ["absent", "available"]
        randomizer.shuffle(arms)
        for arm in arms:
            cells.append(
                {
                    "cell_id": f"cell-{len(cells) + 1:05}",
                    "pair_id": f"pair-{pair:05}",
                    "task_id": task_id,
                    "repetition": repetition,
                    "arm": arm,
                }
            )
    return cells


def _invocations(spec: StudySpec, skill_name: str) -> dict[str, dict[str, str]]:
    """Freeze task prompts separately from the minimal selected-skill request."""
    if (
        not isinstance(skill_name, str)
        or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", skill_name) is None
    ):
        raise SkillEvalError("invalid frozen skill name")
    prefix = (
        f"Use ${skill_name} for this task.\n\n" if spec.invocation_policy == "explicit-use" else ""
    )
    return {
        task.task_id: {"absent": task.prompt, "available": prefix + task.prompt}
        for task in spec.tasks
    }


def _check_inputs(spec: StudySpec) -> dict[str, Any]:
    for task in spec.tasks:
        manifest = evidence.verify(Path(task.input_bundle), task.input_sha256)
        if manifest["kind"] != "snapshot":
            raise SkillEvalError("study tasks require frozen native input snapshots")
        check_task_baseline(Path(task.input_bundle))
    _verified_skill(Path(spec.skill_bundle), spec.skill_sha256)
    if file_digest(Path(spec.catalog)) != runner.CATALOG_SHA256:
        raise SkillEvalError("study model catalog differs from pinned native route")
    return _image_archive(Path(spec.image), spec.image_sha256)


def prepare_study(spec: StudySpec, destination: Path) -> dict[str, Any]:
    """Freeze inputs, explicit budgets and expanded order without launching work."""
    data = spec.model_dump(mode="json")
    for key in ("skill_bundle", "runtime", "image", "catalog", "admission"):
        if data[key] is not None:
            data[key] = os.path.abspath(data[key])
    for task in data["tasks"]:
        task["input_bundle"] = os.path.abspath(task["input_bundle"])
    spec = StudySpec.model_validate_json(json.dumps(data))
    image = _check_inputs(spec)
    identity = snapshot_identity(spec.runtime)
    destination = Path(os.path.abspath(destination))
    _outside_inputs(spec, destination)
    skill_manifest, _ = _verified_skill(Path(spec.skill_bundle), spec.skill_sha256)
    skill_name = skill_manifest["meta"]["skill_name"]
    value: dict[str, Any] = {
        "schema_version": 3,
        "kind": "skilleval-study-plan",
        "spec": spec.model_dump(mode="json"),
        "model": MODEL,
        "effort": EFFORT,
        "identity": identity,
        "image": image,
        "assignments": _matrix(spec),
        "skill_name": skill_name,
        "invocations": _invocations(spec, skill_name),
    }
    digest = _new(destination, value)
    cells = len(value["assignments"])
    return {
        "status": "prepared",
        "plan": str(destination),
        "sha256": digest,
        "scope": spec.scope,
        "cells": cells,
        "reserved_attempts": cells * spec.max_attempts_per_cell,
        "maximum_provider_requests": cells
        * spec.max_attempts_per_cell
        * spec.max_requests_per_attempt,
    }


def _outside_inputs(spec: StudySpec, target: Path) -> None:
    roots = [Path(task.input_bundle) for task in spec.tasks]
    roots += [Path(spec.skill_bundle), Path(spec.runtime).parent]
    if any(
        target.resolve() == root.resolve() or target.resolve().is_relative_to(root.resolve())
        for root in roots
    ):
        raise SkillEvalError("study output must be outside input and runtime inventories")


def _plan(path: Path, expected: str) -> tuple[dict[str, Any], StudySpec]:
    raw = _read_regular(path, 16 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise SkillEvalError("study plan digest differs")
    value = evidence._parse_json(raw)
    if not isinstance(value, dict):
        raise SkillEvalError("expected a study JSON object")
    if value.get("kind") != "skilleval-study-plan" or value.get("schema_version") not in {1, 2, 3}:
        raise SkillEvalError("unsupported study plan")
    data = dict(value["spec"])
    if value["schema_version"] == 1:
        if "invocation_policy" in data or "price_schedule" in data:
            raise SkillEvalError("legacy plans cannot declare new treatment or pricing policies")
        data["invocation_policy"] = "natural-availability"
    elif "invocation_policy" not in data or "price_schedule" not in data:
        raise SkillEvalError("new plans must freeze invocation and pricing policies")
    if value["schema_version"] < 3 and any("mode" in task for task in data["tasks"]):
        raise SkillEvalError("legacy study plans cannot declare task modes")
    if value["schema_version"] == 3 and any("mode" not in task for task in data["tasks"]):
        raise SkillEvalError("new study plans must freeze task modes")
    spec = StudySpec.model_validate_json(json.dumps(data))
    if value["schema_version"] >= 2 and value.get("invocations") != _invocations(
        spec, value.get("skill_name", "")
    ):
        raise SkillEvalError("frozen invocation prompts differ from treatment policy")
    if value["assignments"] != _matrix(spec) or (value["model"], value["effort"]) != (
        MODEL,
        EFFORT,
    ):
        raise SkillEvalError("study matrix or native model contract differs")
    return value, spec


def _dispatch_check(plan: dict[str, Any], spec: StudySpec) -> dict[str, Any] | None:
    if snapshot_identity(spec.runtime) != plan["identity"] or _check_inputs(spec) != plan["image"]:
        raise SkillEvalError("study inputs or execution identity changed; prepare a new plan")
    if plan["schema_version"] >= 2:
        manifest, _ = _verified_skill(Path(spec.skill_bundle), spec.skill_sha256)
        if manifest["meta"]["skill_name"] != plan["skill_name"]:
            raise SkillEvalError("frozen skill invocation name differs from captured package")
    if spec.admission is None:
        return None
    assert spec.admission_sha256 is not None
    checked = check_admission(Path(spec.admission), spec.admission_sha256)
    seal = _document(Path(spec.admission))
    qualified_plan = _document(Path(seal["inputs"]["plan"]))
    if (
        seal["bindings"]["identity"] != plan["identity"]
        or qualified_plan["image_archive"] != plan["image"]
        or seal["bindings"]["catalog_sha256"] != file_digest(Path(spec.catalog))
    ):
        raise SkillEvalError("admission does not cover this runtime/image/catalog route")
    # A bounded seal remains bounded; it is never promoted to task/grader admission.
    return checked


@contextmanager
def _lock(output: Path) -> Iterator[None]:
    with evidence._directory(output) as directory:
        descriptor = os.open(
            ".study.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=directory
        )
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SkillEvalError("another controller owns this study output") from None
        yield
    finally:
        os.close(descriptor)


def _attempts(output: Path, cell: dict[str, Any]) -> list[Path]:
    parent = output / "attempts" / cell["cell_id"]
    if not parent.exists():
        return []
    with evidence._directory(parent):
        # A pending directory cannot have launched native work: publication of
        # its complete dispatch record happens before calling the runner.
        paths = sorted(p for p in parent.iterdir() if not p.name.startswith(".pending-"))
        if [p.name for p in paths] != [f"attempt-{i:03}" for i in range(1, len(paths) + 1)]:
            raise SkillEvalError("study attempt sequence is invalid")
        for path in paths:
            with evidence._directory(path):
                pass
    return paths


def _terminal(path: Path, plan_sha: str, cell: dict[str, Any]) -> dict[str, Any]:
    dispatch = _document(path / "dispatch.json")
    if dispatch.get("plan_sha256") != plan_sha or dispatch.get("assignment") != cell:
        raise SkillEvalError("attempt belongs to a different plan or assignment")
    if not (path / "outcome.json").exists():
        raise SkillEvalError(
            "unfinished attempt; stop its controller, recover its VM, then reconcile"
        )
    outcome = _document(path / "outcome.json")
    if outcome.get("dispatch_sha256") != file_digest(path / "dispatch.json"):
        raise SkillEvalError("attempt outcome does not bind its dispatch")
    receipt = outcome.get("native_receipt_sha256")
    if receipt is not None:
        if file_digest(path / "native/native-run.json") != receipt:
            raise SkillEvalError("native attempt receipt changed")
        report = _document(path / "native/native-run.json")
        _check_native(path, report)
        if any(outcome[key] != value for key, value in _summary_fields(report).items()):
            raise SkillEvalError("attempt summary differs from native evidence")
    if "cost" in outcome:
        native = _document(path / "native/native-run.json") if receipt is not None else None
        if outcome["cost"] != _cost(path, native):
            raise SkillEvalError("attempt cost differs from frozen rates and usage evidence")
    if (path / "reconciled-recovery.json").exists():
        recovery = _document(path / "reconciled-recovery.json")
        if (
            recovery.get("outcome_sha256") != file_digest(path / "outcome.json")
            or recovery.get("ticket_sha256") != file_digest(path / "native/recovery.json")
            or not _clean_native(path / "native")
        ):
            raise SkillEvalError("reconciled recovery evidence changed")
        outcome = {**outcome, "cleanup_complete": True, "recovered_after_attempt": True}
    return outcome


def _clean_native(native: Path) -> bool:
    if not (native / "recovery.json").exists():
        # The native runner durably registers before the first VM mutation.
        return True
    ticket = _document(native / "recovery.json")
    return (
        ticket.get("kind") == "skilleval-lifecycle-recovery"
        and ticket.get("output") == str(native)
        and ticket.get("cleanup_complete") is True
        and ticket.get("runtime_home_removed") is True
        and ticket.get("phase") == "recovered"
        and not os.path.lexists(ticket["runtime_home"])
        and (
            ticket.get("create_attempted") is False
            or (ticket.get("observer_positive") is True and ticket.get("observed_absent") is True)
        )
    )


def _summary_fields(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report["status"],
        "cleanup_complete": report.get("cleanup_complete") is True,
        "usage": report.get("usage"),
        "request_count": report.get("request_count"),
        "skill_consultation": report.get("skill_consultation"),
        "harness_identity": report.get("codex_identity"),
        **{key: report[key] for key in ("usage_accounting", "timings") if key in report},
    }


def _cost(path: Path, report: dict[str, Any] | None) -> dict[str, Any]:
    schedule = _document(path / "dispatch.json").get("price_schedule")
    usage = (report or {}).get("usage_accounting", {"model": MODEL, "status": "unknown"})
    return estimate_api_equivalent_cost(
        usage,
        PriceSchedule.model_validate_json(json.dumps(schedule)) if schedule is not None else None,
        model=MODEL,
    )


def _check_native(path: Path, report: dict[str, Any]) -> None:
    expected = _document(path / "dispatch.json")["native_expected"]
    if any(
        report.get(key) != value
        for key, value in expected.items()
        if key not in {"skill_sha256", "identity_sha256"}
    ):
        raise SkillEvalError("native receipt differs from dispatched inputs")
    from skilleval.judge import _native_audit

    audit_raw = _native_audit(path / "native", report)
    native_identity = (
        evidence._parse_json(audit_raw)["identity"]
        if audit_raw is not None
        else report.get("identity")
    )
    if object_digest(native_identity) != expected["identity_sha256"]:
        raise SkillEvalError("native execution identity differs from study plan")
    treatment = report.get("skill_treatment")
    # A failed setup may precede treatment installation; it cannot become a
    # completed execution through that missing evidence.
    if report.get("status") == "completed":
        from skilleval.judge import _run_files

        _run_files(path / "native", mode=expected["mode"])
        if (
            not isinstance(treatment, dict)
            or treatment.get("manifest_sha256") != expected["skill_sha256"]
        ):
            raise SkillEvalError("native treatment differs from dispatched skill")


def _record_outcome(
    path: Path, report: dict[str, Any] | None, *, elapsed: float | None, error: str | None = None
) -> dict[str, Any]:
    receipt_path = path / "native/native-run.json"
    # KeyboardInterrupt can propagate after the runner's finally has durably
    # published a receipt. Preserve its actual fields rather than inventing an
    # interrupted summary that contradicts the recorded native result.
    if report is None and receipt_path.exists():
        report = _document(receipt_path)
    if report is not None:
        retained = _document(receipt_path)
        if retained != report:
            raise SkillEvalError("returned native result differs from retained receipt")
        _check_native(path, report)
    value = {
        "schema_version": 1,
        "dispatch_sha256": file_digest(path / "dispatch.json"),
        "native_receipt_sha256": file_digest(receipt_path) if receipt_path.exists() else None,
        "status": "interrupted"
        if error in {"controller-interrupted", "reconciled-after-controller-interruption"}
        else "dispatch-failed",
        "cleanup_complete": _clean_native(path / "native"),
        "elapsed_seconds": elapsed,
        "usage": report.get("usage") if report else None,
        "request_count": report.get("request_count") if report else None,
        "skill_consultation": report.get("skill_consultation") if report else None,
        "harness_identity": report.get("codex_identity") if report else None,
        "task_quality": "unassessed",
        "error_kind": error,
        "cost": _cost(path, report),
    }
    if report is not None:
        value.update(_summary_fields(report))
    _new(path / "outcome.json", value)
    return value


@interrupt_scope()
def run_study(
    plan_path: Path,
    expected_sha256: str,
    output: Path,
    *,
    auth_file: Path,
    development: bool = False,
) -> dict[str, Any]:
    if not development:
        raise SkillEvalError(
            "scored dispatch unavailable: human audit and full-workload admission are pending; "
            "explicitly select development"
        )
    plan_path, output = Path(os.path.abspath(plan_path)), Path(os.path.abspath(output))
    plan, spec = _plan(plan_path, expected_sha256)
    _outside_inputs(spec, output)
    with evidence._directory(output.parent):
        output.mkdir(mode=0o700, exist_ok=True)
    with _lock(output):
        binding = {"schema_version": 1, "plan": str(plan_path), "plan_sha256": expected_sha256}
        if (output / "study.json").exists():
            if _document(output / "study.json") != binding:
                raise SkillEvalError("study output is already bound to a different plan")
        else:
            if (output / "attempts").exists():
                raise SkillEvalError("unbound study attempt directories")
            _new(output / "study.json", binding)
        tasks = {task.task_id: task for task in spec.tasks}
        for cell in plan["assignments"]:
            attempts = _attempts(output, cell)
            if len(attempts) > spec.max_attempts_per_cell:
                raise SkillEvalError("recorded attempts exceed the frozen study budget")
            previous = [_terminal(path, expected_sha256, cell) for path in attempts]
            if any(not value["cleanup_complete"] for value in previous):
                raise SkillEvalError(
                    "incomplete VM cleanup; recover and reconcile before continuing"
                )
            if any(value["status"] == "completed" for value in previous):
                continue
            while len(attempts) < spec.max_attempts_per_cell:
                # Recheck before EACH dispatch, including retries, not just at planning.
                _plan(plan_path, expected_sha256)
                admission = _dispatch_check(plan, spec)
                path = output / "attempts" / cell["cell_id"] / f"attempt-{len(attempts) + 1:03}"
                task = tasks[cell["task_id"]]
                prompt = (
                    plan["invocations"][task.task_id][cell["arm"]]
                    if plan["schema_version"] >= 2
                    else task.prompt
                )
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                expected = {
                    "mode": task.mode,
                    "input_digest": task.input_sha256,
                    "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                    "model": MODEL,
                    "effort": EFFORT,
                    "image": plan["image"],
                    "native_timeout_seconds": spec.native_timeout_seconds,
                    "identity_sha256": object_digest(plan["identity"]),
                    "skill_sha256": spec.skill_sha256 if cell["arm"] == "available" else None,
                }
                with (
                    evidence._directory(path.parent),
                    tempfile.TemporaryDirectory(prefix=".pending-", dir=path.parent) as temporary,
                ):
                    _new(
                        Path(temporary) / "dispatch.json",
                        {
                            "schema_version": 1,
                            "plan_sha256": expected_sha256,
                            "assignment": cell,
                            "admission": admission,
                            "scope": "development-only",
                            "native_expected": expected,
                            "invocation_policy": spec.invocation_policy,
                            "price_schedule": spec.price_schedule.model_dump(mode="json")
                            if spec.price_schedule is not None
                            else None,
                        },
                    )
                    evidence._publish(Path(temporary), path)
                    with evidence._directory(path.parent) as directory:
                        os.fsync(directory)
                attempts.append(path)
                started = time.monotonic()
                report = None
                interrupted = False
                error = None
                try:
                    report = runner.run_native(
                        Path(task.input_bundle),
                        task.input_sha256,
                        prompt,
                        path / "native",
                        runtime=Path(spec.runtime),
                        image=Path(spec.image),
                        image_sha256=spec.image_sha256,
                        catalog=Path(spec.catalog),
                        auth_file=auth_file,
                        mode=task.mode,
                        native_timeout_seconds=spec.native_timeout_seconds,
                        limits=GatewayLimits(
                            max_requests=spec.max_requests_per_attempt,
                            timeout_seconds=90,
                            lifetime_seconds=900,
                        ),
                        skill_bundle=Path(spec.skill_bundle)
                        if cell["arm"] == "available"
                        else None,
                        skill_digest=spec.skill_sha256 if cell["arm"] == "available" else None,
                    )
                    interrupted = report.get("error") == "LifecycleInterruptedError"
                    if interrupted:
                        error = "controller-interrupted"
                except (KeyboardInterrupt, LifecycleInterruptedError):
                    interrupted = True
                    error = "controller-interrupted"
                except (OSError, SkillEvalError) as exc:
                    error = type(exc).__name__
                outcome = _record_outcome(
                    path, report, elapsed=time.monotonic() - started, error=error
                )
                if interrupted or not outcome["cleanup_complete"]:
                    return report_study(plan_path, expected_sha256, output)
                if outcome["status"] == "completed":
                    break
    return report_study(plan_path, expected_sha256, output)


def report_study(plan_path: Path, expected_sha256: str, output: Path) -> dict[str, Any]:
    plan_path, output = Path(os.path.abspath(plan_path)), Path(os.path.abspath(output))
    plan, spec = _plan(plan_path, expected_sha256)
    _check_output_binding(plan_path, expected_sha256, output)
    cells = []
    pairs: dict[str, dict[str, Any]] = {}
    for cell in plan["assignments"]:
        rows = []
        for path in _attempts(output, cell):
            if (path / "outcome.json").exists():
                outcome = _terminal(path, expected_sha256, cell)
            else:
                dispatch = _document(path / "dispatch.json")
                if (
                    dispatch.get("plan_sha256") != expected_sha256
                    or dispatch.get("assignment") != cell
                ):
                    raise SkillEvalError("unfinished attempt binding differs")
                outcome = {
                    "status": "unfinished",
                    "cleanup_complete": False,
                    "task_quality": "unassessed",
                }
            rows.append({"path": str(path), **outcome})
        first = rows[0] if rows else None
        primary = (
            first
            if first and first["status"] == "completed" and first["cleanup_complete"]
            else None
        )
        cells.append(
            {
                **cell,
                "attempts": rows,
                "primary": primary,
                "primary_status": first["status"] if first else "not-started",
            }
        )
        pairs.setdefault(cell["pair_id"], {})[cell["arm"]] = primary
    paired: list[dict[str, Any]] = []
    for pair_id, arms in pairs.items():
        both = all(arms.get(arm) is not None for arm in ("absent", "available"))
        comparable = (
            both
            and arms["available"]["harness_identity"] == arms["absent"]["harness_identity"]
            and arms["available"]["harness_identity"] is not None
        )
        timed = comparable and all(
            arms[arm]["elapsed_seconds"] is not None for arm in ("absent", "available")
        )
        paired.append(
            {
                "pair_id": pair_id,
                "both_primary_executions_completed": both,
                "harness_comparable": comparable,
                "elapsed_seconds_difference": arms["available"]["elapsed_seconds"]
                - arms["absent"]["elapsed_seconds"]
                if timed
                else None,
                "task_quality_difference": None,
                "efficiency": pair_efficiency(
                    arms.get("available"), arms.get("absent"), comparable=comparable
                ),
            }
        )
    settled = all(
        row["attempts"]
        and (
            row["attempts"][-1]["status"] == "completed"
            or len(row["attempts"]) >= spec.max_attempts_per_cell
        )
        and all(a["cleanup_complete"] for a in row["attempts"])
        for row in cells
    )
    return {
        "schema_version": 1,
        "status": "complete" if settled else "partial",
        "scope": "development-only",
        "scored_admitted": False,
        "human_audit": "required-pending",
        "primary_attempt_policy": spec.primary_attempt_policy,
        "invocation_policy": spec.invocation_policy,
        "cells": cells,
        "pairs": paired,
        "efficiency_ledger": efficiency_ledger(cells),
        "pair_coverage": {
            "planned": len(paired),
            "harness_comparable": sum(pair["harness_comparable"] for pair in paired),
            "known_token_delta": sum(
                pair["efficiency"]["token_deltas"]["total_tokens"] is not None for pair in paired
            ),
            "known_cost_delta": sum(
                pair["efficiency"]["estimated_api_equivalent_usd_delta"] is not None
                for pair in paired
            ),
            "known_agent_time_delta": sum(
                pair["efficiency"]["timing_deltas_seconds"]["agent_seconds"] is not None
                for pair in paired
            ),
        },
        "planned_cells": len(cells),
        "primary_completed_cells": sum(c["primary"] is not None for c in cells),
        "limits": [
            "Execution completion is not correctness or skill effectiveness.",
            "Elapsed time includes VM setup and teardown; missing usage stays unknown.",
            "Stage timings separate native subprocess time from host setup and cleanup.",
            "Cost is a conditional API-equivalent estimate, not an observed charge.",
            "Descriptive efficiency differences do not establish quality or statistical effects.",
            "Retries remain separate and never replace the first attempt in primary comparisons.",
        ],
    }


def _check_output_binding(plan_path: Path, expected: str, output: Path) -> None:
    if _document(output / "study.json") != {
        "schema_version": 1,
        "plan": str(plan_path),
        "plan_sha256": expected,
    }:
        raise SkillEvalError("study output belongs to a different plan")


def reconcile_study(plan_path: Path, expected_sha256: str, output: Path) -> dict[str, Any]:
    """Record interrupted attempts only after explicit VM recovery; never runs a VM."""
    plan_path, output = Path(os.path.abspath(plan_path)), Path(os.path.abspath(output))
    plan, _ = _plan(plan_path, expected_sha256)
    with _lock(output):
        _check_output_binding(plan_path, expected_sha256, output)
        for cell in plan["assignments"]:
            for path in _attempts(output, cell):
                dispatch = _document(path / "dispatch.json")
                if (
                    dispatch.get("plan_sha256") != expected_sha256
                    or dispatch.get("assignment") != cell
                ):
                    raise SkillEvalError("interrupted attempt binding differs")
                if (path / "outcome.json").exists():
                    if not _terminal(path, expected_sha256, cell)["cleanup_complete"]:
                        if not _clean_native(path / "native"):
                            raise SkillEvalError(
                                "recover the native VM before reconciling the attempt"
                            )
                        _new(
                            path / "reconciled-recovery.json",
                            {
                                "schema_version": 1,
                                "outcome_sha256": file_digest(path / "outcome.json"),
                                "ticket_sha256": file_digest(path / "native/recovery.json"),
                            },
                        )
                    continue
                if not _clean_native(path / "native"):
                    raise SkillEvalError("recover the native VM before reconciling the attempt")
                report = (
                    _document(path / "native/native-run.json")
                    if (path / "native/native-run.json").exists()
                    else None
                )
                outcome = _record_outcome(
                    path, report, elapsed=None, error="reconciled-after-controller-interruption"
                )
                if not outcome["cleanup_complete"]:
                    _new(
                        path / "reconciled-recovery.json",
                        {
                            "schema_version": 1,
                            "outcome_sha256": file_digest(path / "outcome.json"),
                            "ticket_sha256": file_digest(path / "native/recovery.json"),
                        },
                    )
    return report_study(plan_path, expected_sha256, output)
