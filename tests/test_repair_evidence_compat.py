"""Versioned tool policies preserve historical packet and record identities."""

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_judge import run_directory
from test_repair_assessment import assessment as assessment
from test_repair_assessment import judgment
from test_repair_assessment import route as route
from test_repair_assessment import task as task

from skilleval import evidence
from skilleval.errors import SkillEvalError
from skilleval.repair_assessment import (
    DOCUMENT_CRITERIA,
    DocumentAssessmentSpec,
    packet,
    parse_spec,
    prepare_assessment,
    read_assessment,
    record_assessment,
)

# Captured from the existing authored fixture at 0083444 before the policy change.
# A whole-packet golden also pins the rubric, result schema and all evidence bytes.
LEGACY_PACKETS = {
    "repair": "95d1d678deb282c347f80281dbd2a074f3d96e04d2b22feb8596df80527ffc38",
    "document": "d6f81404f4a91f1c8c44283b60d419ffe4d3c480f39d5a3bfff9435a90e96bfc",
}
LEGACY_TOOLS = "462f73dbc00ad6f1ae14d25129a614e5e3e6f47e7aab4cb939ddd8d936ad069f"


@pytest.fixture(params=["repair", "document"])
def family_spec(assessment, request):
    if request.param == "document":
        return DocumentAssessmentSpec.model_validate_json(assessment.model_dump_json())
    return assessment


def family(spec):
    return "document" if isinstance(spec, DocumentAssessmentSpec) else "repair"


def select_policy(spec, policy):
    return parse_spec(json.dumps({**spec.model_dump(mode="json"), "tool_evidence_policy": policy}))


def family_judgment(spec, prepared, **updates):
    value = judgment(prepared, **updates)
    if isinstance(spec, DocumentAssessmentSpec):
        value["judgments"] = [
            dict(value["judgments"][0], criterion=criterion) for criterion in DOCUMENT_CRITERIA
        ]
    return value


def test_implicit_and_explicit_legacy_policy_preserve_packet_and_spec_bytes(family_spec):
    legacy_data = family_spec.model_dump(mode="json")
    assert "tool_evidence_policy" not in legacy_data
    explicit = select_policy(family_spec, "legacy-v1")
    assert explicit.model_dump(mode="json") == legacy_data
    assert explicit.model_dump_json() == family_spec.model_dump_json()
    payloads, digest, _ = packet(family_spec)
    explicit_payloads, explicit_digest, _ = packet(explicit)
    assert explicit_payloads == payloads
    assert explicit_digest == digest == LEGACY_PACKETS[family(family_spec)]
    assert hashlib.sha256(payloads["case/tools.json"]).hexdigest() == LEGACY_TOOLS
    assert "tool_evidence_policy" not in json.loads(payloads["packet.json"])


def test_unknown_policy_is_rejected_for_each_family(family_spec):
    with pytest.raises(ValidationError, match="tool_evidence_policy"):
        select_policy(family_spec, "future-policy")


@pytest.mark.parametrize("policy", ["legacy-v1", "application-context-v2"])
def test_policy_round_trip_binds_source_spec_and_preserves_recorded_bytes(
    family_spec, tmp_path, policy
):
    spec = select_policy(family_spec, policy)
    packet_dir = tmp_path / "packet"
    prepared = prepare_assessment(spec, packet_dir)
    if policy == "legacy-v1":
        assert prepared["packet_sha256"] == LEGACY_PACKETS[family(spec)]
        assert "tool_evidence_policy" not in prepared
    else:
        assert prepared["tool_evidence_policy"] == policy
        tools = json.loads((packet_dir / "case/tools.json").read_bytes())
        assert tools["tool_evidence_policy"] == policy
        assert prepared["packet_sha256"] != LEGACY_PACKETS[family(spec)]
    run = run_directory(tmp_path, prepared, family_judgment(spec, prepared))
    saved = record_assessment(spec, packet_dir, run, tmp_path / "records")
    directory = Path(saved["path"])
    before = {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }
    record, restored = read_assessment(directory, saved["sha256"])
    assert type(restored) is type(spec)
    assert restored.tool_evidence_policy == policy
    assert record["packet_sha256"] == prepared["packet_sha256"]
    assert record["assessment"]["status"] == "satisfied-in-reviewed-scope"
    source_data = json.loads(before["source-spec.json"])
    assert source_data.get("tool_evidence_policy", "legacy-v1") == policy
    if policy == "legacy-v1":
        assert "tool_evidence_policy" not in source_data
    assert before["source-spec.json"] == evidence._json_bytes(spec.model_dump(mode="json"))
    assert before == {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


def test_corrected_policy_cannot_record_against_legacy_packet(family_spec, tmp_path):
    packet_dir = tmp_path / "legacy-packet"
    prepared = prepare_assessment(family_spec, packet_dir)
    run = run_directory(tmp_path, prepared, family_judgment(family_spec, prepared))
    corrected = select_policy(family_spec, "application-context-v2")
    with pytest.raises(SkillEvalError, match="packet differs"):
        record_assessment(corrected, packet_dir, run, tmp_path / "records")
    assert not (tmp_path / "records").exists()
