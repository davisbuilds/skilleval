"""Public prepare/run/record command contracts, using authored native receipts."""

import copy
import json
from pathlib import Path

import pytest
from test_cli import invoke
from test_judge import run_directory
from test_repair_assessment import assessment as assessment
from test_repair_assessment import judgment
from test_repair_assessment import route as route
from test_repair_assessment import task as task

from skilleval.repair_assessment import (
    DOCUMENT_CRITERIA,
    DocumentAssessment,
    DocumentAssessmentSpec,
    RepairAssessment,
    prepare_assessment,
    read_assessment,
)


def test_correction_help_needs_no_inputs_or_runtime() -> None:
    result = invoke("repair", "prepare-citation-correction", "--help")
    assert result.returncode == 0 and not result.stderr
    assert "--timeout-seconds" in result.stdout and "--max-requests" in result.stdout
    assert "--auth-file" not in result.stdout


@pytest.mark.parametrize("flag", ["--correction-packet", "--correction-run"])
def test_record_requires_both_correction_inputs_before_reading_spec(flag: str) -> None:
    result = invoke(
        "repair",
        "record-assessment",
        "missing-spec",
        "--packet",
        "missing-packet",
        "--run",
        "missing-run",
        "--output",
        "missing-output",
        flag,
        "missing-correction",
    )
    assert result.returncode == 2 and not result.stdout
    assert "requires both --correction-packet and --correction-run" in result.stderr


@pytest.mark.parametrize("family", ["repair", "document"])
def test_cli_correction_round_trip_preserves_original_and_rejects_invalid_result(
    assessment, tmp_path: Path, family: str
) -> None:
    spec = assessment
    command = "repair"
    if family == "document":
        spec = DocumentAssessmentSpec.model_validate_json(assessment.model_dump_json())
        command = "artifact"
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(spec.model_dump_json())
    packet = tmp_path / "packet"
    preparation = prepare_assessment(spec, packet)
    proposal = judgment(preparation)
    if family == "document":
        proposal["judgments"] = [
            dict(proposal["judgments"][0], criterion=criterion) for criterion in DOCUMENT_CRITERIA
        ]
    probe = "task/investigation/repair/probe.txt"
    proposal["judgments"][0]["artifact_citations"] = [
        {"path": probe, "line_start": 35, "line_end": 49}
    ]
    original = run_directory(
        tmp_path,
        preparation,
        proposal,
        artifacts={probe: b"Observed supporting evidence\n" * 43},
    )
    original_bytes = {p.name: p.read_bytes() for p in original.iterdir() if p.is_file()}
    records = tmp_path / "records"
    record_args = (
        command,
        "record-assessment",
        str(spec_file),
        "--packet",
        str(packet),
        "--run",
        str(original),
        "--output",
        str(records),
    )
    rejected = invoke(*record_args)
    assert rejected.returncode == 2 and not records.exists()

    correction_packet = tmp_path / "correction-packet"
    prepared = invoke(
        command,
        "prepare-citation-correction",
        str(spec_file),
        "--packet",
        str(packet),
        "--run",
        str(original),
        "--output",
        str(correction_packet),
        "--timeout-seconds",
        "25",
        "--max-requests",
        "2",
    )
    assert prepared.returncode == 0, prepared.stderr
    correction = json.loads(prepared.stdout)
    assert correction["investigation"] is False
    assert correction["budget"]["timeout_seconds"] == 25
    assert correction["budget"]["max_requests"] == 2
    # Author a result as the native runtime fixture would: the final and archive
    # agree, while all original assessment fields remain intact.
    fixed = copy.deepcopy(proposal)
    fixed["judgments"][0]["artifact_citations"][0]["line_end"] = 43
    correction_run = run_directory(tmp_path, correction, fixed, run_name="correction-run")
    receipt_file = correction_run / "native-run.json"
    receipt = json.loads(receipt_file.read_text())
    receipt.update(
        native_timeout_seconds=25,
        gateway_limits={"max_requests": 2},
        request_count=1,
        request_budget={"admitted_limit": 2, "admitted": 1},
    )
    receipt_file.write_text(json.dumps(receipt))
    recorded = invoke(
        *record_args,
        "--correction-packet",
        str(correction_packet),
        "--correction-run",
        str(correction_run),
    )
    assert recorded.returncode == 0, recorded.stderr
    saved = json.loads(recorded.stdout)
    stored = Path(saved["path"])
    record, _ = read_assessment(stored, saved["sha256"])
    assert record["citation_correction"]["budget"]["linked_attempts"] == 1
    model = DocumentAssessment if family == "document" else RepairAssessment
    assert model.model_validate_json((stored / "result.json").read_bytes()) == (
        model.model_validate_json(json.dumps(fixed))
    )
    assert (stored / "final.txt").read_bytes() == original_bytes["final.txt"]
    assert (stored / "correction/run/final.txt").read_bytes() == (
        correction_run / "final.txt"
    ).read_bytes()
    assert original_bytes == {p.name: p.read_bytes() for p in original.iterdir() if p.is_file()}

    receipt["gateway_limits"]["max_requests"] = 3
    receipt_file.write_text(json.dumps(receipt))
    invalid = invoke(
        *record_args,
        "--correction-packet",
        str(correction_packet),
        "--correction-run",
        str(correction_run),
    )
    assert invalid.returncode == 2 and not invalid.stdout
    assert "budget" in invalid.stderr
