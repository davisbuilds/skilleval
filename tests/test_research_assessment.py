"""Research profile/storage controls, not semantic judge reliability."""

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_acceptance import mapped as mapped
from test_judge import run_directory
from test_repair_assessment import assessment as assessment
from test_repair_assessment import judgment
from test_repair_assessment import route as route
from test_repair_assessment import task as task

from skilleval import acceptance, task_package
from skilleval import repair_assessment as assessments
from skilleval.errors import SkillEvalError


def test_research_profile_selects_five_distinct_judgments(assessment):
    spec = research_spec(assessment)
    payloads, _, _ = assessments.packet(spec)
    assert json.loads(payloads["packet.json"])["rubric_version"] == "research-agentic-v1"


@pytest.mark.parametrize("profile", ["document-v1", "specification-v1"])
@pytest.mark.parametrize("investigation", [True, False])
def test_old_profile_packet_characterization(assessment, profile, investigation):
    spec = assessments.DocumentAssessmentSpec.model_validate_json(assessment.model_dump_json())
    spec = spec.model_copy(update={"rubric_profile": profile, "investigation": investigation})
    payloads, digest, _ = assessments.packet(spec)
    expected = {
        ("document-v1", True): "d6f81404f4a91f1c8c44283b60d419ffe4d3c480f39d5a3bfff9435a90e96bfc",
        ("document-v1", False): "2821565cefd502fbc43e124f79c4904d46e994e33597673a3538dc0807bfb45c",
        (
            "specification-v1",
            True,
        ): "1d6190674d6214644965f6e969f9b731b9380c650c2cf0f325a857dc90ad34cc",
        (
            "specification-v1",
            False,
        ): "6521cb00aea8354eced8a0f54219fd93f9113b620cbeeb8beae8d08ae4981cb5",
    }
    schema_hashes = {
        "document-v1": "5cafae438cbb9a08264e0fa3d5951fba5de51b52db8c21ef43d6e1f51e845c44",
        "specification-v1": "95f8bdc7dfce5353386232eda798ef325941d7fc4987363cd49e67e2feeb93e1",
    }
    assert digest == expected[profile, investigation]
    assert hashlib.sha256(payloads["result-schema.json"]).hexdigest() == schema_hashes[profile]


def research_spec(assessment, *, investigation=True):
    return assessments.parse_spec(
        json.dumps(
            {
                **assessment.model_dump(mode="json"),
                "assessment_kind": "document",
                "rubric_profile": "research-v1",
                "investigation": investigation,
            }
        )
    )


def research_result(prepared, **updates):
    value = judgment(prepared)
    value["judgments"] = [
        dict(value["judgments"][0], criterion=criterion)
        for criterion in assessments.RESEARCH_CRITERIA
    ]
    return value | updates


@pytest.mark.parametrize("investigation", [True, False])
@pytest.mark.parametrize("judge_profile", ["codex-sol", "claude-opus"])
def test_research_prepare_record_read_is_provider_neutral(
    assessment, tmp_path, investigation, judge_profile
):
    spec = research_spec(assessment, investigation=investigation)
    prepared = assessments.prepare_assessment(
        spec, tmp_path / "packet", judge_profile=judge_profile
    )
    value = research_result(prepared)
    run = run_directory(tmp_path, prepared, value)
    if judge_profile == "claude-opus":
        receipt = run / "native-run.json"
        data = json.loads(receipt.read_text())
        data.update(model=prepared["model"], harness="claude-code", effort="high")
        receipt.write_text(json.dumps(data))
    saved = assessments.record_assessment(spec, tmp_path / "packet", run, tmp_path / "records")
    record, restored = assessments.read_assessment(Path(saved["path"]), saved["sha256"])
    assert restored.rubric_profile == "research-v1"
    assert restored == spec
    assert record["assessment"]["status"] == "satisfied-in-reviewed-scope"
    assert set(record["assessment"]["criteria"]) == set(assessments.RESEARCH_CRITERIA)
    assert record["assessment"]["independent_audit"] == "required-pending"
    assert record["assessment"]["scored_admitted"] is False
    payloads, _, _ = assessments.packet(spec, judge_profile=judge_profile)
    other, _, _ = assessments.packet(
        spec, judge_profile="claude-opus" if judge_profile == "codex-sol" else "codex-sol"
    )
    assert other["ASSESS.md"] == payloads["ASSESS.md"]
    assert other["result-schema.json"] == payloads["result-schema.json"]


def test_research_rubric_demands_passage_comparison_without_style_proxies(assessment):
    payloads, _, _ = assessments.packet(research_spec(assessment))
    text = payloads["ASSESS.md"].decode()
    assert "original request" in text and "substantive source content you actually inspect" in text
    assert "consequential uncited claims" in text and "real-world truth" in text
    assert "novel valid" in text and "substantively different valid" in text
    assert "appropriately inconclusive" in text and "access-limited" in text
    assert "citation count" in text and "template compliance" in text
    assert "does not itself\nauthorize network access" in text
    schema = json.loads(payloads["result-schema.json"])
    assert schema["$defs"]["ResearchJudgment"]["properties"]["criterion"]["enum"] == list(
        assessments.RESEARCH_CRITERIA
    )
    assert schema["properties"]["judgments"]["maxItems"] == 5


@pytest.mark.parametrize("variant", ["missing", "unresolved", "violated"])
def test_research_summary_preserves_distinct_missing_and_unresolved(assessment, tmp_path, variant):
    spec = research_spec(assessment)
    prepared = assessments.prepare_assessment(spec, tmp_path / "packet")
    value = research_result(prepared)
    if variant == "missing":
        value["judgments"].pop()
    else:
        value["judgments"][-1]["verdict"] = variant
    run = run_directory(tmp_path, prepared, value)
    saved = assessments.record_assessment(spec, tmp_path / "packet", run, tmp_path / "records")
    audit = saved["assessment"]
    assert audit["missing"] == (["usefulness"] if variant == "missing" else [])
    assert audit["status"] == ("violations-reported" if variant == "violated" else "unresolved")


@pytest.mark.parametrize(
    "variant", ["duplicate", "old-criterion", "static-artifact", "wrong-profile"]
)
def test_research_rejects_incompatible_judgments(assessment, tmp_path, variant):
    spec = research_spec(assessment, investigation=False)
    prepared = assessments.prepare_assessment(spec, tmp_path / "packet")
    value = research_result(prepared)
    if variant == "duplicate":
        value["judgments"][1]["criterion"] = "factual-support"
    elif variant == "old-criterion":
        value["judgments"][0]["criterion"] = "relevant-limits"
    elif variant == "static-artifact":
        value["judgments"][0]["citations"].append(
            {"path": "task/investigation/repair/probe.txt", "line_start": 1, "line_end": 1}
        )
    else:
        value["rubric_version"] = "document-static-v1"
    run = run_directory(tmp_path, prepared, value)
    with pytest.raises((ValidationError, SkillEvalError)):
        assessments.record_assessment(spec, tmp_path / "packet", run, tmp_path / "records")
    assert not (tmp_path / "records").exists()


@pytest.mark.parametrize("variant", ["complete", "missing", "unresolved", "unknown"])
def test_research_requirement_profile_round_trip(assessment, mapped, tmp_path, variant):
    package_path = Path(mapped["package"])
    package = json.loads(package_path.read_text())["spec"]
    package["family"] = "document"
    frozen_package = task_package.prepare_package(
        task_package.TaskPackageSpec.model_validate_json(json.dumps(package)),
        tmp_path / "document-package.json",
    )
    mapped.update(package=frozen_package["path"], package_sha256=frozen_package["sha256"])
    mapped["requirements"][0]["semantic"][0]["criterion"] = "factual-support"
    frozen_map = acceptance.prepare_acceptance(
        acceptance.AcceptanceMapSpec.model_validate_json(json.dumps(mapped)),
        tmp_path / "map.json",
    )
    spec = research_spec(assessment).model_copy(
        update={
            "requirement_policy": "acceptance-requirements-v1",
            "acceptance_map": frozen_map["path"],
            "acceptance_map_sha256": frozen_map["sha256"],
        }
    )
    prepared = assessments.prepare_assessment(spec, tmp_path / "packet")
    assert prepared["rubric_version"] == "research-requirements-agentic-v1"
    value = research_result(prepared)
    value["acceptance_map_sha256"] = spec.acceptance_map_sha256
    value["requirement_judgments"] = [
        {
            "requirement_id": "R1" if variant != "unknown" else "foreign",
            "verdict": "unresolved" if variant == "unresolved" else "satisfied",
            "reasoning": "Authored recording control; not a semantic assessment.",
            "citations": [{"path": "case/source/code.py", "line_start": 1, "line_end": 1}],
        }
    ]
    if variant == "missing":
        value["requirement_judgments"] = []
    run = run_directory(tmp_path, prepared, value)
    if variant == "unknown":
        with pytest.raises(SkillEvalError, match="unknown projected requirement"):
            assessments.record_assessment(spec, tmp_path / "packet", run, tmp_path / "records")
        return
    saved = assessments.record_assessment(spec, tmp_path / "packet", run, tmp_path / "records")
    record, restored = assessments.read_assessment(Path(saved["path"]), saved["sha256"])
    assert restored == spec
    audit = record["assessment"]
    assert audit["status"] == (
        "satisfied-in-reviewed-scope" if variant == "complete" else "unresolved"
    )
    assert audit["requirements"]["missing"] == (["R1"] if variant == "missing" else [])
    assert audit["requirements"]["unresolved"] == (["R1"] if variant == "unresolved" else [])


def test_web_selection_is_opt_in_and_cannot_change_legacy_document_profile(assessment):
    legacy = assessments.DocumentAssessmentSpec.model_validate_json(assessment.model_dump_json())
    assert "web_search" not in legacy.model_dump(mode="json")
    for profile, investigation in (
        ("document-v1", True),
        ("specification-v1", True),
        ("research-v1", False),
    ):
        with pytest.raises(ValidationError, match="investigative research-v1"):
            assessments.parse_spec(
                json.dumps(
                    {
                        **legacy.model_dump(mode="json"),
                        "rubric_profile": profile,
                        "investigation": investigation,
                        "web_search": True,
                    }
                )
            )
    with pytest.raises(ValidationError):
        assessments.DocumentAssessment.model_validate_json(
            json.dumps(
                {
                    "result_id": "bad",
                    "evidence_sha256": "a" * 64,
                    "packet_sha256": "b" * 64,
                    "rubric_version": "document-static-v1",
                    "assessor_kind": "llm",
                    "assessor": "storage control",
                    "model": "model",
                    "investigation_status": "not-permitted",
                    "judgments": [
                        {
                            "criterion": "coverage",
                            "verdict": "unresolved",
                            "reasoning": "unknown",
                        }
                    ],
                }
            )
        )


def test_native_web_permission_changes_only_opted_in_research_packet(assessment):
    disabled = research_spec(assessment)
    enabled = assessments.parse_spec(
        json.dumps({**disabled.model_dump(mode="json"), "web_search": True})
    )
    old_payloads, old_digest, _ = assessments.packet(disabled)
    payloads, digest, _ = assessments.packet(enabled)
    assert json.loads(payloads["packet.json"])["web_search"] is True
    assert "web_search" not in json.loads(old_payloads["packet.json"])
    assert digest != old_digest
    assert payloads["result-schema.json"] == old_payloads["result-schema.json"]
    assert all(
        payloads[path] == raw for path, raw in old_payloads.items() if path.startswith("case/")
    )
    text = payloads["ASSESS.md"].decode()
    assert "Native web search is explicitly enabled" in text
    assert "research-notes.md" in text and "model-authored" in text
    assert "full hosted page text remains opaque" in text
    assert "do not install dependencies, access credentials, contact external services" not in text
    assert "contact external services" in old_payloads["ASSESS.md"].decode()


def test_research_web_projection_retains_actions_without_inventing_page_capture():
    events = [
        {
            "type": "item.started",
            "item": {
                "type": "web_search",
                "id": "s1",
                "action": {"type": "search", "query": "source method"},
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "web_search",
                "id": "s1",
                "status": "completed",
                "action": {"type": "open_page", "url": "https://example.org/p"},
                "citations": [{"url": "https://example.org/p"}],
                "page_body": "not projected",
            },
        },
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
    ]
    raw = b"\n".join(json.dumps(event).encode() for event in events)
    observed = assessments._web_search_evidence(raw)
    assert observed["observed_event_count"] == 2
    assert observed["withheld_event_count"] == 0
    assert observed["items"][0]["item"]["action"]["query"] == "source method"
    assert observed["items"][1]["item"]["action"]["url"] == "https://example.org/p"
    assert observed["items"][1]["item"]["status"] == "completed"
    assert "page_body" not in observed["items"][1]["item"]
    assert "not a complete transcript" in observed["limits"][0]
    assert "web_search_observations" not in assessments._tool_evidence(raw)


@pytest.mark.parametrize("web_search,receipt_flag", [(True, True), (True, False), (False, True)])
def test_research_native_web_policy_must_match_retained_receipt(
    assessment, tmp_path, web_search, receipt_flag
):
    spec = assessments.parse_spec(
        json.dumps(
            {
                **research_spec(assessment).model_dump(mode="json"),
                "web_search": web_search,
            }
        )
    )
    prepared = assessments.prepare_assessment(spec, tmp_path / "packet")
    run = run_directory(tmp_path, prepared, research_result(prepared))
    path = run / "native-run.json"
    receipt = json.loads(path.read_text())
    receipt["web_search"] = receipt_flag
    path.write_text(json.dumps(receipt))
    if web_search != receipt_flag:
        with pytest.raises(SkillEvalError, match=r"web.search"):
            assessments.record_assessment(spec, tmp_path / "packet", run, tmp_path / "records")
    else:
        saved = assessments.record_assessment(spec, tmp_path / "packet", run, tmp_path / "records")
        record, restored = assessments.read_assessment(Path(saved["path"]), saved["sha256"])
        assert restored.web_search is True
        assert record["assessment"]["independent_audit"] == "required-pending"


def test_live_web_research_rejects_unsupported_judge_provider(assessment, tmp_path):
    spec = assessments.parse_spec(
        json.dumps(
            {
                **research_spec(assessment).model_dump(mode="json"),
                "web_search": True,
            }
        )
    )
    with pytest.raises(SkillEvalError, match="only by the codex-sol"):
        assessments.prepare_assessment(spec, tmp_path / "packet", judge_profile="claude-opus")
    assert not (tmp_path / "packet").exists()


def test_native_research_tools_project_retained_web_events_only_for_research(assessment, tmp_path):
    from skilleval.identity import file_digest

    prepared = assessments.prepare_assessment(assessment, tmp_path / "packet")
    raw = (
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "s1",
                    "type": "web_search",
                    "status": "completed",
                    "action": {"type": "search", "query": "source method"},
                },
            }
        ).encode()
        + b"\n"
    )
    run = run_directory(
        tmp_path, prepared, {}, mode="repair", artifacts={"evidence/events.jsonl": raw}
    )
    (run / "events.jsonl").write_bytes(raw)
    receipt_path = run / "native-run.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["events"] = "events.jsonl"
    receipt_path.write_text(json.dumps(receipt))
    result = {
        "provenance": {
            "kind": "native-repair",
            "native_run": str(run),
            "native_receipt_sha256": file_digest(receipt_path),
        }
    }
    spec = research_spec(assessment).model_copy(update={"authored_statement": None})
    _, projected, execution = assessments._native_evidence(result, spec)
    assert execution["execution_status"] == "completed"
    tools = json.loads(projected)
    assert (
        tools["web_search_observations"]["items"][0]["item"]["action"]["query"] == "source method"
    )
    legacy = assessments.DocumentAssessmentSpec.model_validate_json(assessment.model_dump_json())
    _, old_projected, _ = assessments._native_evidence(
        result, legacy.model_copy(update={"authored_statement": None})
    )
    assert "web_search_observations" not in json.loads(old_projected)
