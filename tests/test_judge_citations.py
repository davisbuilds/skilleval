"""Native task-root aliases preserve original evidence and raw citation strings."""

import json
from pathlib import Path

import pytest
from test_calibration import result_document, write_result
from test_judge import document, prepared, review_document, review_prepared, run_directory

from skilleval import calibration, judge
from skilleval.errors import SkillEvalError


def test_native_calibration_accepts_exact_collected_original_alias_without_rewriting(
    tmp_path: Path,
) -> None:
    bundle, packet, receipt = prepared(tmp_path)
    control = document(bundle, receipt)
    citation = control["judgments"][0]["citations"][0]
    original_path = citation["path"]
    citation["path"] = "task/" + original_path
    run = run_directory(
        tmp_path,
        receipt,
        control,
        artifacts={citation["path"]: (packet / original_path).read_bytes()},
    )
    original_final = (run / "final.txt").read_bytes()
    recorded = judge.record_agentic_calibration(bundle, packet, run, tmp_path / "records")
    stored = Path(recorded["path"]).parent
    assert recorded["comparison"]["agreement_count"] == 12
    assert (run / "final.txt").read_bytes() == original_final
    assert (stored / "final.txt").read_bytes() == original_final
    retained_result = json.loads((stored / "result.json").read_text())
    assert retained_result["judgments"][0]["citations"][0]["path"] == citation["path"]
    assert (stored / "artifacts.tar").read_bytes() == (run / "artifacts.tar").read_bytes()


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-collected",
        "modified-collected",
        "other-case",
        "outside-prefix",
        "repeated-prefix",
        "hidden-labels",
        "rubric",
        "investigation",
        "range",
    ],
)
def test_native_original_alias_never_expands_evidence_authority(
    tmp_path: Path,
    mutation: str,
) -> None:
    bundle, packet, receipt = prepared(tmp_path)
    control = document(bundle, receipt)
    citation = control["judgments"][0]["citations"][0]
    original_path = citation["path"]
    citation["path"] = "task/" + original_path
    collected = {citation["path"]: (packet / original_path).read_bytes()}
    if mutation == "missing-collected":
        collected = {"task/investigation/c01/probe.txt": b"Supplemental only.\n"}
    elif mutation == "modified-collected":
        collected[citation["path"]] = b"Modified guest source is not original evidence.\n"
    elif mutation == "other-case":
        citation["path"] = "task/cases/c02/source/ledger.py"
        collected = {citation["path"]: (packet / "cases/c02/source/ledger.py").read_bytes()}
    elif mutation == "outside-prefix":
        citation["path"] = "home/" + original_path
        collected = {citation["path"]: (packet / original_path).read_bytes()}
    elif mutation == "repeated-prefix":
        citation["path"] = "task/task/" + original_path
        collected = {citation["path"]: (packet / original_path).read_bytes()}
    elif mutation == "hidden-labels":
        citation["path"] = "task/suite.json"
        collected = {citation["path"]: (bundle / "suite.json").read_bytes()}
    elif mutation == "rubric":
        citation["path"] = "task/ASSESS.md"
        collected = {citation["path"]: (packet / "ASSESS.md").read_bytes()}
    elif mutation == "investigation":
        citation["path"] = "task/investigation/c01/original.py"
        collected = {citation["path"]: (packet / original_path).read_bytes()}
    else:
        citation["line_end"] = 999
    run = run_directory(tmp_path, receipt, control, artifacts=collected)
    before = (run / "final.txt").read_bytes()
    with pytest.raises(SkillEvalError):
        judge.record_agentic_calibration(bundle, packet, run, tmp_path / "records")
    assert (run / "final.txt").read_bytes() == before
    assert not (tmp_path / "records").exists()


def test_canonical_native_citation_still_uses_host_original_despite_modified_guest_copy(
    tmp_path: Path,
) -> None:
    bundle, packet, receipt = prepared(tmp_path)
    control = document(bundle, receipt)
    path = control["judgments"][0]["citations"][0]["path"]
    run = run_directory(
        tmp_path,
        receipt,
        control,
        artifacts={"task/" + path: b"Modified guest bytes.\n"},
    )
    result = judge.record_agentic_calibration(bundle, packet, run, tmp_path / "records")
    assert result["comparison"]["agreement_count"] == 12


def test_native_review_accepts_collected_case_alias_as_original_evidence(tmp_path: Path) -> None:
    bundle, report, packet, receipt = review_prepared(tmp_path)
    control = review_document(receipt)
    collected = {}
    for citation in control["judgments"][0]["citations"]:
        canonical = citation["path"]
        citation["path"] = "task/" + canonical
        collected[citation["path"]] = (packet / canonical).read_bytes()
    control["judgments"][0]["citations"].append(
        {"path": "task/report.json", "line_start": 1, "line_end": 1}
    )
    collected["task/report.json"] = (packet / "report.json").read_bytes()
    run = run_directory(tmp_path, receipt, control, artifacts=collected)
    before = (run / "final.txt").read_bytes()
    result = judge.record_agentic_review(bundle, report, packet, run, tmp_path / "records")
    assert result["verdict_counts"]["supported"] == 1
    stored = Path(result["path"]).parent
    assert (stored / "final.txt").read_bytes() == before
    citations = json.loads((stored / "result.json").read_text())["judgments"][0]["citations"]
    assert [item["path"] for item in citations] == [
        "task/case/source/add.py",
        "task/case/instruction.md",
        "task/report.json",
    ]


@pytest.mark.parametrize("mutation", ["report-only", "missing-collected", "modified-collected"])
def test_native_review_alias_still_requires_original_case_evidence(
    tmp_path: Path,
    mutation: str,
) -> None:
    bundle, report, packet, receipt = review_prepared(tmp_path)
    control = review_document(receipt)
    path = "task/report.json" if mutation == "report-only" else "task/case/source/add.py"
    control["judgments"][0]["citations"] = [{"path": path, "line_start": 1, "line_end": 1}]
    collected = {path: (packet / path.removeprefix("task/")).read_bytes()}
    if mutation == "missing-collected":
        collected = {"task/investigation/review/probe.txt": b"Supplement.\n"}
    elif mutation == "modified-collected":
        collected[path] = b"Modified guest source.\n"
    run = run_directory(tmp_path, receipt, control, artifacts=collected)
    with pytest.raises(SkillEvalError):
        judge.record_agentic_review(bundle, report, packet, run, tmp_path / "records")
    assert not (tmp_path / "records").exists()


def test_original_task_alias_cannot_be_used_as_an_investigation_artifact(tmp_path: Path) -> None:
    bundle, packet, receipt = prepared(tmp_path)
    control = document(bundle, receipt)
    path = control["judgments"][0]["citations"][0]["path"]
    control["judgments"][0]["artifact_citations"] = [
        {"path": "task/" + path, "line_start": 1, "line_end": 1}
    ]
    run = run_directory(
        tmp_path,
        receipt,
        control,
        artifacts={"task/" + path: (packet / path).read_bytes()},
    )
    with pytest.raises(SkillEvalError):
        judge.record_agentic_calibration(bundle, packet, run, tmp_path / "records")


def test_offline_calibration_keeps_canonical_only_citation_contract(tmp_path: Path) -> None:
    bundle, _, _ = prepared(tmp_path)
    control = result_document(bundle)
    citation = control["judgments"][0]["citations"][0]
    citation["path"] = "task/" + citation["path"]
    with pytest.raises(SkillEvalError, match="outside exported case"):
        calibration.record_calibration(
            bundle,
            write_result(tmp_path, control),
            tmp_path / "offline-records",
        )
