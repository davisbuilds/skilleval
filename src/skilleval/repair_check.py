"""Offline VM observations with expected values and verdict authority on the host."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from skilleval import evidence, repair_guest, runner
from skilleval.errors import SkillEvalError
from skilleval.grading import _read_regular
from skilleval.identity import object_digest
from skilleval.lifecycle import Lifecycle, LifecycleInterruptedError, interrupt_scope
from skilleval.repair import _publish, load_task, result_files


def compare_observations(probes: list[dict[str, Any]], raw: str) -> list[dict[str, Any]]:
    value = evidence._parse_json(raw.encode())
    if (
        not isinstance(value, dict)
        or set(value) != {"observations"}
        or not isinstance(value["observations"], list)
        or len(value["observations"]) != len(probes)
    ):
        raise SkillEvalError("incomplete repair observation protocol")
    return [
        {
            "probe_id": probe["probe_id"],
            "observed": observed,
            "expected": probe["expected"],
            "pass": object_digest(observed) == object_digest(probe["expected"]),
        }
        for probe, observed in zip(probes, value["observations"], strict=True)
    ]


@interrupt_scope()
def check_repair(
    task: Path,
    task_digest: str,
    result_dir: Path,
    result_digest: str,
    output: Path,
    *,
    runtime: Path,
    image: Path,
    image_sha256: str,
) -> dict[str, Any]:
    spec = load_task(task, task_digest)
    result, files = result_files(spec, task_digest, result_dir, result_digest)
    if any(
        output.resolve().is_relative_to(root.resolve())
        for root in (result_dir, Path(spec.input_bundle), runtime.resolve().parent)
    ):
        raise SkillEvalError("checker output must be outside repair inputs and runtime inventory")
    session = Lifecycle.prepare(output, runtime, image, image_sha256)
    state = Path(session.ticket["runtime_state"])
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "skilleval-repair-check",
        "status": "incomplete",
        "task_sha256": task_digest,
        "repair_result_sha256": result_digest,
        "candidate_sha256": result["candidate_sha256"],
        "worker_sha256": spec.worker_sha256,
        "identity": session.ticket["identity"],
        "image": session.ticket["image"],
        "scope": "declared-development-checks",
        "scored_admitted": False,
        "task_quality": "unassessed",
        "semantic_assessment": "unassessed",
        "accepted_by_checks": None,
        "checks": [],
        "inference_requests": 0,
        "limits": [
            "Finite checks do not establish exhaustive correctness or skill benefit.",
            "Observation code shares a process with candidate imports; observations are untrusted.",
            "Expected answers and verdict computation stay outside the guest.",
        ],
    }
    try:
        requests = {
            "timeout_seconds": spec.timeout_seconds,
            "requests": [p.request for p in spec.probes],
        }
        archive = state / "checker-input.tar"
        runner._tar(
            archive,
            [("task/" + n, b, x) for n, b, x in files]
            + [
                ("native/controller.py", Path(repair_guest.__file__).read_bytes(), False),
                ("native/worker.py", _read_regular(Path(spec.worker), 1024 * 1024), False),
                ("native/requests.json", evidence._json_bytes(requests), False),
            ],
        )
        session.create()
        runner._required(
            session.invoke(
                "copy_checker_input",
                ["machine", "cp", str(archive), session.machine + ":/var/tmp/checker-input.tar"],
            ),
            "copy checker input",
        )
        runner._required(
            session.guest(
                "prepare_checker",
                runner.preparation_argv(
                    Path("/var/tmp/checker-input.tar"), Path("/workspace/task"), Path("/run/native")
                ),
            ),
            "prepare checker",
        )
        response = session.guest(
            "observe_candidate",
            ["python3", "/run/native/controller.py", "/run/native", "/workspace/task"],
            spec.timeout_seconds + 30,
        )
        runner._required(response, "offline observation process")
        envelope = evidence._parse_json(response["stdout"].encode())
        report["process"] = envelope
        if (
            not isinstance(envelope, dict)
            or envelope.get("schema_version") != 1
            or envelope.get("worker_uid") != 65534
            or envelope.get("expected_answers_supplied") is not False
        ):
            raise SkillEvalError("invalid repair supervisor protocol")
        if (
            envelope.get("returncode") != 0
            or envelope.get("timed_out") is not False
            or envelope.get("output_limit_reached") is not False
            or not isinstance(envelope.get("stdout"), str)
        ):
            raise SkillEvalError("repair observation process incomplete")
        checks = compare_observations(
            [p.model_dump(mode="json") for p in spec.probes], envelope["stdout"]
        )
        report.update(
            status="completed", checks=checks, accepted_by_checks=all(c["pass"] for c in checks)
        )
        load_task(task, task_digest)
        result_files(spec, task_digest, result_dir, result_digest)
    except (SkillEvalError, OSError, ValueError, KeyError, LifecycleInterruptedError) as error:
        report["error"] = str(error) if isinstance(error, SkillEvalError) else type(error).__name__
        report.update(status="incomplete", accepted_by_checks=None)
    finally:
        ticket = session.cleanup()
        report["cleanup_complete"] = ticket["cleanup_complete"]
        report["runtime_home_removed"] = ticket["runtime_home_removed"]
        if not ticket["cleanup_complete"]:
            report.update(status="incomplete", accepted_by_checks=None)
        report["recovery"] = "recovery.json"
        _publish(output / "repair-check.json", report)
    return report
