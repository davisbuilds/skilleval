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
from skilleval.identity import environment_identity, file_digest, object_digest, snapshot_identity


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


def version_two(observed, route):
    """Add the explicit v2 provenance contract without changing observed execution."""
    ref, *rest = observed
    report = json.loads(Path(ref.report).read_text())
    package = task_package.load_package(Path(ref.package), ref.package_sha256)
    contract = task_package.execution_contract(package)
    row = report["cases"][0]
    row["identity"] = snapshot_identity(str(route["runtime"]))
    row["readiness_identity"] = environment_identity(row["identity"], package.environment.profile)
    directory = rest[1]
    (directory / "validation.json").write_text(json.dumps(row))
    recovery_path = directory / "recovery.json"
    recovery = json.loads(recovery_path.read_text())
    recovery["identity"] = row["identity"]
    recovery_path.write_text(json.dumps(recovery))
    report.update(
        schema_version=2,
        package=ref.package,
        execution_contract=contract,
        execution_contract_sha256=object_digest(contract),
    )
    Path(ref.report).write_text(json.dumps(report))
    return (ref.model_copy(update={"report_sha256": file_digest(Path(ref.report))}), *rest)


def test_v2_unexpected_observation_replays_without_live_readiness(observed, route):
    expected = payloads(observed)
    selected = version_two(observed, route)
    # Neither a changed live runtime nor a retired checker alters retained observations.
    Path(route["runtime"]).write_text("changed runtime must never execute")
    Path(selected[1]["spec"].worker).unlink()
    assert payloads(selected) == expected


@pytest.mark.parametrize("binding", ["package", "contract", "contract_digest"])
def test_v2_observation_rejects_changed_original_package_or_execution_contract(
    observed, route, binding
):
    selected = version_two(observed, route)
    assert payloads(selected)
    ref, *rest = selected
    report = json.loads(Path(ref.report).read_text())
    if binding == "package":
        report["package"] = str(Path(ref.package).with_name("foreign-package.json"))
    elif binding == "contract":
        report["execution_contract"] = {"image_sha256": "f" * 64}
        report["execution_contract_sha256"] = object_digest(report["execution_contract"])
    else:
        report["execution_contract_sha256"] = "f" * 64
    Path(ref.report).write_text(json.dumps(report))
    ref = ref.model_copy(update={"report_sha256": file_digest(Path(ref.report))})
    with pytest.raises(SkillEvalError, match=r"observation.*binding"):
        payloads((ref, *rest))


@pytest.mark.parametrize("proof", ["missing", "changed", "incomplete_source"])
def test_v2_observation_requires_original_captured_readiness_proof(observed, route, proof):
    selected = version_two(observed, route)
    assert payloads(selected)
    ref, task, directory, raw = selected
    report = json.loads(Path(ref.report).read_text())
    row = report["cases"][0]
    if proof == "missing":
        del row["readiness_identity"]
    elif proof == "changed":
        row["readiness_identity"]["runtime"] = {"changed": True}
    else:
        row["identity"]["sources"] = {}
        recovery_path = directory / "recovery.json"
        recovery = json.loads(recovery_path.read_text())
        recovery["identity"] = row["identity"]
        recovery_path.write_text(json.dumps(recovery))
    (directory / "validation.json").write_text(json.dumps(row))
    Path(ref.report).write_text(json.dumps(report))
    ref = ref.model_copy(update={"report_sha256": file_digest(Path(ref.report))})
    with pytest.raises(SkillEvalError):
        payloads((ref, task, directory, raw))


def test_v2_raw_observation_export_survives_changed_host_interpretation(
    observed, route, monkeypatch
):
    selected = version_two(observed, route)
    expected = payloads(selected)
    original = task_package.observations

    def changed_interpretation(lane, process):
        return {"updated_parser": original(lane, process)}

    monkeypatch.setattr(task_package, "observations", changed_interpretation)
    assert payloads(selected) == expected


def version_three(observed, route, *, source_status="unexpected"):
    from skilleval import lane_readiness

    ref, task, old_directory, raw = version_two(observed, route)
    spec = task_package.read_package(Path(ref.package), ref.package_sha256)
    spec = spec.model_copy(
        update={
            "environment": spec.environment.model_copy(update={"validation_isolation": "lane-v1"})
        }
    )
    package = task_package.prepare_package(spec, Path(ref.package).with_name("lane-package.json"))
    ref = ref.model_copy(update={"package": package["path"], "package_sha256": package["sha256"]})
    directory = old_directory.parent / "executions" / ref.case_id / ref.lane_id
    directory.parent.mkdir(parents=True)
    old_directory.rename(directory)
    row = json.loads((directory / "validation.json").read_text())
    row["status"] = source_status
    row["readiness_identity"] = environment_identity(
        row["identity"], spec.environment.profile, isolation="lane-v1"
    )
    (directory / "validation.json").write_text(json.dumps(row))
    recovery_path = directory / "recovery.json"
    recovery = json.loads(recovery_path.read_text())
    recovery["output"] = str(directory)
    recovery_path.write_text(json.dumps(recovery))
    case = spec.validation[0]
    receipt = directory / "lane-execution.json"
    repair._publish(
        receipt,
        lane_readiness.execution_receipt(
            spec, Path(ref.package), ref.package_sha256, case, case.lanes[0], row
        ),
    )
    selected = [
        {
            "case_id": ref.case_id,
            "lane_id": ref.lane_id,
            "action": "execute",
            "reason": "authored observation",
            "source": {"receipt": str(receipt), "sha256": file_digest(receipt)},
        }
    ]
    report = lane_readiness.validation_report(spec, Path(ref.package), ref.package_sha256, selected)
    Path(ref.report).write_text(json.dumps(report))
    return (
        ref.model_copy(update={"report_sha256": file_digest(Path(ref.report))}),
        task,
        directory,
        raw,
    )


def test_v3_unexpected_observation_exports_raw_evidence_without_live_readiness(observed, route):
    expected = payloads(observed)
    selected = version_three(observed, route)
    Path(route["runtime"]).write_text("retained evidence must not execute")
    Path(selected[1]["spec"].worker).unlink()
    assert payloads(selected) == expected


@pytest.mark.parametrize("binding", ["package", "package_digest", "process", "source_digest"])
def test_v3_observation_rejects_changed_selection_bindings(observed, route, binding):
    selected = version_three(observed, route)
    assert payloads(selected)
    ref, *rest = selected
    report = json.loads(Path(ref.report).read_text())
    lane = report["cases"][0]["lanes"][0]
    if binding == "package":
        ref = ref.model_copy(update={"package": str(Path(ref.package).with_name("foreign.json"))})
    elif binding == "package_digest":
        ref = ref.model_copy(update={"package_sha256": "f" * 64})
    elif binding == "process":
        lane["comparison"]["process"]["stdout"] = "{}"
    else:
        lane["source"]["sha256"] = "f" * 64
    Path(ref.report).write_text(json.dumps(report))
    ref = ref.model_copy(update={"report_sha256": file_digest(Path(ref.report))})
    with pytest.raises(SkillEvalError):
        payloads((ref, *rest))


def test_v3_incomplete_source_with_raw_process_preserves_legacy_diagnostics(observed, route):
    from skilleval import lane_readiness

    legacy = version_two(observed, route)
    ref, task, directory, raw = legacy
    report = json.loads(Path(ref.report).read_text())
    report["cases"][0]["status"] = "incomplete"
    (directory / "validation.json").write_text(json.dumps(report["cases"][0]))
    Path(ref.report).write_text(json.dumps(report))
    ref = ref.model_copy(update={"report_sha256": file_digest(Path(ref.report))})
    legacy = ref, task, directory, raw
    expected = payloads(legacy)
    selected = version_three(legacy, route, source_status="incomplete")
    ref = selected[0]
    _, report = lane_readiness.read_report(Path(ref.report), ref.report_sha256)
    assert report["cases"][0]["lanes"][0]["comparison"]["status"] == "unexpected"
    assert payloads(selected) == expected


def test_v3_observation_requires_selected_candidate(observed, route):
    selected = version_three(observed, route)
    assert payloads(selected)
    with pytest.raises(SkillEvalError, match="candidate"):
        payloads(selected, candidate_digest="a" * 64)


def changed_raw_observation(observed, route, fault):
    selected = version_two(observed, route)
    assert payloads(selected)
    ref, task, directory, raw = selected
    report = json.loads(Path(ref.report).read_text())
    source = report["cases"][0]
    process = source["lanes"][0]["process"]
    if fault == "timeout":
        process["timed_out"] = True
    elif fault == "output_limit":
        process["output_limit_reached"] = True
    elif fault == "returncode":
        process["returncode"] = None
    elif fault == "profile":
        process["profile"]["uid"] = 0
    elif fault == "boundary":
        process["boundary"]["namespace_reaped"] = False
    elif fault == "argv":
        process["argv"] = ["python3", "-c", "print('different command')"]
    else:
        process["stdout"] = "complete raw output that is not JSON"
    (directory / "validation.json").write_text(json.dumps(source))
    (directory / "lane-browser.json").write_text(json.dumps(process))
    Path(ref.report).write_text(json.dumps(report))
    ref = ref.model_copy(update={"report_sha256": file_digest(Path(ref.report))})
    return ref, task, directory, raw


@pytest.mark.parametrize("fault", ["timeout", "output_limit"])
def test_v3_incomplete_diagnostics_preserve_legacy_projection_without_unpacking(
    observed, route, fault
):
    legacy = changed_raw_observation(observed, route, fault)
    expected = payloads(legacy)
    selected = version_three(legacy, route)
    exported = payloads(selected)
    assert exported == expected
    projection = json.loads(exported["case/observations/observation-001/observation.json"])
    assert projection["execution_complete"] is False
    assert not any("/artifacts/" in name for name in exported)


@pytest.mark.parametrize("fault", ["returncode", "profile", "boundary", "argv"])
def test_v3_raw_execution_binding_refusals_match_legacy(observed, route, fault):
    legacy = changed_raw_observation(observed, route, fault)
    with pytest.raises(SkillEvalError):
        payloads(legacy)
    selected = version_three(legacy, route)
    with pytest.raises(SkillEvalError):
        payloads(selected)


def test_v3_parser_failure_exports_physically_complete_raw_evidence_like_legacy(observed, route):
    legacy = changed_raw_observation(observed, route, "parser")
    expected = payloads(legacy)
    selected = version_three(legacy, route)
    ref = selected[0]
    report = json.loads(Path(ref.report).read_text())
    assert report["cases"][0]["lanes"][0]["comparison"]["status"] == "incomplete"
    exported = payloads(selected)
    assert exported == expected
    projection = json.loads(exported["case/observations/observation-001/observation.json"])
    assert projection["execution_complete"] is True
    assert projection["process"]["stdout"] == "complete raw output that is not JSON"
