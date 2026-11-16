"""Provider selection is frozen by the packet, never by model-authored output."""

import json
from pathlib import Path

import pytest
from test_handoff import writer as writer
from test_handoff import writer_factory as writer_factory
from test_handoff_report import linked as linked
from test_judge import CORPUS, document, review_document, review_prepared, run_directory
from test_repair_assessment import assessment as assessment
from test_repair_assessment import judgment
from test_repair_assessment import task as task
from test_runner import route as route
from test_synthesis import native_run
from test_synthesis_inputs import selection_file

from skilleval import evidence, judge, repair_assessment, runner, synthesis
from skilleval.errors import SkillEvalError
from skilleval.review_results import load_packet

CLAUDE_MODEL = "claude-opus-5-5"


def claude_receipt(run: Path, **changes) -> None:
    path = run / "native-run.json"
    receipt = json.loads(path.read_text())
    receipt.update(model=CLAUDE_MODEL, effort="high", harness="claude-code")
    if isinstance(receipt.get("usage_accounting"), dict):
        receipt["usage_accounting"]["model"] = CLAUDE_MODEL
    receipt.update(changes)
    path.write_text(json.dumps(receipt))


@pytest.mark.parametrize("family", ["calibration", "review"])
def test_selected_claude_packet_records_with_actual_native_identity(tmp_path, family):
    packet = tmp_path / "claude-packet"
    if family == "calibration":
        bundle = tmp_path / "bundle"
        evidence.snapshot(CORPUS, bundle)
        prep = judge.prepare_agentic_calibration(
            bundle, packet, investigation=True, judge_profile="claude-opus"
        )
        value = document(bundle, prep)
        recorder_args = (bundle, packet)
        record = judge.record_agentic_calibration
    else:
        bundle, report, _, _ = review_prepared(tmp_path)
        prep = judge.prepare_agentic_review(bundle, report, packet, judge_profile="claude-opus")
        value = review_document(prep)
        recorder_args = (bundle, report, packet)
        record = judge.record_agentic_review
    assert prep["model"] == CLAUDE_MODEL
    assert json.loads((packet / "packet.json").read_text())["judge_profile"] == "claude-opus"
    assert f"model={CLAUDE_MODEL}\neffort=high" in prep["prompt"]
    value["model"] = CLAUDE_MODEL
    run = run_directory(tmp_path, prep, value)
    claude_receipt(run)
    outcome = record(*recorder_args, run, tmp_path / "record")
    assert outcome["status"] == "recorded"


@pytest.mark.parametrize(
    "changes",
    [{"model": "gpt-6.1-sol"}, {"effort": "low"}, {"harness": "codex"}, {"harness": None}],
)
def test_claude_recording_rejects_identity_drift_bound_to_selected_packet(tmp_path, changes):
    bundle, packet = tmp_path / "bundle", tmp_path / "packet"
    evidence.snapshot(CORPUS, bundle)
    prep = judge.prepare_agentic_calibration(
        bundle, packet, investigation=True, judge_profile="claude-opus"
    )
    value = document(bundle, prep)
    value["model"] = changes.get("model", CLAUDE_MODEL)
    run = run_directory(tmp_path, prep, value)
    claude_receipt(run, **changes)
    with pytest.raises(SkillEvalError, match=r"identity|harness"):
        judge.record_agentic_calibration(bundle, packet, run, tmp_path / "record")
    assert not (tmp_path / "record").exists()


def test_claude_receipt_cannot_change_legacy_packet_selection(tmp_path):
    bundle, packet = tmp_path / "bundle", tmp_path / "packet"
    evidence.snapshot(CORPUS, bundle)
    prep = judge.prepare_agentic_calibration(bundle, packet, investigation=True)
    value = document(bundle, prep)
    value["model"] = CLAUDE_MODEL
    run = run_directory(tmp_path, prep, value)
    claude_receipt(run)
    with pytest.raises(SkillEvalError, match="identity"):
        judge.record_agentic_calibration(bundle, packet, run, tmp_path / "record")


def test_default_profile_preserves_prepared_packet_and_prompt_bytes(tmp_path):
    bundle = tmp_path / "bundle"
    evidence.snapshot(CORPUS, bundle)
    implicit = judge.prepare_agentic_calibration(bundle, tmp_path / "implicit", investigation=True)
    explicit = judge.prepare_agentic_calibration(
        bundle, tmp_path / "explicit", investigation=True, judge_profile="codex-sol"
    )
    assert implicit["packet_sha256"] == explicit["packet_sha256"]
    assert implicit["input_digest"] == explicit["input_digest"]
    assert implicit["prompt"] == explicit["prompt"]
    assert "judge_profile" not in json.loads((tmp_path / "explicit/packet.json").read_text())


@pytest.mark.parametrize("family", ["repair", "document", "specification"])
def test_claude_repair_family_records_and_replays(assessment, tmp_path, family):
    spec = assessment
    if family != "repair":
        spec = repair_assessment.DocumentAssessmentSpec.model_validate_json(spec.model_dump_json())
        if family == "specification":
            spec = spec.model_copy(update={"rubric_profile": "specification-v1"})
    packet = tmp_path / "packet"
    prep = repair_assessment.prepare_assessment(spec, packet, judge_profile="claude-opus")
    assert prep["model"] == CLAUDE_MODEL
    value = judgment(prep)
    if family != "repair":
        criterion = "factual-support"
        value["judgments"] = [dict(value["judgments"][0], criterion=criterion)]
    run = run_directory(tmp_path, prep, value)
    claude_receipt(run)
    outcome = repair_assessment.record_assessment(spec, packet, run, tmp_path / "records")
    record, restored = repair_assessment.read_assessment(Path(outcome["path"]), outcome["sha256"])
    assert restored == repair_assessment._normalize(spec)
    assert record["packet_sha256"] == prep["packet_sha256"]


def test_synthesis_freezes_claude_and_uses_it_for_dispatch_and_attribution(
    linked, tmp_path, monkeypatch
):
    selection = selection_file(tmp_path, linked)
    selected = json.loads(selection.read_text())
    selected["price_schedule"]["model"] = CLAUDE_MODEL
    selection.write_text(json.dumps(selected))
    prepared = tmp_path / "prepared"
    receipt = synthesis.prepare_synthesis(selection, prepared, judge_profile="claude-opus")
    assert receipt["model"] == CLAUDE_MODEL
    assert synthesis.inspect_synthesis(prepared, receipt["sha256"])["model"] == CLAUDE_MODEL
    calls = []

    def run(*args, **kwargs):
        calls.append(kwargs)
        output = args[3]
        native = native_run(output.parent, prepared, receipt, run_name=output.name)
        claude_receipt(native)
        return json.loads((native / "native-run.json").read_text())

    monkeypatch.setattr(runner, "run_native", run)
    outcome = synthesis.run_synthesis(
        prepared,
        receipt["sha256"],
        tmp_path / "run",
        runtime=tmp_path / "runtime",
        image=tmp_path / "image",
        image_sha256="b" * 64,
        catalog=tmp_path / "catalog",
        auth_file=tmp_path / "auth",
        claude_binary=tmp_path / "claude",
    )
    assert outcome["status"] == "completed"
    assert calls[0]["judge_profile"] == "claude-opus"
    assert calls[0]["claude_binary"] == tmp_path / "claude"
    author = load_packet(tmp_path / "run/review")["author"]
    assert author["model"] == CLAUDE_MODEL
    assert author["name"] == "Claude Code native synthesis (high effort)"

    def fail(*args, **kwargs):
        raise SkillEvalError("native launch unavailable")

    monkeypatch.setattr(runner, "run_native", fail)
    failed = synthesis.run_synthesis(
        prepared,
        receipt["sha256"],
        tmp_path / "failed-run",
        runtime=tmp_path / "runtime",
        image=tmp_path / "image",
        image_sha256="b" * 64,
    )
    assert failed["status"] == "incomplete"
    assert failed["synthesis_consumption"]["usage_accounting"] is None
    assert failed["synthesis_consumption"]["estimated_api_equivalent_usd"] is None
    assert (tmp_path / "failed-run/attempt.json").exists()


def test_unknown_profile_is_rejected_before_creating_packet(tmp_path):
    bundle = tmp_path / "bundle"
    evidence.snapshot(CORPUS, bundle)
    with pytest.raises(SkillEvalError, match="judge profile"):
        judge.prepare_agentic_calibration(
            bundle, tmp_path / "packet", investigation=True, judge_profile="invented"
        )
    assert not (tmp_path / "packet").exists()


def test_claude_citation_correction_inherits_original_profile_and_replays(assessment, tmp_path):
    from test_citation_correction import correction_run

    packet = tmp_path / "packet"
    prep = repair_assessment.prepare_assessment(assessment, packet, judge_profile="claude-opus")
    value = judgment(prep)
    artifact = "task/investigation/repair/probe.txt"
    value["judgments"][0]["artifact_citations"] = [
        {"path": artifact, "line_start": 35, "line_end": 49}
    ]
    run = run_directory(tmp_path, prep, value, artifacts={artifact: b"Observed evidence.\n" * 43})
    claude_receipt(run)
    with pytest.raises(SkillEvalError, match="provider request limits"):
        repair_assessment.prepare_citation_correction(
            assessment, packet, run, tmp_path / "unsupported-limit", max_requests=4
        )
    assert not (tmp_path / "unsupported-limit").exists()
    correction_packet = tmp_path / "correction-packet"
    correction = repair_assessment.prepare_citation_correction(
        assessment, packet, run, correction_packet
    )
    assert correction["judge_profile"] == "claude-opus"
    assert correction["model"] == CLAUDE_MODEL
    assert correction["budget"] == {"linked_attempts": 1, "timeout_seconds": 180}
    assert correction["provider_requests"] == "unknown"
    corrected = correction_run(tmp_path, correction, value)
    claude_receipt(corrected)
    receipt_path = corrected / "native-run.json"
    native = json.loads(receipt_path.read_text())
    for key in ("gateway_limits", "request_count", "request_budget"):
        native.pop(key)
    receipt_path.write_text(json.dumps(native))
    for index, timeout in enumerate((None, 181)):
        receipt_path.write_text(json.dumps({**native, "native_timeout_seconds": timeout}))
        with pytest.raises(SkillEvalError, match="declared budget"):
            repair_assessment.record_assessment(
                assessment,
                packet,
                run,
                tmp_path / f"invalid-budget-{index}",
                correction_packet=correction_packet,
                correction_run=corrected,
            )
        assert not (tmp_path / f"invalid-budget-{index}").exists()
    receipt_path.write_text(json.dumps(native))
    recorded = repair_assessment.record_assessment(
        assessment,
        packet,
        run,
        tmp_path / "records",
        correction_packet=correction_packet,
        correction_run=corrected,
    )
    record, _ = repair_assessment.read_assessment(Path(recorded["path"]), recorded["sha256"])
    assert record["packet_sha256"] == prep["packet_sha256"]
    assert record["citation_correction"]["judge_profile"] == "claude-opus"


def test_synthesis_rejects_price_schedule_for_another_selected_model(linked, tmp_path):
    selection = selection_file(tmp_path, linked)
    with pytest.raises(SkillEvalError, match="price schedule"):
        synthesis.prepare_synthesis(selection, tmp_path / "prepared", judge_profile="claude-opus")
    assert not (tmp_path / "prepared").exists()
