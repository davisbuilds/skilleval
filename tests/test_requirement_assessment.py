"""Frozen requirement projection and coverage, not semantic judge validity."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_acceptance import mapped as mapped
from test_assessment_observations import observed as observed
from test_judge import run_directory
from test_repair_assessment import assessment as assessment
from test_repair_assessment import judgment
from test_repair_assessment import route as route
from test_repair_assessment import task as task

from skilleval import acceptance
from skilleval import repair_assessment as assessments
from skilleval.errors import SkillEvalError


@pytest.fixture
def requirement_spec(mapped, assessment, tmp_path):
    mapped["requirements"][0]["source_rationale"] = "Original request requires the result."
    mapped["requirements"].append(
        {
            "requirement_id": "R2",
            "statement": "Preserve the ordinary failure behavior.",
            "role": "regression",
            "criticality": "required",
            "source_rationale": "The ordinary contract includes failure cases.",
            "semantic": [
                {"criterion": "scope-and-regressions", "question": "Are failures preserved?"}
            ],
            "gaps": ["The mechanical observation does not cover every failure."],
        }
    )
    mapped["controls"][0]["expected_discrimination"] = "SECRET-EXPECTED-LABEL"
    mapped["exposure_history"] = "SECRET-TREATMENT-AND-COST"
    frozen = acceptance.prepare_acceptance(
        acceptance.AcceptanceMapSpec.model_validate_json(json.dumps(mapped)),
        tmp_path / "acceptance.json",
    )
    return assessments.parse_spec(
        json.dumps(
            {
                **assessment.model_dump(mode="json"),
                "requirement_policy": "acceptance-requirements-v1",
                "acceptance_map": frozen["path"],
                "acceptance_map_sha256": frozen["sha256"],
            }
        )
    )


def test_packet_projects_frozen_requirements_without_operator_labels(requirement_spec, tmp_path):
    payloads, _, _ = assessments.packet(requirement_spec)
    assert "case/requirements.json" in payloads
    projection = json.loads(payloads["case/requirements.json"])
    assert projection["policy"] == "acceptance-requirements-v1"
    assert projection["acceptance_map_sha256"] == requirement_spec.acceptance_map_sha256
    assert projection["task_sha256"] == requirement_spec.task_sha256
    assert [r["requirement_id"] for r in projection["requirements"]] == ["R1", "R2"]
    assert projection["requirements"][0]["source_rationale"].startswith("Original request")
    assert projection["requirements"][1]["semantic"][0]["question"] == "Are failures preserved?"
    assert projection["requirements"][1]["gaps"]
    assert all("mechanical" not in r for r in projection["requirements"])
    combined = b"\n".join(payloads.values())
    assert b"SECRET-EXPECTED-LABEL" not in combined
    assert b"SECRET-TREATMENT-AND-COST" not in combined
    assert str(tmp_path).encode() not in combined
    assert json.loads(payloads["packet.json"])["rubric_version"] == "repair-requirements-agentic-v1"
    assert "requirement_judgments" in json.loads(payloads["result-schema.json"])["properties"]


def requirement_result(prepared, requirement_spec, *, verdict="satisfied"):
    value = judgment(prepared)
    value["judgments"] = [
        dict(value["judgments"][0], criterion=criterion) for criterion in assessments.CRITERIA
    ]
    value["acceptance_map_sha256"] = requirement_spec.acceptance_map_sha256
    value["requirement_judgments"] = [
        {
            "requirement_id": requirement_id,
            "verdict": verdict,
            "reasoning": "Original source and candidate support this judgment.",
            "citations": [{"path": "case/source/code.py", "line_start": 1, "line_end": 1}],
        }
        for requirement_id in ("R1", "R2")
    ]
    return value


@pytest.mark.parametrize("variant", ["complete", "missing", "unresolved", "violated", "empty"])
def test_round_trip_reports_explicit_coverage(requirement_spec, tmp_path, variant):
    prepared = assessments.prepare_assessment(requirement_spec, tmp_path / "packet")
    value = requirement_result(prepared, requirement_spec)
    if variant == "missing":
        value["requirement_judgments"].pop()
    elif variant == "empty":
        value["requirement_judgments"] = []
    elif variant in {"unresolved", "violated"}:
        value["requirement_judgments"][1]["verdict"] = variant
    run = run_directory(tmp_path, prepared, value)
    saved = assessments.record_assessment(
        requirement_spec, tmp_path / "packet", run, tmp_path / "records"
    )
    record, restored = assessments.read_assessment(Path(saved["path"]), saved["sha256"])
    assert restored == requirement_spec
    audit = record["assessment"]
    requirements = audit["requirements"]
    assert requirements["expected_count"] == 2
    assert requirements["judged_count"] == len(value["requirement_judgments"])
    assert requirements["missing"] == (
        ["R1", "R2"] if variant == "empty" else ["R2"] if variant == "missing" else []
    )
    assert requirements["unresolved"] == (["R2"] if variant == "unresolved" else [])
    expected = (
        "satisfied-in-reviewed-scope"
        if variant == "complete"
        else "violations-reported"
        if variant == "violated"
        else "unresolved"
    )
    assert requirements["status"] == audit["status"] == expected
    assert audit["scored_admitted"] is False


@pytest.mark.parametrize(
    "variant", ["unknown", "duplicate", "map-drift", "bad-span", "no-original", "map-only"]
)
def test_requirement_result_rejects_foreign_ids_and_unsubstantiated_verdicts(
    requirement_spec, tmp_path, variant
):
    prepared = assessments.prepare_assessment(requirement_spec, tmp_path / "packet")
    value = requirement_result(prepared, requirement_spec)
    if variant == "unknown":
        value["requirement_judgments"][0]["requirement_id"] = "foreign"
    elif variant == "duplicate":
        value["requirement_judgments"].append(value["requirement_judgments"][0])
    elif variant == "map-drift":
        value["acceptance_map_sha256"] = "0" * 64
    elif variant == "bad-span":
        value["requirement_judgments"][0]["citations"][0]["line_end"] = 10000
    elif variant == "no-original":
        value["requirement_judgments"][0]["citations"] = []
    else:
        value["requirement_judgments"][0]["citations"][0]["path"] = "case/requirements.json"
    run = run_directory(tmp_path, prepared, value)
    with pytest.raises((SkillEvalError, ValidationError)):
        assessments.record_assessment(
            requirement_spec, tmp_path / "packet", run, tmp_path / "records"
        )
    assert not (tmp_path / "records").exists()


@pytest.mark.parametrize(
    "missing", ["requirement_policy", "acceptance_map", "acceptance_map_sha256"]
)
def test_requirement_opt_in_requires_exact_map_identity(requirement_spec, missing):
    data = requirement_spec.model_dump(mode="json")
    data.pop(missing)
    with pytest.raises(ValidationError, match="requires policy and exact acceptance map"):
        assessments.parse_spec(json.dumps(data))


def test_requirement_map_digest_and_exact_task_binding(requirement_spec, tmp_path):
    changed = requirement_spec.model_copy(update={"acceptance_map_sha256": "0" * 64})
    with pytest.raises(SkillEvalError, match="map digest"):
        assessments.prepare_assessment(changed, tmp_path / "bad-digest")
    copied = tmp_path / "same-task-different-path.json"
    copied.write_bytes(Path(requirement_spec.task).read_bytes())
    changed = requirement_spec.model_copy(update={"task": str(copied)})
    with pytest.raises(SkillEvalError, match="another assessment task"):
        assessments.prepare_assessment(changed, tmp_path / "bad-task")


@pytest.mark.parametrize("investigation", [True, False])
def test_document_requirement_profile_and_static_policy(
    mapped, assessment, tmp_path, investigation
):
    from skilleval import task_package

    package_path = Path(mapped["package"])
    package = json.loads(package_path.read_text())["spec"]
    package["family"] = "document"
    frozen_package = task_package.prepare_package(
        task_package.TaskPackageSpec.model_validate_json(json.dumps(package)),
        tmp_path / "document-package.json",
    )
    mapped.update(package=frozen_package["path"], package_sha256=frozen_package["sha256"])
    mapped["requirements"][0]["semantic"][0]["criterion"] = "audience-usefulness"
    frozen_map = acceptance.prepare_acceptance(
        acceptance.AcceptanceMapSpec.model_validate_json(json.dumps(mapped)),
        tmp_path / "document-map.json",
    )
    spec = assessments.parse_spec(
        json.dumps(
            {
                **assessment.model_dump(mode="json"),
                "assessment_kind": "document",
                "rubric_profile": "specification-v1",
                "investigation": investigation,
                "requirement_policy": "acceptance-requirements-v1",
                "acceptance_map": frozen_map["path"],
                "acceptance_map_sha256": frozen_map["sha256"],
            }
        )
    )
    prepared = assessments.prepare_assessment(spec, tmp_path / "packet")
    mode = "agentic" if investigation else "static"
    assert prepared["rubric_version"] == f"specification-requirements-{mode}-v1"
    value = requirement_result(prepared, spec)
    value["requirement_judgments"].pop()
    value["judgments"] = [
        dict(value["judgments"][0], criterion=criterion)
        for criterion in assessments.DOCUMENT_CRITERIA
    ]
    run = run_directory(tmp_path, prepared, value)
    saved = assessments.record_assessment(spec, tmp_path / "packet", run, tmp_path / "records")
    assert saved["assessment"]["requirements"]["status"] == "satisfied-in-reviewed-scope"
    if not investigation:
        value["requirement_judgments"][0]["artifact_citations"] = [
            {"path": "task/investigation/repair/test.txt", "line_start": 1, "line_end": 1}
        ]
        with pytest.raises(ValidationError, match="static assessment"):
            assessments.DocumentRequirementAssessment.model_validate_json(json.dumps(value))


def test_requirement_family_binding_rejects_document_assessment_of_repair_map(requirement_spec):
    spec = assessments.parse_spec(
        json.dumps({**requirement_spec.model_dump(mode="json"), "assessment_kind": "document"})
    )
    with pytest.raises(SkillEvalError, match="family differs"):
        assessments.packet(spec)


def test_requirement_citation_correction_retains_verdict_and_coverage(requirement_spec, tmp_path):
    prepared = assessments.prepare_assessment(requirement_spec, tmp_path / "packet")
    value = requirement_result(prepared, requirement_spec)
    value["requirement_judgments"][0]["citations"][0]["line_end"] = 10000
    run = run_directory(tmp_path, prepared, value)
    correction = assessments.prepare_citation_correction(
        requirement_spec, tmp_path / "packet", run, tmp_path / "correction"
    )
    diagnostics = json.loads((tmp_path / "correction/diagnostics.json").read_text())
    assert diagnostics[0]["pointer"] == "/requirement_judgments/0/citations/0"
    value["requirement_judgments"][0]["citations"][0]["line_end"] = 1
    fixed = run_directory(tmp_path, correction, value, run_name="corrected-run")
    receipt_path = fixed / "native-run.json"
    receipt = json.loads(receipt_path.read_text())
    receipt.update(
        native_timeout_seconds=180,
        gateway_limits={"max_requests": 4},
        request_count=2,
        request_budget={"admitted_limit": 4, "admitted": 2},
    )
    receipt_path.write_text(json.dumps(receipt))
    saved = assessments.record_assessment(
        requirement_spec,
        tmp_path / "packet",
        run,
        tmp_path / "records",
        correction_packet=tmp_path / "correction",
        correction_run=fixed,
    )
    record, _ = assessments.read_assessment(Path(saved["path"]), saved["sha256"])
    assert record["assessment"]["requirements"]["verdicts"] == {
        "R1": "satisfied",
        "R2": "satisfied",
    }
    retained = Path(saved["path"])
    assert json.loads((retained / "correction/run/final.txt").read_text()) == value
    result = json.loads((retained / "result.json").read_text())
    assert result["requirement_judgments"][0]["citations"][0]["line_end"] == 1


def test_observation_export_reaches_packet_and_can_support_a_judgment(
    assessment, observed, tmp_path
):
    reference, _, _, _ = observed
    spec = assessment.model_copy(update={"observations": (reference,)})
    prepared = assessments.prepare_assessment(spec, tmp_path / "packet")
    original = "case/observations/observation-001/artifacts/observation.json"
    assert (tmp_path / "packet" / original).read_text().startswith('{"rendered":')
    value = judgment(prepared)
    value["judgments"][0]["citations"] = [{"path": original, "line_start": 1, "line_end": 1}]
    run = run_directory(tmp_path, prepared, value)
    saved = assessments.record_assessment(spec, tmp_path / "packet", run, tmp_path / "records")
    record, restored = assessments.read_assessment(Path(saved["path"]), saved["sha256"])
    assert restored.observations == (reference,)
    assert record["assessment"]["criteria"]["task-contract"] == "satisfied"
