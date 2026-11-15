"""Specification profile selection and recording, not semantic judge calibration."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_judge import run_directory
from test_repair_assessment import assessment as assessment
from test_repair_assessment import judgment
from test_repair_assessment import route as route
from test_repair_assessment import task as task

from skilleval import repair_assessment as assessments
from skilleval.errors import SkillEvalError


def specification(assessment, *, investigation=True):
    return assessments.parse_spec(
        json.dumps(
            {
                **assessment.model_dump(mode="json"),
                "assessment_kind": "document",
                "rubric_profile": "specification-v1",
                "investigation": investigation,
            }
        )
    )


def document_judgment(prepared, **updates):
    value = judgment(prepared)
    value["judgments"] = [
        dict(value["judgments"][0], criterion=criterion)
        for criterion in assessments.DOCUMENT_CRITERIA
    ]
    return value | updates


@pytest.mark.parametrize("investigation", [True, False])
def test_specification_profile_selects_task_appropriate_rubric(assessment, investigation):
    spec = specification(assessment, investigation=investigation)
    payloads, _, _ = assessments.packet(spec)
    mode = "agentic" if investigation else "static"
    assert json.loads(payloads["packet.json"])["rubric_version"] == f"specification-{mode}-v1"
    prompt = payloads["ASSESS.md"].decode()
    assert "completed specification" in prompt
    assert "original work request" in prompt
    assert "questions" in prompt and "blocked" in prompt
    assert "headings" in prompt and "word count" in prompt
    assert "whether trying it fits their situation" not in prompt
    for criterion in assessments.DOCUMENT_CRITERIA:
        assert criterion in prompt


@pytest.mark.parametrize("investigation", [True, False])
def test_specification_record_round_trip_and_profile_binding(assessment, tmp_path, investigation):
    spec = specification(assessment, investigation=investigation)
    prepared = assessments.prepare_assessment(spec, tmp_path / "packet")
    run = run_directory(tmp_path, prepared, document_judgment(prepared))
    saved = assessments.record_assessment(spec, tmp_path / "packet", run, tmp_path / "records")
    record, restored = assessments.read_assessment(Path(saved["path"]), saved["sha256"])
    assert restored.rubric_profile == "specification-v1"
    assert record["assessment"]["status"] == "satisfied-in-reviewed-scope"
    assert record["assessment"]["scored_admitted"] is False
    assert record["assessment"]["independent_audit"] == "required-pending"
    original = assessments.DocumentAssessmentSpec.model_validate_json(assessment.model_dump_json())
    original = original.model_copy(update={"investigation": investigation})
    with pytest.raises(SkillEvalError, match="packet differs"):
        assessments.record_assessment(original, tmp_path / "packet", run, tmp_path / "other")


def test_legacy_document_default_serialization_and_schema_are_preserved(assessment):
    legacy = assessments.DocumentAssessmentSpec.model_validate_json(assessment.model_dump_json())
    explicit = assessments.parse_spec(
        json.dumps({**legacy.model_dump(mode="json"), "rubric_profile": "document-v1"})
    )
    assert explicit.model_dump_json() == legacy.model_dump_json()
    assert "rubric_profile" not in json.loads(legacy.model_dump_json())
    payloads, digest, _ = assessments.packet(legacy)
    assert assessments.packet(explicit)[:2] == (payloads, digest)
    schema = json.loads(payloads["result-schema.json"])
    assert schema["properties"]["rubric_version"]["enum"] == [
        "document-agentic-v1",
        "document-static-v1",
    ]


def test_wrong_result_version_and_static_investigation_are_rejected(assessment, tmp_path):
    spec = specification(assessment, investigation=False)
    prepared = assessments.prepare_assessment(spec, tmp_path / "packet")
    value = document_judgment(prepared, rubric_version="document-static-v1")
    run = run_directory(tmp_path, prepared, value)
    with pytest.raises(SkillEvalError, match="another packet"):
        assessments.record_assessment(spec, tmp_path / "packet", run, tmp_path / "records")
    value = document_judgment(prepared, investigation_status="completed")
    with pytest.raises(ValidationError, match="investigation status"):
        assessments.DocumentAssessment.model_validate_json(json.dumps(value))


def test_unknown_profile_is_rejected(assessment):
    with pytest.raises(ValidationError, match="rubric_profile"):
        assessments.parse_spec(
            json.dumps(
                {
                    **assessment.model_dump(mode="json"),
                    "assessment_kind": "document",
                    "rubric_profile": "unversioned",
                }
            )
        )
