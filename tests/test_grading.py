"""Offline grader checks use synthetic development evidence only."""

import hashlib
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

import pytest

from skilleval import evidence, grading
from skilleval.errors import SkillEvalError


def imported_bundle(tmp_path: Path, **report_updates: Any) -> Path:
    source = tmp_path / "legacy"
    case = source / "cases" / "synthetic"
    (case / "source").mkdir(parents=True)
    (case / "instruction.md").write_text("Review this untrusted task.\n")
    (case / "oracle.md").write_text("Known target is only a starting point.\n")
    (case / "source" / "example.py").write_text("value = None\nprint(value + 1)\n")
    report = {
        "case_id": "synthetic",
        "report_id": "report-1",
        "outcome": "valid-report",
        "run_completed": True,
        "findings": [{"title": "None plus integer"}],
        "scope_drift": False,
    }
    report.update(report_updates)
    (source / "reports.json").write_text(
        json.dumps(
            [
                {"case_id": "other", "report_id": "secret", "historical_score": 99},
                report,
            ]
        )
    )
    bundle = tmp_path / "bundle"
    evidence.import_review(source, "synthetic", bundle)
    return bundle


def assessment_document(bundle: Path, **updates: Any) -> dict[str, Any]:
    result = {
        "schema_version": 1,
        "assessment_id": "assessment-1",
        "subject_id": "report-1",
        "evidence_sha256": hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest(),
        "rubric_version": "review-v1",
        "assessor_kind": "human",
        "assessor": "tester",
        "judgments": [
            {
                "finding_index": 0,
                "verdict": "supported",
                "reasoning": "Raises",
                "citations": [{"path": "case/source/example.py", "line_start": 1, "line_end": 2}],
            }
        ],
    }
    result.update(updates)
    return result


def write_assessment(tmp_path: Path, document: dict[str, Any]) -> Path:
    assessment = tmp_path / "input-assessment.json"
    assessment.write_text(json.dumps(document))
    return assessment


def test_prepare_packet_omits_provenance_and_reference_by_default(tmp_path: Path) -> None:
    bundle = imported_bundle(tmp_path, assignment_map={"treatment": "skill"}, historical_score=99)
    packet = tmp_path / "packet"
    result = grading.prepare_review(bundle, packet)
    assert result["status"] == "prepared"
    assert (packet / "case/source/example.py").read_bytes() == (
        bundle / "case/source/example.py"
    ).read_bytes()
    assert (packet / "reports.json").exists()
    assert not (packet / "case/oracle.md").exists()
    assert not (packet / "provenance").exists()
    selected = json.loads((packet / "reports.json").read_text())
    assert selected[0]["report_id"] == "report-1"
    assert "assignment_map" not in selected[0]
    assert "historical_score" not in selected[0]
    rubric = (packet / "ASSESS.md").read_text().lower()
    assert "untrusted" in rubric
    assert "nonexhaustive" in rubric
    assert "novel" in rubric
    assert "lexical" in rubric
    assert "blind" in rubric
    assert not packet.stat().st_mode & 0o077


def test_prepare_reference_is_explicit_and_destination_is_fresh(tmp_path: Path) -> None:
    bundle = imported_bundle(tmp_path)
    packet = tmp_path / "packet"
    grading.prepare_review(bundle, packet, include_reference=True)
    assert (packet / "case/oracle.md").exists()
    with pytest.raises(SkillEvalError):
        grading.prepare_review(bundle, packet)
    with pytest.raises(SkillEvalError):
        grading.prepare_review(bundle, bundle / "packet")


def test_record_persists_a_valid_assessment_without_claiming_correctness(tmp_path: Path) -> None:
    bundle = imported_bundle(tmp_path)
    assessment = write_assessment(tmp_path, assessment_document(bundle))
    output = tmp_path / "assessments"
    result = grading.record_assessment(bundle, assessment, output)
    assert result["status"] == "recorded"
    assert json.loads((output / "assessment-1.json").read_text())["subject_id"] == "report-1"
    with pytest.raises(SkillEvalError):
        grading.record_assessment(bundle, assessment, output)


@pytest.mark.parametrize(
    "updates",
    [
        {"evidence_sha256": "0" * 64},
        {"subject_id": "unknown"},
        {"unexpected": True},
        {"judgments": []},
        {"judgments": [{"finding_index": 1, "verdict": "unresolved", "reasoning": "Unknown"}]},
        {"judgments": [{"finding_index": 0, "verdict": "supported", "reasoning": "No evidence"}]},
        {"judgments": [{"finding_index": 0, "verdict": "unsupported", "reasoning": "No evidence"}]},
        {
            "judgments": [
                {
                    "finding_index": 0,
                    "verdict": "supported",
                    "reasoning": "Wrong path",
                    "citations": [
                        {"path": "case/source/missing.py", "line_start": 1, "line_end": 1}
                    ],
                }
            ]
        },
        {
            "judgments": [
                {
                    "finding_index": 0,
                    "verdict": "supported",
                    "reasoning": "Wrong span",
                    "citations": [
                        {"path": "case/source/example.py", "line_start": 1, "line_end": 3}
                    ],
                }
            ]
        },
    ],
)
def test_record_rejects_invalid_or_incomplete_assessments(
    tmp_path: Path, updates: dict[str, Any]
) -> None:
    bundle = imported_bundle(tmp_path)
    assessment = write_assessment(tmp_path, assessment_document(bundle, **updates))
    output = tmp_path / "assessments"
    with pytest.raises(SkillEvalError):
        grading.record_assessment(bundle, assessment, output)
    assert not output.exists() or not list(output.iterdir())


def test_unresolved_finding_can_have_no_citation(tmp_path: Path) -> None:
    bundle = imported_bundle(tmp_path)
    document = assessment_document(
        bundle,
        judgments=[
            {
                "finding_index": 0,
                "verdict": "unresolved",
                "reasoning": "Need runtime context",
            }
        ],
    )
    result = grading.record_assessment(
        bundle, write_assessment(tmp_path, document), tmp_path / "out"
    )
    assert result["status"] == "recorded"


def test_empty_completed_report_can_have_empty_assessment(tmp_path: Path) -> None:
    bundle = imported_bundle(tmp_path, findings=[])
    document = assessment_document(bundle, judgments=[])
    result = grading.record_assessment(
        bundle, write_assessment(tmp_path, document), tmp_path / "out"
    )
    assert result["status"] == "recorded"


def test_valid_report_from_failed_run_can_be_assessed(tmp_path: Path) -> None:
    bundle = imported_bundle(tmp_path, outcome="valid-report-in-failed-run", run_completed=False)
    document = assessment_document(bundle)
    result = grading.record_assessment(
        bundle, write_assessment(tmp_path, document), tmp_path / "out"
    )
    assert result["status"] == "recorded"
    assert json.loads((bundle / "reports.json").read_text())[0]["run_completed"] is False


@pytest.mark.parametrize(
    "updates",
    [
        {"outcome": "missing", "findings": []},
        {"outcome": "invalid", "findings": []},
        {"run_completed": False, "findings": []},
        {"findings": None},
    ],
)
def test_missing_or_invalid_report_cannot_be_assessed_as_clean(
    tmp_path: Path, updates: dict[str, Any]
) -> None:
    bundle = imported_bundle(tmp_path, **updates)
    document = assessment_document(bundle, judgments=[])
    with pytest.raises(SkillEvalError):
        grading.record_assessment(bundle, write_assessment(tmp_path, document), tmp_path / "out")


def test_record_refuses_writing_inside_evidence(tmp_path: Path) -> None:
    bundle = imported_bundle(tmp_path)
    assessment = write_assessment(tmp_path, assessment_document(bundle))
    with pytest.raises(SkillEvalError):
        grading.record_assessment(bundle, assessment, bundle / "assessments")


def test_superseding_assessment_preserves_prior_record_and_checks_identity(tmp_path: Path) -> None:
    bundle = imported_bundle(tmp_path)
    output = tmp_path / "out"
    first = assessment_document(bundle)
    grading.record_assessment(bundle, write_assessment(tmp_path, first), output)
    second = assessment_document(bundle, assessment_id="assessment-2", supersedes="assessment-1")
    grading.record_assessment(bundle, write_assessment(tmp_path, second), output)
    assert sorted(path.name for path in output.iterdir()) == [
        "assessment-1.json",
        "assessment-2.json",
    ]
    missing = assessment_document(bundle, assessment_id="assessment-3", supersedes="absent")
    with pytest.raises(SkillEvalError):
        grading.record_assessment(bundle, write_assessment(tmp_path, missing), output)


def test_assessment_validation_diagnostics_do_not_echo_control_content(tmp_path: Path) -> None:
    bundle = imported_bundle(tmp_path)
    marker = "PRIVATE-CONTROL-CONTENT"
    document = assessment_document(bundle, judgments=marker)
    with pytest.raises(SkillEvalError) as captured:
        grading.record_assessment(bundle, write_assessment(tmp_path, document), tmp_path / "out")
    assert marker not in "".join(traceback.format_exception(captured.value))


@pytest.mark.parametrize("control_kind", ["symlink", "parent-symlink", "oversize"])
def test_assessment_control_requires_bounded_regular_no_follow_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, control_kind: str
) -> None:
    bundle = imported_bundle(tmp_path)
    original = write_assessment(tmp_path, assessment_document(bundle))
    control = original
    if control_kind == "symlink":
        control = tmp_path / "linked.json"
        control.symlink_to(original)
    elif control_kind == "parent-symlink":
        linked = tmp_path / "linked"
        linked.symlink_to(tmp_path, target_is_directory=True)
        control = linked / original.name
    else:
        monkeypatch.setattr(grading, "MAX_ASSESSMENT_BYTES", 128, raising=False)
    with pytest.raises(SkillEvalError):
        grading.record_assessment(bundle, control, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    ("insertion", "reason"),
    [
        (',"assessor":"duplicate"', "duplicate JSON"),
        (',"model":NaN', "non-finite JSON"),
        (',"model":Infinity', "non-finite JSON"),
        (',"model":1e999', "non-finite JSON"),
    ],
)
def test_assessment_json_rejects_duplicate_keys_and_nonfinite_numbers(
    tmp_path: Path, insertion: str, reason: str
) -> None:
    bundle = imported_bundle(tmp_path)
    document = assessment_document(bundle)
    # The original is a valid control; only the inserted JSON changes admission.
    original = json.dumps(document)
    assessment = tmp_path / "assessment.json"
    assessment.write_text(original[:-1] + insertion + "}")
    with pytest.raises(SkillEvalError, match=reason):
        grading.record_assessment(bundle, assessment, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_superseded_assessment_cannot_be_a_symlink(tmp_path: Path) -> None:
    bundle = imported_bundle(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    first = write_assessment(tmp_path, assessment_document(bundle))
    (output / "assessment-1.json").symlink_to(first)
    second = assessment_document(bundle, assessment_id="assessment-2", supersedes="assessment-1")
    assessment = tmp_path / "second.json"
    assessment.write_text(json.dumps(second))
    with pytest.raises(SkillEvalError):
        grading.record_assessment(bundle, assessment, output)
    assert not (output / "assessment-2.json").exists()


def test_payload_read_refuses_a_replaced_symlink_even_if_contents_match(tmp_path: Path) -> None:
    bundle = imported_bundle(tmp_path)
    manifest = evidence.verify(bundle)
    target = bundle / "case/source/example.py"
    copy = tmp_path / "same-content.py"
    copy.write_bytes(target.read_bytes())
    target.parent.chmod(0o755)
    target.unlink()
    target.symlink_to(copy)
    with pytest.raises(SkillEvalError):
        grading._read_payload(bundle, manifest, "case/source/example.py")


@pytest.mark.parametrize("kind", ["assessment", "manifest"])
def test_fifo_controls_are_rejected_without_waiting_for_a_writer(tmp_path: Path, kind: str) -> None:
    bundle = imported_bundle(tmp_path)
    control = tmp_path / "assessment-fifo"
    if kind == "manifest":
        bundle.chmod(0o755)
        control = bundle / "manifest.json"
        control.unlink()
    os.mkfifo(control)
    script = """
import sys
from pathlib import Path
from skilleval import grading
from skilleval.errors import SkillEvalError
try:
    if sys.argv[1] == 'assessment':
        grading._read_assessment(Path(sys.argv[2]))
    else:
        grading.prepare_review(Path(sys.argv[3]), Path(sys.argv[4]))
except SkillEvalError:
    raise SystemExit(3)
raise SystemExit(0)
"""
    process = subprocess.run(
        [sys.executable, "-c", script, kind, str(control), str(bundle), str(tmp_path / "packet")],
        timeout=2,
        capture_output=True,
        check=False,
    )
    assert process.returncode == 3
    assert not (tmp_path / "packet").exists()
