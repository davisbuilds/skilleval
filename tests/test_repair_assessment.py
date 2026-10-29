"""Repair evidence/record contracts, not semantic judge accuracy."""

import json
from pathlib import Path

import pytest
from test_judge import run_directory
from test_repair import task as task
from test_runner import route as route

from skilleval import evidence, repair
from skilleval.errors import SkillEvalError
from skilleval.identity import file_digest
from skilleval.repair_assessment import RepairAssessmentSpec, prepare_assessment
from skilleval.repair_check import check_repair


@pytest.fixture
def assessment(task, route, tmp_path):
    result = repair.collect_control(
        task["task"], task["digest"], task["base"], task["spec"].input_sha256, tmp_path / "control"
    )
    check_repair(
        task["task"],
        task["digest"],
        Path(result["path"]),
        result["sha256"],
        tmp_path / "check",
        runtime=route["runtime"],
        image=route["image"],
        image_sha256=route["image_sha256"],
    )
    ref = {
        "task": str(task["task"]),
        "task_sha256": task["digest"],
        "result": result["path"],
        "result_sha256": result["sha256"],
    }
    report = tmp_path / "check/repair-check.json"
    spec = RepairAssessmentSpec.model_validate_json(
        json.dumps(
            {
                **ref,
                "checks": [{**ref, "report": str(report), "report_sha256": file_digest(report)}],
                "authored_statement": "The repaired code preserves the ordinary contract.",
            }
        )
    )
    return spec


def test_packet_preserves_evidence_but_withholds_host_grades_and_provenance(assessment, tmp_path):
    out = tmp_path / "packet"
    prepared = prepare_assessment(assessment, out)
    assert (out / "case/source/code.py").read_bytes() == (
        Path(assessment.result) / "candidate/code.py"
    ).read_bytes()
    observed = json.loads((out / "case/checks/check-001/observations.json").read_text())
    assert observed["observations"] == [{"probe_id": "result", "observed": False}]
    assert "expected" not in observed and "accepted_by_checks" not in observed
    all_bytes = b"\n".join(p.read_bytes() for p in out.rglob("*") if p.is_file())
    assert str(tmp_path).encode() not in all_bytes
    assert b"native_receipt_sha256" not in all_bytes
    assert b"input_tokens" not in all_bytes
    assert b"authored_statement" not in all_bytes
    frozen = evidence.snapshot(out, tmp_path / "frozen")
    assert frozen["manifest_sha256"] == prepared["input_digest"]
    assert prepared["independent_blind"] is False


def test_foreign_check_cannot_supply_observations_for_selected_candidate(assessment, tmp_path):
    ref = assessment.checks[0]
    path = Path(ref.report)
    value = json.loads(path.read_text())
    value["candidate_sha256"] = "f" * 64
    path.write_text(json.dumps(value))
    changed = ref.model_copy(update={"report_sha256": file_digest(path)})
    spec = assessment.model_copy(update={"checks": (changed,)})
    with pytest.raises(SkillEvalError, match="binding"):
        prepare_assessment(spec, tmp_path / "packet")
    assert not (tmp_path / "packet").exists()


def judgment(prepared, **updates):
    value = {
        "result_id": "assessment-one",
        "evidence_sha256": prepared["evidence_sha256"],
        "packet_sha256": prepared["packet_sha256"],
        "rubric_version": prepared["rubric_version"],
        "assessor_kind": "llm",
        "assessor": "synthetic recording control",
        "model": prepared["model"],
        "investigation_status": "not-needed" if prepared["investigation"] else "not-permitted",
        "judgments": [
            {
                "criterion": c,
                "verdict": "satisfied",
                "reasoning": "Authored storage control.",
                "citations": [{"path": "case/source/code.py", "line_start": 1, "line_end": 1}],
            }
            for c in ("task-contract", "scope-and-regressions", "verification-claims")
        ],
    }
    return value | updates


def test_record_round_trip_separate_execution_and_audit_pending(assessment, tmp_path):
    from skilleval.repair_assessment import read_assessment, record_assessment

    prepared = prepare_assessment(assessment, tmp_path / "packet")
    run = run_directory(tmp_path, prepared, judgment(prepared))
    saved = record_assessment(assessment, tmp_path / "packet", run, tmp_path / "records")
    record, spec = read_assessment(Path(saved["path"]), saved["sha256"])
    assert record["assessment"]["status"] == "satisfied-in-reviewed-scope"
    assert record["assessment"]["independent_audit"] == "required-pending"
    assert record["scored_admitted"] is False
    assert spec.result_sha256 == assessment.result_sha256
    with pytest.raises(SkillEvalError, match="fresh"):
        record_assessment(assessment, tmp_path / "packet", run, tmp_path / "records")


@pytest.mark.parametrize(
    "mutation",
    ["statement-only", "wrong-case", "invented-artifact", "wrong-packet", "changed-packet"],
)
def test_unsupported_judgment_evidence_is_rejected(assessment, tmp_path, mutation):
    from skilleval.repair_assessment import record_assessment

    prepared = prepare_assessment(assessment, tmp_path / "packet")
    result = judgment(prepared)
    item = result["judgments"][0]
    if mutation == "statement-only":
        item["citations"][0]["path"] = "case/statement.txt"
    elif mutation == "wrong-case":
        item["citations"][0]["path"] = "cases/other/source.py"
    elif mutation == "invented-artifact":
        item["artifact_citations"] = [
            {"path": "task/investigation/repair/missing.txt", "line_start": 1, "line_end": 1}
        ]
    elif mutation == "wrong-packet":
        result["packet_sha256"] = "0" * 64
    run = run_directory(tmp_path, prepared, result)
    if mutation == "changed-packet":
        (tmp_path / "packet/case/instruction.md").write_text("changed")
    with pytest.raises(SkillEvalError):
        record_assessment(assessment, tmp_path / "packet", run, tmp_path / "records")
    assert not (tmp_path / "records").exists()


def test_partial_and_failed_judgments_do_not_become_success(assessment, tmp_path):
    from skilleval.repair_assessment import record_assessment

    prepared = prepare_assessment(assessment, tmp_path / "packet")
    result = judgment(
        prepared,
        judgments=[
            {
                "criterion": "task-contract",
                "verdict": "unresolved",
                "reasoning": "Missing external contract.",
            }
        ],
    )
    run = run_directory(tmp_path, prepared, result, status="failed")
    saved = record_assessment(assessment, tmp_path / "packet", run, tmp_path / "records")
    assert saved["assessment"]["status"] == "unusable-execution"
    assert saved["assessment"]["missing"] == ["scope-and-regressions", "verification-claims"]


def test_revision_must_match_same_candidate_and_retains_original(assessment, tmp_path):
    from skilleval.repair_assessment import read_assessment, record_assessment

    prepared = prepare_assessment(assessment, tmp_path / "packet")
    run = run_directory(tmp_path, prepared, judgment(prepared))
    first = record_assessment(assessment, tmp_path / "packet", run, tmp_path / "records")
    next_run = run_directory(
        tmp_path,
        prepared,
        judgment(prepared, result_id="assessment-two", supersedes="assessment-one"),
        run_name="run-two",
    )
    second = record_assessment(assessment, tmp_path / "packet", next_run, tmp_path / "records")
    assert first["sha256"] != second["sha256"]
    read_assessment(Path(first["path"]), first["sha256"])
    read_assessment(Path(second["path"]), second["sha256"])


@pytest.mark.parametrize("mutation", ["verdict", "recovery"])
def test_report_cannot_override_frozen_comparison_or_cleanup(assessment, tmp_path, mutation):
    ref = assessment.checks[0]
    path = Path(ref.report)
    value = json.loads(path.read_text())
    if mutation == "verdict":
        value["checks"][0]["pass"] = True
        value["accepted_by_checks"] = True
        path.write_text(json.dumps(value))
    else:
        ticket = json.loads((path.parent / "recovery.json").read_text())
        Path(ticket["runtime_home"]).mkdir()
    spec = assessment.model_copy(
        update={"checks": (ref.model_copy(update={"report_sha256": file_digest(path)}),)}
    )
    with pytest.raises(SkillEvalError):
        prepare_assessment(spec, tmp_path / "packet")


def test_incomplete_protocol_evidence_remains_inspectable(assessment, tmp_path):
    ref = assessment.checks[0]
    path = Path(ref.report)
    value = json.loads(path.read_text())
    value.update(
        status="incomplete",
        process=[],
        checks=[],
        accepted_by_checks=None,
        error="invalid repair supervisor protocol",
    )
    path.write_text(json.dumps(value))
    spec = assessment.model_copy(
        update={"checks": (ref.model_copy(update={"report_sha256": file_digest(path)}),)}
    )
    prepare_assessment(spec, tmp_path / "packet")
    observed = json.loads((tmp_path / "packet/case/checks/check-001/observations.json").read_text())
    assert observed["status"] == "incomplete"
    assert observed["observations"] == []
    assert observed["error"] == "invalid repair supervisor protocol"


def test_record_manifest_cannot_drop_retained_evidence(assessment, tmp_path):
    from skilleval.repair_assessment import read_assessment, record_assessment

    prepared = prepare_assessment(assessment, tmp_path / "packet")
    run = run_directory(tmp_path, prepared, judgment(prepared))
    saved = record_assessment(assessment, tmp_path / "packet", run, tmp_path / "records")
    directory = Path(saved["path"])
    path = directory / "record.json"
    record = json.loads(path.read_text())
    del record["files"]["audit.json"]
    path.write_text(json.dumps(record))
    with pytest.raises(SkillEvalError, match="manifest"):
        read_assessment(directory, file_digest(path))


def test_incomplete_check_retains_partial_stdout_and_truncation(assessment, tmp_path):
    ref = assessment.checks[0]
    path = Path(ref.report)
    value = json.loads(path.read_text())
    value.update(
        status="incomplete",
        checks=[],
        accepted_by_checks=None,
        error="incomplete repair observation protocol",
    )
    value["process"].update(
        stdout="candidate-diagnostic-marker: partial JSON", output_limit_reached=True
    )
    path.write_text(json.dumps(value))
    spec = assessment.model_copy(
        update={"checks": (ref.model_copy(update={"report_sha256": file_digest(path)}),)}
    )
    prepare_assessment(spec, tmp_path / "packet")
    observed = json.loads((tmp_path / "packet/case/checks/check-001/observations.json").read_text())
    assert observed["process"]["stdout"] == "candidate-diagnostic-marker: partial JSON"
    assert observed["process"]["output_limit_reached"] is True
