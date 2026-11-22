import hashlib
import json
from pathlib import Path

import pytest

from skilleval.errors import SkillEvalError
from skilleval.review_results import build_review, load_packet


def source_spec(tmp_path: Path) -> Path:
    source = tmp_path / "result.txt"
    source.write_text("original judgment: unresolved\ncanonical cost: unknown\n")
    spec = {
        "schema_version": 1,
        "title": "A bounded comparison",
        "author": {"name": "Synthesis agent", "role": "agent", "model": "test-model"},
        "summary": {
            "headline": "No demonstrated advantage",
            "conclusion": "Both artifacts are useful; accounting remains incomplete.",
            "scope": "One exposed development pair; no general effectiveness claim.",
            "limitations": ["Independent reliability unestablished."],
            "evidence": ["judgment"],
        },
        "arms": [
            {
                "id": "on",
                "label": "ON",
                "status": "failed-native-attempt",
                "quality": ["Retained artifact has useful observations."],
                "usage": ["Canonical usage unknown"],
                "cost": ["Unknown"],
                "timing": ["No time estimate"],
                "evidence": ["judgment"],
            }
        ],
        "exceptions": [
            {
                "id": "usage",
                "title": "Incomplete accounting",
                "category": "qualified-unknown",
                "question": "What was complete invocation usage?",
                "why_human": None,
                "investigation": ["Inspected canonical receipt; ledger incomplete."],
                "impact": "No complete efficiency winner can be inferred.",
                "evidence": ["judgment"],
                "options": [],
            }
        ],
        "sources": [
            {
                "id": "result",
                "label": "Selected original result",
                "path": "result.txt",
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        ],
        "citations": [
            {
                "id": "judgment",
                "source_id": "result",
                "start_line": 1,
                "end_line": 2,
                "label": "Original unresolved judgment and incomplete accounting",
            }
        ],
    }
    path = tmp_path / "source.json"
    path.write_text(json.dumps(spec))
    return path


def edit_spec(path: Path, edit) -> None:
    spec = json.loads(path.read_text())
    edit(spec)
    path.write_text(json.dumps(spec))


def test_review_retains_excerpt_without_exposing_source_paths(tmp_path: Path) -> None:
    spec = source_spec(tmp_path)
    original = (tmp_path / "result.txt").read_bytes()
    receipt = build_review(spec, tmp_path / "review")
    packet = load_packet(tmp_path / "review")
    assert receipt["packet_id"] == packet["packet_id"]
    assert packet["evidence"][0]["excerpt"] == original.decode().rstrip("\n")
    assert packet["sources"][0]["sha256"] == hashlib.sha256(original).hexdigest()
    assert "path" not in packet["sources"][0]
    assert packet["exceptions"][0]["category"] == "qualified-unknown"
    assert receipt["human_questions"] == 0
    assert (tmp_path / "result.txt").read_bytes() == original
    # WHY: The portable excerpt packet remains usable after the original source moves.
    (tmp_path / "result.txt").unlink()
    assert load_packet(tmp_path / "review") == packet


def test_review_checks_pinned_evidence_before_creating_output(tmp_path: Path) -> None:
    spec = source_spec(tmp_path)
    (tmp_path / "result.txt").write_text("different judgment\n")
    with pytest.raises(SkillEvalError, match="digest"):
        build_review(spec, tmp_path / "review")
    assert not (tmp_path / "review").exists()


@pytest.mark.parametrize("start,end", [(0, 1), (2, 1), (1, 3), (1, 202)])
def test_review_rejects_invalid_citation_range(tmp_path: Path, start: int, end: int) -> None:
    spec = source_spec(tmp_path)
    edit_spec(spec, lambda x: x["citations"][0].update(start_line=start, end_line=end))
    with pytest.raises((ValueError, SkillEvalError)):
        build_review(spec, tmp_path / "review")
    assert not (tmp_path / "review").exists()


def test_review_rejects_missing_reference(tmp_path: Path) -> None:
    spec = source_spec(tmp_path)
    edit_spec(spec, lambda x: x["summary"].update(evidence=["missing"]))
    with pytest.raises((ValueError, SkillEvalError)):
        build_review(spec, tmp_path / "review")


def test_human_question_requires_reason_investigation_and_evidence(tmp_path: Path) -> None:
    spec = source_spec(tmp_path)
    edit_spec(spec, lambda x: x["exceptions"][0].update(category="human-needed"))
    with pytest.raises((ValueError, SkillEvalError)):
        build_review(spec, tmp_path / "invalid")
    edit_spec(
        spec,
        lambda x: x["exceptions"][0].update(
            why_human="The original request leaves this material owner preference undecided."
        ),
    )
    assert build_review(spec, tmp_path / "review")["human_questions"] == 1


def test_review_is_new_generation_and_detects_packet_tampering(tmp_path: Path) -> None:
    spec = source_spec(tmp_path)
    output = tmp_path / "review"
    build_review(spec, output)
    with pytest.raises((OSError, SkillEvalError)):
        build_review(spec, output)
    path = output / "packet.json"
    packet = json.loads(path.read_text())
    packet["arms"][0]["cost"] = ["Free"]
    path.write_text(json.dumps(packet))
    with pytest.raises(SkillEvalError, match="identity"):
        load_packet(output)


def test_suggested_answers_fit_the_response_contract(tmp_path: Path) -> None:
    spec = source_spec(tmp_path)
    edit_spec(
        spec,
        lambda x: x["exceptions"][0].update(
            category="human-needed", why_human="Owner preference required", options=["x" * 10001]
        ),
    )
    with pytest.raises(ValueError, match="10000"):
        build_review(spec, tmp_path / "too-long")
    edit_spec(spec, lambda x: x["exceptions"][0].update(options=["x" * 10000]))
    assert build_review(spec, tmp_path / "valid")["human_questions"] == 1


def test_large_single_line_evidence_is_preserved_within_packet_budget(tmp_path: Path) -> None:
    spec = source_spec(tmp_path)
    text = json.dumps({"captured_output": "retained evidence " * 60000})
    (tmp_path / "result.txt").write_text(text + "\n")
    edit_spec(
        spec,
        lambda x: x["sources"][0].update(sha256=hashlib.sha256((text + "\n").encode()).hexdigest()),
    )
    edit_spec(spec, lambda x: x["citations"][0].update(end_line=1))
    build_review(spec, tmp_path / "review")
    assert load_packet(tmp_path / "review")["evidence"][0]["excerpt"] == text


def test_large_excerpts_still_obey_complete_packet_budget(tmp_path: Path) -> None:
    spec = source_spec(tmp_path)
    text = "x" * (5 * 1024 * 1024)
    (tmp_path / "result.txt").write_text(text + "\n")
    edit_spec(
        spec,
        lambda x: x["sources"][0].update(sha256=hashlib.sha256((text + "\n").encode()).hexdigest()),
    )
    edit_spec(spec, lambda x: x["citations"][0].update(end_line=1))
    edit_spec(spec, lambda x: x["citations"].append({**x["citations"][0], "id": "second"}))
    with pytest.raises(SkillEvalError, match="packet exceeds size"):
        build_review(spec, tmp_path / "review")
    assert not (tmp_path / "review").exists()
