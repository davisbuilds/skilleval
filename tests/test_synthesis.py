"""Storage/contract controls only; these never qualify a VM or call a model."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from test_handoff import writer as writer
from test_handoff import writer_factory as writer_factory
from test_handoff_report import linked as linked
from test_judge import run_directory
from test_runner import route as route
from test_synthesis_inputs import selection_file

from skilleval import runner, synthesis
from skilleval.errors import SkillEvalError
from skilleval.gateway import EFFORT, MODEL
from skilleval.identity import file_digest
from skilleval.review_results import load_packet
from skilleval.synthesis import SynthesisResult, _check_result


def result_document() -> dict:
    return {
        "packet_sha256": "a" * 64,
        "summary": {
            "headline": "Both arms meet the visible request",
            "conclusion": "The supplied results support a bounded comparison.",
            "scope": "Selected known ON/OFF evidence, not blind grading",
            "limitations": ["Unknown accounting remains unknown."],
            "evidence": ["request-lines"],
        },
        "arms": [
            {"id": "off", "quality": ["Visible request addressed"], "evidence": ["request-lines"]}
        ],
        "exceptions": [],
        "citations": [
            {
                "id": "request-lines",
                "source_id": "request",
                "label": "Request",
                "start_line": 1,
                "end_line": 1,
            }
        ],
        "investigation_notes": ["Inspected the selected request and compared its constraints."],
    }


def test_result_rejects_citation_to_unselected_source() -> None:
    value = result_document()
    value["citations"][0]["source_id"] = "unselected-private-file"
    result = SynthesisResult.model_validate_json(json.dumps(value))
    with pytest.raises(SkillEvalError, match="source"):
        _check_result(result, {"request": b"Build this feature.\n"}, {"off"})


@pytest.fixture
def selected(tmp_path, linked):
    path = selection_file(tmp_path, linked)
    prepared = tmp_path / "prepared"
    receipt = synthesis.prepare_synthesis(path, prepared)
    return path, prepared, receipt


def native_run(
    tmp_path: Path,
    prepared: Path,
    receipt: dict[str, Any],
    *,
    value=None,
    status="completed",
    run_name="synthesis-native",
    **changes,
) -> Path:
    preparation = {
        **receipt,
        "path": str(prepared / "packet"),
        "prompt": (prepared / "prompt.txt").read_text(),
        "investigation": False,
    }
    value = result_document() if value is None else value
    value["packet_sha256"] = receipt["packet_sha256"]
    run = run_directory(
        tmp_path,
        preparation,
        value,
        status=status,
        run_name=run_name,
        artifacts={
            "task/report.json": json.dumps(value).encode(),
            "evidence/events.jsonl": b'{"type":"turn.completed"}\n',
        },
    )
    path = run / "native-run.json"
    native = json.loads(path.read_text())
    native.update(
        original_input={"path": str(prepared / "input"), "sha256": receipt["input_digest"]},
        usage={"input_tokens": 999999999, "total_tokens": 999999999},
        usage_accounting={"status": "incomplete", "model": MODEL, "total_tokens": None},
        timings={"agent_seconds": 2.5, "total_seconds": None},
        request_count=3,
        events="events.jsonl",
        **changes,
    )
    path.write_text(json.dumps(native))
    (run / "events.jsonl").write_bytes(b'{"type":"turn.completed"}\n')
    return run


def test_preparation_freezes_evidence_and_exact_native_policy(selected, tmp_path):
    path, prepared, receipt = selected
    observed = synthesis.inspect_synthesis(prepared, receipt["sha256"])
    assert observed["status"] == "verified"
    assert observed["model"] == MODEL
    assert observed["effort"] == EFFORT
    assert observed["native_timeout_seconds_default"] == 900
    assert observed["investigation"] is False
    prompt = (prepared / "prompt.txt").read_text()
    assert "known ON/OFF labels" in prompt
    assert "Use native source-reading tools" in prompt
    assert "not a separately enforced no-execution runtime" in prompt
    assert "host access" in prompt
    assert f"packet_sha256={receipt['packet_sha256']}" in prompt
    schema = json.loads((prepared / "input/result.schema.json").read_text())
    assert not {"author", "sources", "usage", "cost", "status", "model"} & set(schema["properties"])
    assert schema["additionalProperties"] is False
    assert json.loads((prepared / "packet/source-modes.json").read_text())["files"] == {}
    # WHY: Frozen copies support recording after the original selected sources move.
    path.unlink()
    (tmp_path / "request.txt").unlink()
    run = native_run(tmp_path, prepared, receipt)
    result = synthesis.record_synthesis(prepared, receipt["sha256"], run, tmp_path / "review")
    assert result["status"] == "completed"
    assert load_packet(tmp_path / "review")["summary"]["headline"].startswith("Both arms")


def test_record_injects_canonical_accounting_and_native_attribution(selected, tmp_path):
    _, prepared, receipt = selected
    native = native_run(tmp_path, prepared, receipt)
    review = tmp_path / "review"
    outcome = synthesis.record_synthesis(prepared, receipt["sha256"], native, review)
    packet = load_packet(review)
    assert packet["author"]["model"] == MODEL
    assert EFFORT in packet["author"]["name"]
    arm = packet["arms"][0]
    assert "Writer primary: completed" in arm["status"]
    assert "Combined agents (2 selected attempt(s)): 10 tokens total." in arm["usage"]
    assert not any("999999999" in row for row in arm["usage"])
    assert len(arm["evidence"]) == 2
    canonical = next(row for row in packet["evidence"] if row["id"].startswith("canonical-"))
    assert "accounting" in canonical["label"]
    assert outcome["synthesis_consumption"]["usage_accounting"]["status"] == "incomplete"
    assert outcome["synthesis_consumption"]["estimated_api_equivalent_usd"] is None
    assert outcome["synthesis_consumption"]["timings"]["agent_seconds"] == 2.5
    assert outcome["synthesis_consumption"]["cash_cost_usd"] is None
    assert "unknown total tokens; 2.500 agent seconds" in packet["summary"]["scope"]
    assert "separate from compared agents and graders" in packet["summary"]["scope"]
    record = json.loads((review / "record.json").read_text())
    assert record["scored_admitted"] is False
    assert record["native_receipt_sha256"] == file_digest(native / "native-run.json")
    assert (review / "audit/native/artifacts.tar").read_bytes() == (
        native / "artifacts.tar"
    ).read_bytes()
    assert (review / "audit/input/manifest.json").read_bytes() == (
        prepared / "input/manifest.json"
    ).read_bytes()
    assert (review / "audit/input/result.schema.json").exists()
    assert not (review / "responses").exists()
    # WHY: A new recording does not mutate the original generation or native evidence.
    before = (review / "packet.json").read_bytes()
    again = synthesis.record_synthesis(prepared, receipt["sha256"], native, tmp_path / "replay")
    assert again["status"] == "completed"
    assert (review / "packet.json").read_bytes() == before
    with pytest.raises(SkillEvalError, match="fresh"):
        synthesis.record_synthesis(prepared, receipt["sha256"], native, review)
    assert (review / "packet.json").read_bytes() == before


@pytest.mark.parametrize("status", ["failed", "timed-out", "partial"])
def test_valid_partial_synthesis_preserves_native_execution_failure(selected, tmp_path, status):
    _, prepared, receipt = selected
    native = native_run(tmp_path, prepared, receipt, status=status)
    result = synthesis.record_synthesis(prepared, receipt["sha256"], native, tmp_path / "review")
    assert result["status"] == "partial"
    assert result["native_status"] == status
    packet = load_packet(tmp_path / "review")
    assert f"execution: {status}" in packet["summary"]["scope"]
    assert "partial synthesis" in packet["summary"]["limitations"][0]


@pytest.mark.parametrize(
    "mutation", ["model", "effort", "mode", "prompt", "digest", "artifact", "events"]
)
def test_native_provenance_mismatch_rejected_before_output(selected, tmp_path, mutation):
    _, prepared, receipt = selected
    run = native_run(tmp_path, prepared, receipt)
    path = run / "native-run.json"
    native = json.loads(path.read_text())
    if mutation in {"model", "effort", "mode"}:
        native[mutation] = "different"
    elif mutation == "prompt":
        (run / "prompt.txt").write_text("A different policy")
    elif mutation == "digest":
        native["input_digest"] = "0" * 64
    elif mutation == "artifact":
        (run / "artifacts.tar").write_bytes(b"altered artifact")
    else:
        (run / "events.jsonl").write_bytes(b"altered native event")
    path.write_text(json.dumps(native))
    with pytest.raises(SkillEvalError):
        synthesis.record_synthesis(prepared, receipt["sha256"], run, tmp_path / "review")
    assert not (tmp_path / "review").exists()


@pytest.mark.parametrize("mutation", ["receipt", "source", "input", "addition", "prompt"])
def test_prepared_identity_checked_before_native_dispatch(
    selected, tmp_path, mutation, monkeypatch
):
    _, prepared, receipt = selected
    called = []
    monkeypatch.setattr(runner, "run_native", lambda *args, **kwargs: called.append(True))
    if mutation == "receipt":
        expected = "0" * 64
    else:
        expected = receipt["sha256"]
        target = {
            "source": "packet/case/sources/request.txt",
            "input": "input/case/sources/request.txt",
            "addition": "input/extra.txt",
            "prompt": "prompt.txt",
        }[mutation]
        changed = prepared / target
        if changed.exists():
            changed.chmod(0o600)
        changed.write_text("changed selected bytes")
    with pytest.raises(SkillEvalError):
        synthesis.run_synthesis(
            prepared,
            expected,
            tmp_path / "run",
            runtime=tmp_path / "runtime",
            image=tmp_path / "image",
            image_sha256="b" * 64,
            catalog=tmp_path / "catalog",
            auth_file=tmp_path / "auth",
        )
    assert not called
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("mutation", ["source", "range", "evidence", "arm", "injected"])
def test_bad_agent_result_cannot_change_accounting_or_read_host_paths(selected, tmp_path, mutation):
    _, prepared, receipt = selected
    value = result_document()
    if mutation == "source":
        value["citations"][0]["source_id"] = "/etc/passwd"
    elif mutation == "range":
        value["citations"][0]["end_line"] = 2
    elif mutation == "evidence":
        value["summary"]["evidence"] = ["no-citation"]
    elif mutation == "arm":
        value["arms"][0]["id"] = "unselected-arm"
    else:
        value["arms"][0]["usage"] = ["Zero tokens"]
    run = native_run(tmp_path, prepared, receipt, value=value)
    with pytest.raises((SkillEvalError, ValidationError)):
        synthesis.record_synthesis(prepared, receipt["sha256"], run, tmp_path / "review")
    assert not (tmp_path / "review").exists()


@pytest.mark.parametrize("missing_final", [False, True])
def test_run_uses_existing_native_route_and_preserves_invalid_final(
    selected, tmp_path, monkeypatch, missing_final
):
    _, prepared, receipt = selected
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        output = args[3]
        native = native_run(
            output.parent,
            prepared,
            receipt,
            value={"incomplete": True},
            run_name=output.name,
        )
        if missing_final:
            (native / "final.txt").unlink()
        return json.loads((native / "native-run.json").read_text())

    monkeypatch.setattr(runner, "run_native", run)
    kwargs = {
        "runtime": tmp_path / "runtime",
        "image": tmp_path / "image",
        "image_sha256": "b" * 64,
        "catalog": tmp_path / "catalog",
        "auth_file": tmp_path / "auth",
    }
    result = synthesis.run_synthesis(prepared, receipt["sha256"], tmp_path / "run", **kwargs)
    assert result["status"] == "incomplete"
    assert len(calls) == 1
    args, actual = calls[0]
    assert args[:3] == (
        prepared / "input",
        receipt["input_digest"],
        (prepared / "prompt.txt").read_text(),
    )
    assert actual == {
        **kwargs,
        "mode": "judge",
        "native_timeout_seconds": 900,
        "investigation": False,
    }
    assert (tmp_path / "run/native/artifacts.tar").exists()
    assert (tmp_path / "run/attempt.json").exists()
    assert not (tmp_path / "run/review").exists()
    with pytest.raises(SkillEvalError, match="fresh"):
        synthesis.run_synthesis(prepared, receipt["sha256"], tmp_path / "run", **kwargs)
    assert len(calls) == 1


def test_run_auto_records_valid_native_result(selected, tmp_path, monkeypatch):
    _, prepared, receipt = selected

    def run(*args, **kwargs):
        output = args[3]
        native = native_run(output.parent, prepared, receipt, run_name=output.name)
        return json.loads((native / "native-run.json").read_text())

    monkeypatch.setattr(runner, "run_native", run)
    result = synthesis.run_synthesis(
        prepared,
        receipt["sha256"],
        tmp_path / "run",
        runtime=tmp_path / "runtime",
        image=tmp_path / "image",
        image_sha256="b" * 64,
        catalog=tmp_path / "catalog",
        auth_file=tmp_path / "auth",
        timeout_seconds=30,
    )
    assert result["status"] == "completed"
    assert load_packet(tmp_path / "run/review")["author"]["role"] == "agent"


def test_valid_result_reserves_space_for_recording_context(selected, tmp_path):
    _, prepared, receipt = selected
    value = result_document()
    value["summary"]["scope"] = "s" * 15000
    value["summary"]["limitations"] = ["l" * 15000]
    value["citations"].extend(
        {
            "id": f"evidence-{index}",
            "source_id": "request",
            "label": "Request",
            "start_line": 1,
            "end_line": 1,
        }
        for index in range(99)
    )
    value["arms"][0]["evidence"] = [f"evidence-{index}" for index in range(99)]
    native = native_run(tmp_path, prepared, receipt, value=value)
    synthesis.record_synthesis(prepared, receipt["sha256"], native, tmp_path / "review")
    packet = load_packet(tmp_path / "review")
    assert len(packet["arms"][0]["evidence"]) == 100
    assert packet["summary"]["scope"].endswith("s" * 15000)
    assert packet["summary"]["limitations"][0].endswith("l" * 15000)
    assert "Synthesis overhead" in packet["summary"]["scope"]


def test_synthesis_cost_uses_frozen_schedule_only_with_complete_accounting():
    from skilleval.accounting import PriceSchedule

    schedule = PriceSchedule.model_validate_json(
        json.dumps(
            {
                "model": MODEL,
                "source_url": "https://example.com/prices",
                "retrieved_on": "2026-11-08",
                "input_per_million": "2",
                "cached_input_per_million": "0.1",
                "output_per_million": "10",
            }
        )
    )
    native = {
        "model": MODEL,
        "usage_accounting": {
            "status": "complete",
            "model": MODEL,
            "input_tokens": 10,
            "uncached_input_tokens": 8,
            "cached_input_tokens": 2,
            "output_tokens": 3,
            "total_tokens": 13,
        },
    }
    assert synthesis._overhead(native)["estimated_api_equivalent_usd"] is None
    observed = synthesis._overhead(native, schedule)
    assert observed["estimated_api_equivalent_usd"] == "0.0000462"
    assert observed["cash_cost_usd"] is None
    native["usage_accounting"]["status"] = "incomplete"
    assert synthesis._overhead(native, schedule)["estimated_api_equivalent_usd"] is None
