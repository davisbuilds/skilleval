"""Mechanical citation correction preserves semantic judgments and original authority."""

import copy
import json
from pathlib import Path

import pytest
from test_judge import run_directory
from test_repair_assessment import assessment as assessment
from test_repair_assessment import judgment
from test_repair_assessment import route as route
from test_repair_assessment import task as task

from skilleval import repair_assessment as assessments
from skilleval.errors import SkillEvalError


def original(assessment, tmp_path, *, document=False, alias=False, mutate=None, status="completed"):
    spec = assessment
    if document:
        spec = assessments.DocumentAssessmentSpec.model_validate_json(spec.model_dump_json())
    prepared = assessments.prepare_assessment(spec, tmp_path / "packet")
    value = judgment(prepared)
    if document:
        value["judgments"] = [dict(value["judgments"][0], criterion="factual-support")]
    path = "task/investigation/repair/probe.txt"
    value["judgments"][0]["artifact_citations"] = [{"path": path, "line_start": 35, "line_end": 49}]
    artifacts = {path: b"Observed evidence.\n" * 43}
    if alias:
        citation = value["judgments"][0]["citations"][0]
        canonical = citation["path"]
        citation["path"] = "task/" + canonical
        artifacts[citation["path"]] = (tmp_path / "packet" / canonical).read_bytes()
    if mutate is not None:
        mutate(value, artifacts)
    run = run_directory(tmp_path, prepared, value, artifacts=artifacts, status=status)
    return spec, value, run


def correction_run(tmp_path, preparation, value, **kwargs):
    fixed = copy.deepcopy(value)
    fixed["judgments"][0]["artifact_citations"][0]["line_end"] = 43
    run = run_directory(tmp_path, preparation, fixed, run_name="correction-run", **kwargs)
    receipt_file = run / "native-run.json"
    receipt = json.loads(receipt_file.read_text())
    receipt.update(
        native_timeout_seconds=180,
        gateway_limits={"max_requests": 4},
        request_count=2,
        request_budget={"admitted_limit": 4, "admitted": 2},
    )
    receipt_file.write_text(json.dumps(receipt))
    return run


def test_43_line_artifact_correction_preserves_original_assessment(assessment, tmp_path):
    spec, value, run = original(assessment, tmp_path)
    before = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
    with pytest.raises(SkillEvalError, match="line range"):
        assessments.record_assessment(spec, tmp_path / "packet", run, tmp_path / "rejected")
    prepared = assessments.prepare_citation_correction(
        spec, tmp_path / "packet", run, tmp_path / "correction-packet"
    )
    assert prepared["investigation"] is False
    assert prepared["budget"] == {
        "linked_attempts": 1,
        "timeout_seconds": 180,
        "max_requests": 4,
    }
    diagnostics = json.loads((tmp_path / "correction-packet/diagnostics.json").read_text())
    assert diagnostics == [
        {
            "pointer": "/judgments/0/artifact_citations/0",
            "path": "task/investigation/repair/probe.txt",
            "line_count": 43,
            "error": "line-range-outside-evidence",
        }
    ]
    assert (tmp_path / "correction-packet/previous-result.json").read_bytes() == before["final.txt"]
    fixed_run = correction_run(tmp_path, prepared, value)
    saved = assessments.record_assessment(
        spec,
        tmp_path / "packet",
        run,
        tmp_path / "records",
        correction_packet=tmp_path / "correction-packet",
        correction_run=fixed_run,
    )
    record_dir = Path(saved["path"])
    record, _ = assessments.read_assessment(record_dir, saved["sha256"])
    assert record["citation_correction"]["budget"] == prepared["budget"]
    for name, raw in before.items():
        assert (run / name).read_bytes() == raw
        assert (record_dir / name).read_bytes() == raw
    result = json.loads((record_dir / "result.json").read_text())
    assert result["rubric_version"] == "repair-agentic-v1"
    assert result["packet_sha256"] == value["packet_sha256"]
    assert result["result_id"] == value["result_id"]
    assert result["judgments"][0]["artifact_citations"][0]["line_end"] == 43


@pytest.mark.parametrize("document,alias", [(False, True), (True, False), (True, True)])
def test_document_and_exact_original_alias_round_trip(assessment, tmp_path, document, alias):
    spec, value, run = original(assessment, tmp_path, document=document, alias=alias)
    prepared = assessments.prepare_citation_correction(
        spec, tmp_path / "packet", run, tmp_path / "correction-packet"
    )
    fixed_run = correction_run(tmp_path, prepared, value)
    saved = assessments.record_assessment(
        spec,
        tmp_path / "packet",
        run,
        tmp_path / "records",
        correction_packet=tmp_path / "correction-packet",
        correction_run=fixed_run,
    )
    record, restored = assessments.read_assessment(Path(saved["path"]), saved["sha256"])
    assert isinstance(restored, assessments.DocumentAssessmentSpec) is document
    assert record["assessment"]["judge_execution"] == "completed"


@pytest.mark.parametrize(
    "mutation",
    [
        "valid",
        "missing",
        "non-utf8",
        "empty",
        "wrong-model",
        "wrong-packet",
        "wrong-rubric",
        "no-original",
        "schema",
        "alias-mismatch",
        "reversed",
    ],
)
def test_ineligible_originals_are_never_prepared(assessment, tmp_path, mutation):
    def mutate(value, artifacts):
        item = value["judgments"][0]
        citation = item["artifact_citations"][0]
        if mutation == "valid":
            citation["line_end"] = 43
        elif mutation == "missing":
            artifacts.clear()
            artifacts["task/investigation/repair/other.txt"] = b"Other\n"
        elif mutation in {"non-utf8", "empty"}:
            artifacts[citation["path"]] = b"\xff" if mutation == "non-utf8" else b""
        elif mutation == "wrong-model":
            value["model"] = "other"
        elif mutation == "wrong-packet":
            value["packet_sha256"] = "0" * 64
        elif mutation == "wrong-rubric":
            value["rubric_version"] = "repair-static-v1"
        elif mutation == "no-original":
            item["citations"] = []
        elif mutation == "schema":
            item["verdict"] = "invalid"
        elif mutation == "alias-mismatch":
            canonical = item["citations"][0]["path"]
            item["citations"][0]["path"] = "task/" + canonical
            artifacts["task/" + canonical] = b"Changed guest evidence\n"
        else:
            citation["line_start"] = 50

    spec, _, run = original(assessment, tmp_path, mutate=mutate)
    with pytest.raises((SkillEvalError, ValueError)):
        assessments.prepare_citation_correction(
            spec, tmp_path / "packet", run, tmp_path / "correction-packet"
        )
    assert not (tmp_path / "correction-packet").exists()


@pytest.mark.parametrize("mutation", ["failed", "timed-out", "partial", "prompt", "input"])
def test_original_execution_binding_is_required(assessment, tmp_path, mutation):
    status = mutation if mutation in {"failed", "timed-out", "partial"} else "completed"
    spec, _, run = original(assessment, tmp_path, status=status)
    receipt_file = run / "native-run.json"
    receipt = json.loads(receipt_file.read_text())
    if mutation == "prompt":
        receipt["prompt_sha256"] = "0" * 64
    elif mutation == "input":
        receipt["input_digest"] = "0" * 64
    receipt_file.write_text(json.dumps(receipt))
    with pytest.raises(SkillEvalError):
        assessments.prepare_citation_correction(
            spec, tmp_path / "packet", run, tmp_path / "correction-packet"
        )
    assert not (tmp_path / "correction-packet").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "reasoning",
        "verdict",
        "result-id",
        "path",
        "list-length",
        "list-order",
        "extra-key",
        "identity",
        "bad-span",
        "use-correction-artifact",
        "move-citation",
        "missing-bound",
    ],
)
def test_correction_cannot_change_semantics_or_evidence_authority(assessment, tmp_path, mutation):
    spec, value, run = original(assessment, tmp_path)
    preparation = assessments.prepare_citation_correction(
        spec, tmp_path / "packet", run, tmp_path / "correction-packet"
    )
    fixed = copy.deepcopy(value)
    item = fixed["judgments"][0]
    item["artifact_citations"][0]["line_end"] = 43
    if mutation in {"reasoning", "verdict"}:
        item[mutation] = "Different reasoning" if mutation == "reasoning" else "violated"
    elif mutation == "result-id":
        fixed["result_id"] = "new-result"
    elif mutation == "path":
        item["citations"][0]["path"] = "case/candidate/code.py"
    elif mutation == "list-length":
        fixed["judgments"].pop()
    elif mutation == "list-order":
        fixed["judgments"].reverse()
    elif mutation == "extra-key":
        fixed["unexpected"] = True
    elif mutation == "identity":
        fixed["packet_sha256"] = preparation["packet_sha256"]
    elif mutation in {"bad-span", "use-correction-artifact"}:
        item["artifact_citations"][0]["line_end"] = 44
    elif mutation == "move-citation":
        item["citations"].append(item["artifact_citations"].pop())
    else:
        item["artifact_citations"][0].pop("line_start")
    # Preserve the deliberately mutated value rather than the helper's correction.
    fixed_run = run_directory(
        tmp_path,
        preparation,
        fixed,
        run_name="correction-run",
        artifacts={"task/investigation/repair/probe.txt": b"New authority\n" * 100},
    )
    receipt_file = fixed_run / "native-run.json"
    receipt = json.loads(receipt_file.read_text())
    receipt.update(
        native_timeout_seconds=180,
        gateway_limits={"max_requests": 4},
        request_count=2,
        request_budget={"admitted_limit": 4, "admitted": 2},
    )
    receipt_file.write_text(json.dumps(receipt))
    with pytest.raises((SkillEvalError, ValueError)):
        assessments.record_assessment(
            spec,
            tmp_path / "packet",
            run,
            tmp_path / "records",
            correction_packet=tmp_path / "correction-packet",
            correction_run=fixed_run,
        )
    assert not (tmp_path / "records").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "timeout",
        "requests-config",
        "requests-actual",
        "missing-budget",
        "request-disagreement",
        "request-limit-disagreement",
        "boolean",
        "zero",
        "prompt",
        "input",
        "model",
        "policy",
        "failed",
        "packet",
        "original",
    ],
)
def test_correction_invocation_is_exact_and_budgeted(assessment, tmp_path, mutation):
    spec, value, run = original(assessment, tmp_path)
    preparation = assessments.prepare_citation_correction(
        spec, tmp_path / "packet", run, tmp_path / "correction-packet"
    )
    fixed_run = correction_run(tmp_path, preparation, value)
    receipt_file = fixed_run / "native-run.json"
    receipt = json.loads(receipt_file.read_text())
    if mutation == "timeout":
        receipt["native_timeout_seconds"] = 181
    elif mutation == "requests-config":
        receipt["gateway_limits"]["max_requests"] = 5
    elif mutation == "requests-actual":
        receipt["request_count"] = 5
    elif mutation == "missing-budget":
        receipt.pop("request_budget")
    elif mutation == "request-disagreement":
        receipt["request_budget"]["admitted"] = 1
    elif mutation == "request-limit-disagreement":
        receipt["request_budget"]["admitted_limit"] = 3
    elif mutation in {"boolean", "zero"}:
        receipt["request_count"] = True if mutation == "boolean" else 0
    elif mutation == "prompt":
        receipt["prompt_sha256"] = "0" * 64
    elif mutation == "input":
        receipt["input_digest"] = "0" * 64
    elif mutation == "model":
        receipt["model"] = "other"
    elif mutation == "policy":
        receipt["investigation"] = True
    elif mutation == "failed":
        receipt["status"] = "failed"
    elif mutation == "packet":
        (tmp_path / "correction-packet/diagnostics.json").write_text("[]")
    else:
        (tmp_path / "packet/case/instruction.md").write_text("Changed")
    receipt_file.write_text(json.dumps(receipt))
    with pytest.raises(SkillEvalError):
        assessments.record_assessment(
            spec,
            tmp_path / "packet",
            run,
            tmp_path / "records",
            correction_packet=tmp_path / "correction-packet",
            correction_run=fixed_run,
        )
    assert not (tmp_path / "records").exists()


@pytest.mark.parametrize(
    "timeout,requests", [(0, 4), (901, 4), (True, 4), (180, 0), (180, 129), (180, True)]
)
def test_declared_budget_is_finite_and_valid(assessment, tmp_path, timeout, requests):
    spec, _, run = original(assessment, tmp_path)
    with pytest.raises(SkillEvalError):
        assessments.prepare_citation_correction(
            spec,
            tmp_path / "packet",
            run,
            tmp_path / "correction-packet",
            timeout_seconds=timeout,
            max_requests=requests,
        )
    assert not (tmp_path / "correction-packet").exists()


@pytest.mark.parametrize("field", ["correction_packet", "correction_run"])
def test_record_requires_paired_correction_inputs(assessment, tmp_path, field):
    spec, _, run = original(assessment, tmp_path)
    with pytest.raises(SkillEvalError, match="both"):
        assessments.record_assessment(
            spec,
            tmp_path / "packet",
            run,
            tmp_path / "records",
            **{field: tmp_path / "other"},
        )


def test_all_invalid_spans_are_reported_without_suggested_replacements(assessment, tmp_path):
    def mutate(value, artifacts):
        value["judgments"][0]["citations"][0]["line_end"] = 999

    spec, _, run = original(assessment, tmp_path, mutate=mutate)
    preparation = assessments.prepare_citation_correction(
        spec, tmp_path / "packet", run, tmp_path / "correction-packet"
    )
    diagnostics = json.loads((Path(preparation["path"]) / "diagnostics.json").read_text())
    assert [item["pointer"] for item in diagnostics] == [
        "/judgments/0/citations/0",
        "/judgments/0/artifact_citations/0",
    ]
    assert all(set(item) == {"pointer", "path", "line_count", "error"} for item in diagnostics)
    files = json.loads((Path(preparation["path"]) / "cited-files.json").read_text())
    assert {row["path"] for row in files} == {
        "case/source/code.py",
        "task/investigation/repair/probe.txt",
    }
    assert not (Path(preparation["path"]) / "case/candidate/code.py").exists()


@pytest.mark.parametrize("mutation", ["lineage", "manifest-drop", "corrected-result", "packet"])
def test_readback_rejects_rehashed_lineage_or_retained_file_drift(assessment, tmp_path, mutation):
    from skilleval import evidence
    from skilleval.identity import file_digest

    spec, value, run = original(assessment, tmp_path)
    preparation = assessments.prepare_citation_correction(
        spec, tmp_path / "packet", run, tmp_path / "correction-packet"
    )
    fixed_run = correction_run(tmp_path, preparation, value)
    saved = assessments.record_assessment(
        spec,
        tmp_path / "packet",
        run,
        tmp_path / "records",
        correction_packet=tmp_path / "correction-packet",
        correction_run=fixed_run,
    )
    directory = Path(saved["path"])
    record_file = directory / "record.json"
    record = json.loads(record_file.read_text())
    if mutation == "lineage":
        record["citation_correction"]["original_final_sha256"] = "0" * 64
    elif mutation == "manifest-drop":
        record["files"].pop("correction/run/prompt.txt")
    else:
        name = (
            "result.json"
            if mutation == "corrected-result"
            else "correction/packet/diagnostics.json"
        )
        target = directory / name
        target.write_text("{}")
        record["files"][name] = {"sha256": file_digest(target), "bytes": len(target.read_bytes())}
    record_file.write_bytes(evidence._json_bytes(record))
    with pytest.raises((SkillEvalError, ValueError)):
        assessments.read_assessment(directory, file_digest(record_file))
