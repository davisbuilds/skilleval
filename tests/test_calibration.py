"""Public semantic controls use actual captured files, with no model calls."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from skilleval import calibration, evidence
from skilleval.errors import SkillEvalError

CORPUS = Path(__file__).resolve().parents[1] / "examples/calibration/review-v1"


def captured(tmp_path: Path) -> Path:
    bundle = tmp_path / "bundle"
    evidence.snapshot(CORPUS, bundle)
    return bundle


def result_document(bundle: Path, **updates: Any) -> dict[str, Any]:
    suite = json.loads((bundle / "suite.json").read_text())
    document = {
        "schema_version": 1,
        "result_id": "judge-1",
        "suite_id": suite["suite_id"],
        "evidence_sha256": hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest(),
        "rubric_version": "calibration-review-v1",
        "assessor_kind": "human",
        "assessor": "test-author",
        "packet_sha256": calibration._packet(bundle)[4],
        "judgments": [
            {
                "probe_id": probe["probe_id"],
                "verdict": probe["expected"],
                "reasoning": probe["rationale"],
                "citations": probe["citations"],
            }
            for probe in suite["probes"]
        ],
        "limitations": ["Authored-label control only; no semantic judge executed."],
    }
    document.update(updates)
    return document


def write_result(tmp_path: Path, document: dict[str, Any], name: str = "input.json") -> Path:
    result = tmp_path / name
    result.write_text(json.dumps(document))
    return result


def test_packet_hides_labels_and_history_but_preserves_source_and_probes(tmp_path: Path) -> None:
    bundle = captured(tmp_path)
    packet = tmp_path / "packet"
    result = calibration.prepare_calibration(bundle, packet)
    assert result["status"] == "prepared"
    assert (packet / "cases/c01/source/ledger.py").read_bytes() == (
        bundle / "cases/c01/source/ledger.py"
    ).read_bytes()
    assert not (packet / "suite.json").exists()
    probes = json.loads((packet / "probes.json").read_text())
    assert len(probes) >= 11
    assert all("expected" not in probe and "rationale" not in probe for probe in probes)
    visible = b"".join(path.read_bytes() for path in packet.rglob("*") if path.is_file())
    assert b"WITHHELD-ARM-MARKER" not in visible
    assert b"WITHHELD-HISTORY-MARKER" not in visible
    assert b"known_reference" not in visible
    assert not packet.stat().st_mode & 0o077
    with pytest.raises(SkillEvalError):
        calibration.prepare_calibration(bundle, packet)


def test_record_is_append_only_and_bound_to_verified_evidence(tmp_path: Path) -> None:
    bundle = captured(tmp_path)
    control = write_result(tmp_path, result_document(bundle))
    output = tmp_path / "records"
    result = calibration.record_calibration(bundle, control, output)
    assert result["status"] == "recorded"
    assert json.loads((output / "judge-1.json").read_text())["result_id"] == "judge-1"
    with pytest.raises(SkillEvalError):
        calibration.record_calibration(bundle, control, output)
    other = write_result(tmp_path, result_document(bundle, evidence_sha256="0" * 64))
    with pytest.raises(SkillEvalError):
        calibration.record_calibration(bundle, other, tmp_path / "other")


def test_comparison_reports_confusion_missing_and_unresolved_separately(tmp_path: Path) -> None:
    bundle = captured(tmp_path)
    document = result_document(bundle)
    # A correct control establishes the detector can recognize every authored class.
    control = write_result(tmp_path, document)
    full = calibration.compare_calibration(bundle, [control])["results"][0]
    assert full["agreement_count"] == full["probe_count"]
    assert full["missing_count"] == 0
    document["judgments"][0]["verdict"] = "unsupported"
    document["judgments"][1]["verdict"] = "unresolved"
    document["judgments"].pop()
    partial = write_result(tmp_path, document, "partial.json")
    result = calibration.compare_calibration(bundle, [partial])["results"][0]
    assert result["confusion"]["supported"]["unsupported"] == 1
    assert result["missing_count"] == 1
    assert result["missing_counts"]["supported"] == 1
    assert result["expected_counts"]["supported"] == 3
    assert result["unresolved_count"] == 2  # Includes the authored insufficient-evidence control.
    assert result["resolved_count"] == result["judgment_count"] - 2
    assert result["agreement_count"] == result["probe_count"] - 3
    assert result["finding_count"] > 0
    assert result["coverage_probe_count"] > 0


def test_no_results_and_invalid_results_are_unknown_instead_of_zero_quality(tmp_path: Path) -> None:
    bundle = captured(tmp_path)
    empty = calibration.compare_calibration(bundle, [])
    assert empty["valid_result_count"] == 0
    assert empty["unassessed_probe_count"] == empty["probe_count"]
    bad = write_result(tmp_path, result_document(bundle, evidence_sha256="0" * 64))
    result = calibration.compare_calibration(bundle, [bad])
    assert result["invalid_result_count"] == 1
    assert result["unassessed_probe_count"] == result["probe_count"]
    assert result["results"] == []


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown-probe",
        "duplicate-probe",
        "bad-citation",
        "no-evidence",
        "llm-model",
        "unknown-field",
    ],
)
def test_invalid_results_cannot_be_recorded(tmp_path: Path, mutation: str) -> None:
    bundle = captured(tmp_path)
    document = result_document(bundle)
    if mutation == "unknown-probe":
        document["judgments"][0]["probe_id"] = "unknown"
    elif mutation == "duplicate-probe":
        document["judgments"].append(document["judgments"][0])
    elif mutation == "bad-citation":
        document["judgments"][0]["citations"][0]["line_end"] = 999
    elif mutation == "no-evidence":
        document["judgments"][0]["citations"] = []
    elif mutation == "llm-model":
        document["assessor_kind"] = "llm"
    else:
        document["labels"] = {"p01": "supported"}
    with pytest.raises(SkillEvalError):
        calibration.record_calibration(bundle, write_result(tmp_path, document), tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_packet_and_records_cannot_be_inside_bundle(tmp_path: Path) -> None:
    bundle = captured(tmp_path)
    with pytest.raises(SkillEvalError):
        calibration.prepare_calibration(bundle, bundle / "packet")
    with pytest.raises(SkillEvalError):
        calibration.record_calibration(
            bundle, write_result(tmp_path, result_document(bundle)), bundle / "records"
        )


def test_compare_rejects_duplicate_result_identity(tmp_path: Path) -> None:
    bundle = captured(tmp_path)
    result = write_result(tmp_path, result_document(bundle))
    with pytest.raises(SkillEvalError):
        calibration.compare_calibration(bundle, [result, result])


def test_result_identity_pins_exact_export_and_rubric(tmp_path: Path) -> None:
    bundle = captured(tmp_path)
    document = result_document(bundle, packet_sha256="0" * 64)
    with pytest.raises(SkillEvalError, match="identity mismatch"):
        calibration.record_calibration(bundle, write_result(tmp_path, document), tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_result_cannot_cite_hidden_labels_or_other_cases(tmp_path: Path) -> None:
    bundle = captured(tmp_path)
    for cited_path in ("suite.json", "cases/c02/source/ledger.py"):
        document = result_document(bundle)
        document["judgments"][0]["citations"] = [
            {"path": cited_path, "line_start": 1, "line_end": 1}
        ]
        with pytest.raises(SkillEvalError, match="outside exported case"):
            calibration.record_calibration(
                bundle, write_result(tmp_path, document), tmp_path / "out"
            )


def test_result_file_must_be_regular_without_symlinks(tmp_path: Path) -> None:
    bundle = captured(tmp_path)
    original = write_result(tmp_path, result_document(bundle))
    linked = tmp_path / "linked.json"
    linked.symlink_to(original)
    with pytest.raises(SkillEvalError, match="bounded regular"):
        calibration.record_calibration(bundle, linked, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    ("probe_id", "supplement"),
    [("p07", "probes.json"), ("p11", "ASSESS.md"), ("p12", "ASSESS.md")],
)
def test_same_case_evidence_accepts_supplemental_packet_citations(
    tmp_path: Path, probe_id: str, supplement: str
) -> None:
    bundle = captured(tmp_path)
    document = result_document(bundle)
    judgment = next(item for item in document["judgments"] if item["probe_id"] == probe_id)
    judgment["citations"].append({"path": supplement, "line_start": 1, "line_end": 1})
    result = calibration.record_calibration(
        bundle, write_result(tmp_path, document), tmp_path / "out"
    )
    assert result["status"] == "recorded"
    stored = json.loads((tmp_path / "out/judge-1.json").read_text())
    recorded = next(item for item in stored["judgments"] if item["probe_id"] == probe_id)
    assert recorded["citations"][-1]["path"] == supplement


@pytest.mark.parametrize("supplement", ["ASSESS.md", "probes.json"])
def test_supplemental_citations_cannot_replace_resolved_case_evidence(
    tmp_path: Path, supplement: str
) -> None:
    bundle = captured(tmp_path)
    document = result_document(bundle)
    document["judgments"][0]["citations"] = [{"path": supplement, "line_start": 1, "line_end": 1}]
    with pytest.raises(SkillEvalError):
        calibration.record_calibration(bundle, write_result(tmp_path, document), tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("path", ["packet.json", "cases/c02/source/ledger.py"])
def test_supplemental_packet_evidence_does_not_admit_identity_or_other_cases(
    tmp_path: Path, path: str
) -> None:
    bundle = captured(tmp_path)
    document = result_document(bundle)
    document["judgments"][0]["citations"].extend(
        [{"path": name, "line_start": 1, "line_end": 1} for name in ("ASSESS.md", path)]
    )
    with pytest.raises(SkillEvalError):
        calibration.record_calibration(bundle, write_result(tmp_path, document), tmp_path / "out")
    assert not (tmp_path / "out").exists()
