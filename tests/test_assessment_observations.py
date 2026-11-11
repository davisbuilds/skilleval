"""Authored receipts test evidence binding; they do not qualify a VM."""

import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from test_repair import task as task
from test_runner import route as route
from test_task_package import package_spec, solver_process

from skilleval import repair, task_package
from skilleval.assessment_observations import AssessmentObservationSource, observation_payloads
from skilleval.errors import SkillEvalError
from skilleval.identity import file_digest


def archive(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for name, content in files.items():
            target.writestr(name, content)
    raw = buffer.getvalue()
    return {
        "schema_version": 1,
        "kind": "operational-metrics-downstream-envelope",
        "encoding": "zip+base64",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "base64": base64.b64encode(raw).decode(),
        "summary": {"semantic_verdict": "unassessed"},
    }


@pytest.fixture
def observed(task, route, tmp_path):
    lane = task_package.ValidationLane(
        lane_id="browser",
        purpose="Authored observer",
        argv=("python3", "-c", "print(1)"),
        parser="json",
        expected_exit_code=0,
        expected={"SECRET_CONTROL_LABEL": True},
    )
    spec = package_spec(task, route)
    spec = spec.model_copy(
        update={
            "environment": spec.environment.model_copy(update={"profile": "solver-isolated-v1"}),
            "validation": (spec.validation[0].model_copy(update={"lanes": (lane,)}),),
        }
    )
    package = task_package.prepare_package(spec, tmp_path / "package.json")
    raw = archive({"observation.json": '{"rendered":"2 stored points; value 9"}\n'})
    process = solver_process(argv=list(lane.argv), stdout=json.dumps(raw), stderr="observer log")
    compared = task_package.compare_lane(lane, process, "solver-isolated-v1")
    directory = tmp_path / "run/base"
    directory.mkdir(parents=True)
    recovery = {
        "kind": "skilleval-lifecycle-recovery",
        "output": str(directory),
        "phase": "recovered",
        "cleanup_complete": True,
        "runtime_home_removed": True,
        "runtime_home": str(tmp_path / "removed-home"),
        "create_attempted": True,
        "observer_positive": True,
        "observed_absent": True,
        "identity": {"source": "authored"},
        "image": {"sha256": route["image_sha256"]},
    }
    row = {
        "case_id": "base",
        "role": "baseline",
        "input_sha256": task["spec"].input_sha256,
        "status": "unexpected",
        "identity": recovery["identity"],
        "image": recovery["image"],
        "cleanup_complete": True,
        "lanes": [compared],
    }
    repair._publish(directory / "lane-browser.json", process)
    repair._publish(directory / "validation.json", row)
    repair._publish(directory / "recovery.json", recovery)
    report = tmp_path / "run/task-validation.json"
    repair._publish(
        report,
        {
            "schema_version": 1,
            "kind": "skilleval-task-validation",
            "package_sha256": package["sha256"],
            "family": "repair",
            "cases": [row],
            "status": "incomplete",
            "inference_requests": 0,
            "scored_admitted": False,
            "coverage_gaps": list(spec.environment.coverage_gaps),
            "limitations": list(spec.limitations),
        },
    )
    reference = AssessmentObservationSource(
        package=package["path"],
        package_sha256=package["sha256"],
        report=str(report),
        report_sha256=file_digest(report),
        case_id="base",
        lane_id="browser",
    )
    return reference, task, directory, raw


def payloads(observed, **updates):
    ref, task, _, _ = observed
    return observation_payloads(
        (ref,),
        task_path=task["task"],
        task_digest=task["digest"],
        candidate_digest=updates.get("candidate_digest", task["spec"].input_sha256),
    )


def test_export_preserves_actual_observations_without_host_expected_labels(observed):
    exported = payloads(observed)
    assert (
        exported["case/observations/observation-001/artifacts/observation.json"]
        == b'{"rendered":"2 stored points; value 9"}\n'
    )
    text = b"\n".join(exported.values())
    assert b"SECRET_CONTROL_LABEL" not in text
    assert b"2 stored points" in text
    assert b"expected_exit_code" not in text
    assert b"passed" not in text


def test_other_candidate_and_changed_raw_lane_are_rejected(observed):
    with pytest.raises(SkillEvalError, match="candidate"):
        payloads(observed, candidate_digest="a" * 64)
    _, _, directory, _ = observed
    raw = json.loads((directory / "lane-browser.json").read_text())
    raw["stdout"] = "{}"
    (directory / "lane-browser.json").write_text(json.dumps(raw))
    with pytest.raises(SkillEvalError, match="observation"):
        payloads(observed)


@pytest.mark.parametrize("name", ["../escape", "/absolute", "a/../../escape", "x\\y"])
def test_archive_member_escape_rejected(observed, name):
    from skilleval.assessment_observations import _archive_payloads

    with pytest.raises(SkillEvalError):
        _archive_payloads(archive({name: "bad"}))


def test_archive_hash_drift_and_decompression_limit_are_rejected():
    from skilleval.assessment_observations import _archive_payloads

    value = archive({"evidence.txt": "bounded"})
    with pytest.raises(SkillEvalError, match="digest"):
        _archive_payloads(value | {"sha256": "a" * 64})
    with pytest.raises(SkillEvalError, match="limit"):
        _archive_payloads(archive({"large.txt": "x" * (4 * 1024 * 1024 + 1)}))


def test_consistent_wrong_image_in_retained_rows_still_violates_package(observed):
    ref, _, directory, _ = observed
    report = json.loads(Path(ref.report).read_text())
    row = report["cases"][0]
    row["image"]["sha256"] = "b" * 64
    (directory / "validation.json").write_text(json.dumps(row))
    recovery = json.loads((directory / "recovery.json").read_text())
    recovery["image"] = row["image"]
    (directory / "recovery.json").write_text(json.dumps(recovery))
    Path(ref.report).write_text(json.dumps(report))
    ref = ref.model_copy(update={"report_sha256": file_digest(Path(ref.report))})
    with pytest.raises(SkillEvalError, match="image"):
        payloads((ref, *observed[1:]))
