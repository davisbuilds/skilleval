"""Agentic judge storage controls; no imported fixture code or models execute."""

import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Any

import pytest

from skilleval import evidence, judge
from skilleval.errors import SkillEvalError
from skilleval.gateway import EFFORT, MODEL

CORPUS = Path(__file__).resolve().parents[1] / "examples/calibration/review-v1"


def test_investigation_packet_replaces_static_prohibition_and_withholds_labels(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle"
    evidence.snapshot(CORPUS, bundle)
    packet = tmp_path / "packet"
    receipt = judge.prepare_agentic_calibration(bundle, packet, investigation=True)
    rubric = (packet / "ASSESS.md").read_text()
    assert "never execute source or commands" not in rubric
    assert "disposable VM" in rubric
    assert "clone" in rubric
    assert receipt["rubric_version"] == "calibration-agentic-v1"
    assert not (packet / "suite.json").exists()
    probes = json.loads((packet / "probes.json").read_text())
    assert all("expected" not in probe and "rationale" not in probe for probe in probes)


def prepared(tmp_path: Path, investigation: bool = True) -> tuple[Path, Path, dict[str, Any]]:
    bundle, packet = tmp_path / "bundle", tmp_path / "packet"
    evidence.snapshot(CORPUS, bundle)
    receipt = judge.prepare_agentic_calibration(bundle, packet, investigation=investigation)
    return bundle, packet, receipt


def document(bundle: Path, preparation: dict[str, Any]) -> dict[str, Any]:
    suite = json.loads((bundle / "suite.json").read_text())
    return {
        "schema_version": 1,
        "result_id": "judge-1",
        "suite_id": suite["suite_id"],
        "evidence_sha256": preparation["evidence_sha256"],
        "packet_sha256": preparation["packet_sha256"],
        "rubric_version": preparation["rubric_version"],
        "assessor_kind": "llm",
        "assessor": "synthetic storage control, no model invocation",
        "model": MODEL,
        "investigation_status": "not-needed" if preparation["investigation"] else "not-permitted",
        "judgments": [
            {
                "probe_id": probe["probe_id"],
                "verdict": probe["expected"],
                "reasoning": probe["rationale"],
                "citations": probe["citations"],
            }
            for probe in suite["probes"]
        ],
    }


def run_directory(
    tmp_path: Path,
    preparation: dict[str, Any],
    result: dict[str, Any],
    *,
    status: str = "completed",
    artifacts: dict[str, bytes] | None = None,
    mode: str = "judge",
    run_name: str = "run",
) -> Path:
    run = tmp_path / run_name
    run.mkdir(exist_ok=True)
    frozen = tmp_path / f"native-input-{run_name}"
    if not frozen.exists():
        evidence.snapshot(Path(preparation["path"]), frozen)
    payloads = dict(artifacts or {"task/investigation/c01/probe.txt": b"Observed: 1200\n"})
    payloads["evidence/final.txt"] = json.dumps(result).encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data in payloads.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    archive_data = buffer.getvalue()
    archive_hash = hashlib.sha256(archive_data).hexdigest()
    manifest = {
        "schema_version": 1,
        "kind": "skilleval-native-artifacts",
        "sha256": archive_hash,
        "entries": [
            {
                "path": name,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "executable": False,
            }
            for name, data in payloads.items()
        ],
    }
    manifest_data = json.dumps(manifest).encode()
    (run / "final.txt").write_text(json.dumps(result))
    (run / "prompt.txt").write_text(preparation["prompt"])
    (run / "artifacts.tar").write_bytes(archive_data)
    (run / "artifacts.json").write_bytes(manifest_data)
    (run / "native-run.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "skilleval-native-run",
                "mode": mode,
                "status": status,
                "input_digest": preparation["input_digest"],
                "original_input": {"path": str(frozen), "sha256": preparation["input_digest"]},
                "copied_input": {"sha256": preparation["input_digest"]},
                "original_input_unchanged": True,
                "model": MODEL,
                "effort": EFFORT,
                "investigation": preparation["investigation"],
                "prompt_sha256": hashlib.sha256(preparation["prompt"].encode()).hexdigest(),
                "final_text": "final.txt",
                "artifacts": {
                    "archive": "artifacts.tar",
                    "manifest": "artifacts.json",
                    "sha256": archive_hash,
                    "manifest_sha256": hashlib.sha256(manifest_data).hexdigest(),
                },
            }
        )
    )
    return run


def test_static_and_investigative_inputs_share_case_evidence_but_pin_permissions(
    tmp_path: Path,
) -> None:
    bundle, packet, active = prepared(tmp_path)
    static = tmp_path / "static"
    passive = judge.prepare_agentic_calibration(bundle, static, investigation=False)
    for path in packet.rglob("*"):
        if path.is_file() and path.name not in {"ASSESS.md", "packet.json"}:
            assert path.read_bytes() == (static / path.relative_to(packet)).read_bytes()
    assert active["packet_sha256"] != passive["packet_sha256"]
    assert passive["rubric_version"] == "calibration-static-v1"
    assert "Do not execute imported source" in (static / "ASSESS.md").read_text()
    frozen = tmp_path / "frozen"
    snapshot = evidence.snapshot(packet, frozen)
    assert snapshot["manifest_sha256"] == active["input_digest"]
    control = document(bundle, active)
    run = run_directory(tmp_path, active, control)
    recorded = judge.record_agentic_calibration(bundle, frozen, run, tmp_path / "records")
    assert recorded["status"] == "recorded"


def test_records_retain_original_packet_artifacts_and_pending_independent_audit(
    tmp_path: Path,
) -> None:
    bundle, packet, prep = prepared(tmp_path)
    control = document(bundle, prep)
    control["judgments"][0]["artifact_citations"] = [
        {"path": "task/investigation/c01/probe.txt", "line_start": 1, "line_end": 1}
    ]
    run = run_directory(tmp_path, prep, control)
    output = tmp_path / "records"
    result = judge.record_agentic_calibration(bundle, packet, run, output)
    assert result["independent_audit"] == "required-pending"
    comparison = result["comparison"]
    assert comparison["agreement_count"] == comparison["probe_count"] == 12
    assert comparison["missing_count"] == 0
    assert comparison["expected_counts"]["supported"] == 3
    stored = Path(result["path"]).parent
    assert (stored / "packet/cases/c01/source/ledger.py").read_bytes() == (
        packet / "cases/c01/source/ledger.py"
    ).read_bytes()
    assert (stored / "artifacts.tar").read_bytes() == (run / "artifacts.tar").read_bytes()
    assert (stored / "authored-labels.json").exists()
    before = (stored / "result.json").read_bytes()
    with pytest.raises(SkillEvalError, match="fresh"):
        judge.record_agentic_calibration(bundle, packet, run, output)
    assert (stored / "result.json").read_bytes() == before


def test_partial_blocked_failed_run_is_retained_without_inventing_clean_completion(
    tmp_path: Path,
) -> None:
    bundle, packet, prep = prepared(tmp_path)
    control = document(bundle, prep)
    control["judgments"] = [
        {"probe_id": "p01", "verdict": "unresolved", "reasoning": "Required tool unavailable."}
    ]
    control["investigation_status"] = "blocked"
    run = run_directory(tmp_path, prep, control, status="failed")
    result = judge.record_agentic_calibration(bundle, packet, run, tmp_path / "records")
    assert result["execution_status"] == "failed"
    assert result["comparison"]["unresolved_count"] == 1
    assert result["comparison"]["missing_count"] == 11
    assert result["comparison"]["missing_counts"]["supported"] == 2
    assert result["comparison"]["resolved_count"] == 0


@pytest.mark.parametrize(
    "mutation",
    ["artifact-only", "hidden-labels", "other-case", "invented-artifact", "artifact-range"],
)
def test_artifacts_cannot_replace_original_or_cross_case_evidence(
    tmp_path: Path, mutation: str
) -> None:
    bundle, packet, prep = prepared(tmp_path)
    control = document(bundle, prep)
    item = control["judgments"][0]
    artifact = {"path": "task/investigation/c01/probe.txt", "line_start": 1, "line_end": 1}
    item["artifact_citations"] = [artifact]
    if mutation == "artifact-only":
        item["citations"] = []
    elif mutation == "hidden-labels":
        item["citations"] = [{"path": "suite.json", "line_start": 1, "line_end": 1}]
    elif mutation == "other-case":
        item["citations"] = [{"path": "cases/c02/source/ledger.py", "line_start": 1, "line_end": 1}]
    elif mutation == "invented-artifact":
        artifact["path"] = "task/investigation/c01/missing.txt"
    else:
        artifact["line_end"] = 999
    run = run_directory(tmp_path, prep, control)
    with pytest.raises(SkillEvalError):
        judge.record_agentic_calibration(bundle, packet, run, tmp_path / "records")
    assert not (tmp_path / "records").exists()


@pytest.mark.parametrize("mutation", ["packet", "source", "artifacts", "input", "result"])
def test_changed_inputs_are_rejected_before_record_creation(tmp_path: Path, mutation: str) -> None:
    bundle, packet, prep = prepared(tmp_path)
    control = document(bundle, prep)
    run = run_directory(tmp_path, prep, control)
    if mutation == "packet":
        (packet / "cases/c01/source/ledger.py").write_text("Changed source\n")
    elif mutation == "source":
        (bundle / "cases/c01/source/ledger.py").chmod(0o600)
        (bundle / "cases/c01/source/ledger.py").write_text("Changed original\n")
    elif mutation == "artifacts":
        (run / "artifacts.tar").write_bytes(b"changed archive")
    elif mutation == "input":
        receipt = json.loads((run / "native-run.json").read_text())
        receipt["input_digest"] = "0" * 64
        (run / "native-run.json").write_text(json.dumps(receipt))
    else:
        (run / "final.txt").write_text('{"result_id":"x", "result_id":"y"}')
    with pytest.raises(SkillEvalError):
        judge.record_agentic_calibration(bundle, packet, run, tmp_path / "records")
    assert not (tmp_path / "records").exists()


def test_new_revision_preserves_prior_record_and_requires_matching_evidence(tmp_path: Path) -> None:
    bundle, packet, prep = prepared(tmp_path)
    control = document(bundle, prep)
    run = run_directory(tmp_path, prep, control)
    output = tmp_path / "records"
    first = judge.record_agentic_calibration(bundle, packet, run, output)
    original = Path(first["path"]).read_bytes()
    control.update(result_id="judge-2", supersedes="judge-1")
    run = run_directory(tmp_path, prep, control)
    second = judge.record_agentic_calibration(bundle, packet, run, output)
    assert Path(first["path"]).read_bytes() == original
    assert json.loads(Path(second["path"]).read_text())["supersedes"] == "judge-1"
    control.update(result_id="judge-3", supersedes="missing")
    run = run_directory(tmp_path, prep, control)
    with pytest.raises(SkillEvalError):
        judge.record_agentic_calibration(bundle, packet, run, output)


def review_prepared(
    tmp_path: Path, *, findings: list[dict[str, Any]] | None = None, execution: str = "completed"
) -> tuple[Path, Path, Path, dict[str, Any]]:
    source = tmp_path / "review-source"
    (source / "case/source").mkdir(parents=True)
    (source / "case/instruction.md").write_text("Return the sum of two integers.\n")
    (source / "case/source/add.py").write_text("def add(a, b):\n    return a - b\n")
    (source / "case/oracle.md").write_text("WITHHELD-ORACLE\n")
    (source / "reports.json").write_text('[{"arm":"WITHHELD-ARM"}]')
    bundle = tmp_path / "review-bundle"
    evidence.snapshot(source, bundle)
    report_data = {
        "findings": findings
        if findings is not None
        else [{"title": "Subtraction", "body": "add(2, 1) returns 1 rather than 3."}],
        "summary": "Inspect additive contract.",
        "arm": "WITHHELD-NEW-ARM",
    }
    native = run_directory(
        tmp_path,
        {
            "path": str(source),
            "input_digest": hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest(),
            "prompt": "Review the integer addition contract.",
            "investigation": None,
        },
        report_data,
        status=execution,
        mode="review",
        run_name="native-review",
    )
    report = native / "final.txt"
    packet = tmp_path / "review-packet"
    receipt = judge.prepare_agentic_review(bundle, report, packet)
    return bundle, report, packet, receipt


def review_document(preparation: dict[str, Any], *, empty: bool = False) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "result_id": "review-judge-1",
        "evidence_sha256": preparation["evidence_sha256"],
        "packet_sha256": preparation["packet_sha256"],
        "report_sha256": preparation["report_sha256"],
        "rubric_version": preparation["rubric_version"],
        "assessor_kind": "llm",
        "assessor": "synthetic storage control, no model invocation",
        "model": MODEL,
        "investigation_status": "not-needed",
        "judgments": []
        if empty
        else [
            {
                "finding_index": 0,
                "verdict": "supported",
                "reasoning": "The integer addition contract is violated by subtraction.",
                "citations": [
                    {"path": "case/source/add.py", "line_start": 1, "line_end": 2},
                    {"path": "case/instruction.md", "line_start": 1, "line_end": 1},
                ],
            }
        ],
    }


def test_native_review_packet_hides_original_answers_and_judges_new_report(tmp_path: Path) -> None:
    bundle, report, packet, prep = review_prepared(tmp_path)
    visible = b"".join(path.read_bytes() for path in packet.rglob("*") if path.is_file())
    assert b"WITHHELD" not in visible
    assert json.loads((packet / "report.json").read_text())["execution_status"] == "completed"
    control = review_document(prep)
    control["judgments"][0]["artifact_citations"] = [
        {"path": "task/investigation/review/probe.txt", "line_start": 1, "line_end": 1}
    ]
    run = run_directory(
        tmp_path, prep, control, artifacts={"task/investigation/review/probe.txt": b"1\n"}
    )
    result = judge.record_agentic_review(bundle, report, packet, run, tmp_path / "records")
    assert result["finding_count"] == 1
    assert result["verdict_counts"]["supported"] == 1
    assert result["independent_audit"] == "required-pending"
    assert result["review_execution_status"] == "completed"


def test_failed_empty_review_retains_failure_separately_from_no_findings(tmp_path: Path) -> None:
    bundle, report, packet, prep = review_prepared(tmp_path, findings=[], execution="failed")
    assert prep["review_execution_status"] == "failed"
    run = run_directory(tmp_path, prep, review_document(prep, empty=True))
    result = judge.record_agentic_review(bundle, report, packet, run, tmp_path / "records")
    assert result["review_execution_status"] == "failed"
    assert result["finding_count"] == 0
    assert result["execution_status"] == "completed"  # Judge execution only.
    assert result["independent_audit"] == "required-pending"


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "report-only", "hidden", "report-drift"]
)
def test_review_requires_every_finding_and_original_source(tmp_path: Path, mutation: str) -> None:
    bundle, report, packet, prep = review_prepared(tmp_path)
    control = review_document(prep)
    if mutation == "missing":
        control["judgments"] = []
    elif mutation == "duplicate":
        control["judgments"].append(control["judgments"][0])
    elif mutation == "report-only":
        control["judgments"][0]["citations"] = [
            {"path": "report.json", "line_start": 1, "line_end": 1}
        ]
    elif mutation == "hidden":
        control["judgments"][0]["citations"] = [
            {"path": "case/oracle.md", "line_start": 1, "line_end": 1}
        ]
    else:
        report.write_text('{"findings":[]}')
    run = run_directory(tmp_path, prep, control)
    with pytest.raises(SkillEvalError):
        judge.record_agentic_review(bundle, report, packet, run, tmp_path / "records")
    assert not (tmp_path / "records").exists()


def test_malformed_review_is_not_exported_as_clean(tmp_path: Path) -> None:
    bundle, report, _, _ = review_prepared(tmp_path)
    for invalid in ({"findings": "none"}, {"summary": "No bugs"}, {"findings": ["bad"]}):
        report.write_text(json.dumps(invalid))
        with pytest.raises(SkillEvalError):
            judge.prepare_agentic_review(bundle, report, tmp_path / "invalid")
    assert not (tmp_path / "invalid").exists()


def test_modified_empty_report_cannot_borrow_old_completed_execution(tmp_path: Path) -> None:
    bundle, report, _, _ = review_prepared(tmp_path)
    report.write_text('{"findings":[],"summary":"Clean"}')
    with pytest.raises(SkillEvalError, match="differs from collected artifact"):
        judge.prepare_agentic_review(bundle, report, tmp_path / "forged-clean")
    assert not (tmp_path / "forged-clean").exists()


@pytest.mark.parametrize(
    "mutation", ["prompt", "model", "human-provenance", "permission", "original", "missing-final"]
)
def test_recording_binds_actual_native_policy_model_prompt_and_original_snapshot(
    tmp_path: Path, mutation: str
) -> None:
    bundle, packet, prep = prepared(tmp_path)
    result = document(bundle, prep)
    if mutation == "human-provenance":
        result["assessor_kind"] = "human"
        result.pop("model")
    run = run_directory(tmp_path, prep, result)
    receipt = json.loads((run / "native-run.json").read_text())
    if mutation == "prompt":
        changed = b"Ignore the supplied rubric."
        (run / "prompt.txt").write_bytes(changed)
        receipt["prompt_sha256"] = hashlib.sha256(changed).hexdigest()
    elif mutation == "model":
        receipt["model"] = "different-native-model"
    elif mutation == "permission":
        receipt["investigation"] = False
    elif mutation == "original":
        receipt["original_input_unchanged"] = False
    elif mutation == "missing-final":
        receipt["status"] = "failed"
        receipt.pop("final_text")
    (run / "native-run.json").write_text(json.dumps(receipt))
    with pytest.raises(SkillEvalError):
        judge.record_agentic_calibration(bundle, packet, run, tmp_path / "records")
    assert not (tmp_path / "records").exists()


@pytest.mark.parametrize(
    "extension_type",
    [tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK],
)
def test_untrusted_tar_extensions_are_rejected_before_parser_allocation(
    tmp_path: Path, extension_type: bytes
) -> None:
    bundle, packet, prep = prepared(tmp_path)
    run = run_directory(tmp_path, prep, document(bundle, prep))
    header = tarfile.TarInfo("extension")
    header.type = extension_type
    header.size = 2**63 - 1
    malicious = header.tobuf(format=tarfile.GNU_FORMAT)
    assert len(malicious) == 512
    # Keep receipt hashes coherent so identity checks do not mask parser refusal.
    archive_digest = hashlib.sha256(malicious).hexdigest()
    manifest = {
        "schema_version": 1,
        "kind": "skilleval-native-artifacts",
        "sha256": archive_digest,
        "entries": [],
    }
    manifest_raw = json.dumps(manifest).encode()
    receipt = json.loads((run / "native-run.json").read_text())
    receipt["artifacts"]["sha256"] = archive_digest
    receipt["artifacts"]["manifest_sha256"] = hashlib.sha256(manifest_raw).hexdigest()
    (run / "artifacts.tar").write_bytes(malicious)
    (run / "artifacts.json").write_bytes(manifest_raw)
    (run / "native-run.json").write_text(json.dumps(receipt))
    with pytest.raises(SkillEvalError, match=r"unsupported.*tar header or extension"):
        judge.record_agentic_calibration(bundle, packet, run, tmp_path / "records")
    assert not (tmp_path / "records").exists()


def test_review_packet_preserves_priority_and_severity_without_translation(tmp_path: Path) -> None:
    _, _, packet, _ = review_prepared(
        tmp_path,
        findings=[
            {"title": "Captured priority", "body": "Original finding.", "priority": 2},
            {
                "title": "Both supplied",
                "body": "Keep independent source fields.",
                "priority": 1,
                "severity": "original-severity",
            },
        ],
    )
    findings = json.loads((packet / "report.json").read_bytes())["findings"]
    assert findings[0]["priority"] == 2
    assert "severity" not in findings[0]
    assert findings[1]["priority"] == 1
    assert findings[1]["severity"] == "original-severity"


@pytest.mark.parametrize("kind", ["calibration", "review"])
def test_agentic_source_modes_survive_preparation_capture_verification_and_recording(
    tmp_path: Path, kind: str
) -> None:
    import shutil
    import subprocess

    original = tmp_path / "original"
    if kind == "calibration":
        shutil.copytree(CORPUS, original)
        source_prefix = "cases/c01/source"
    else:
        (original / "case/source").mkdir(parents=True)
        (original / "case/instruction.md").write_text("Inspect the supplied executable tool.\n")
        source_prefix = "case/source"
    # Authored positive control, never arbitrary imported code. Execution via
    # the original command form distinguishes the lost-executable-bit defect.
    program = b"#!/bin/sh\nprintf 'mode-sensitive-control\\n'\n"
    tool_relative = source_prefix + "/tool.sh"
    helper_relative = source_prefix + "/helper.txt"
    (original / tool_relative).write_bytes(program)
    (original / tool_relative).chmod(0o755)
    (original / helper_relative).write_bytes(b"nonexecutable control\n")
    (original / helper_relative).chmod(0o644)
    bundle = tmp_path / "mode-bundle"
    evidence.snapshot(original, bundle)
    packet = tmp_path / "mode-packet"
    report = tmp_path / "selected-report.json"
    report.write_text('{"findings":[]}')
    if kind == "calibration":
        prep = judge.prepare_agentic_calibration(bundle, packet, investigation=True)
        control = document(bundle, prep)
    else:
        prep = judge.prepare_agentic_review(bundle, report, packet, investigation=True)
        control = review_document(prep, empty=True)
    assert (packet / tool_relative).read_bytes() == program
    assert (packet / tool_relative).stat().st_mode & 0o777 == 0o755
    assert (packet / helper_relative).stat().st_mode & 0o777 == 0o644
    invocation = subprocess.run(
        ["./tool.sh"], cwd=packet / source_prefix, capture_output=True, timeout=5, check=False
    )
    assert invocation.returncode == 0 and invocation.stdout == b"mode-sensitive-control\n"
    modes = json.loads((packet / "source-modes.json").read_bytes())
    assert modes["files"][tool_relative] is True
    assert modes["files"][helper_relative] is False
    assert "suite.json" not in modes["files"]
    frozen = tmp_path / "mode-input"
    receipt = evidence.snapshot(packet, frozen)
    assert receipt["manifest_sha256"] == prep["input_digest"]
    # A read-only evidence snapshot still retains the executable flag.
    assert (frozen / tool_relative).stat().st_mode & 0o111
    run = run_directory(tmp_path, prep, control)
    records = tmp_path / "mode-records"
    if kind == "calibration":
        result = judge.record_agentic_calibration(bundle, frozen, run, records)
    else:
        result = judge.record_agentic_review(bundle, report, frozen, run, records)
    retained = Path(result["path"]).parent / "packet"
    assert (retained / tool_relative).stat().st_mode & 0o777 == 0o755
    assert not (retained / helper_relative).stat().st_mode & 0o111
    mode_file = packet / "source-modes.json"
    original_modes = mode_file.read_bytes()
    modes["files"][tool_relative] = False
    mode_file.write_text(json.dumps(modes))
    if kind == "calibration":
        with pytest.raises(SkillEvalError, match="differs from original evidence"):
            judge.record_agentic_calibration(bundle, packet, run, tmp_path / "forged-modes")
    else:
        with pytest.raises(SkillEvalError, match="differs from original evidence"):
            judge.record_agentic_review(bundle, report, packet, run, tmp_path / "forged-modes")
    mode_file.write_bytes(original_modes)
    (packet / tool_relative).chmod(0o644)
    if kind == "calibration":
        with pytest.raises(SkillEvalError, match="differs from original evidence"):
            judge.record_agentic_calibration(bundle, packet, run, tmp_path / "invalid-records")
    else:
        with pytest.raises(SkillEvalError, match="differs from original evidence"):
            judge.record_agentic_review(bundle, report, packet, run, tmp_path / "invalid-records")


@pytest.mark.parametrize("kind", ["calibration", "review"])
def test_judge_prompt_supplies_actual_native_model_effort_and_pins_identity(
    tmp_path: Path, kind: str
) -> None:
    if kind == "calibration":
        bundle, packet, prep = prepared(tmp_path)
        control = document(bundle, prep)
    else:
        bundle, report, packet, prep = review_prepared(tmp_path)
        control = review_document(prep)
    assert prep["model"] == MODEL
    assert prep["effort"] == EFFORT
    assert f"packet_sha256={prep['packet_sha256']}" in prep["prompt"]
    assert f"model={MODEL}" in prep["prompt"]
    assert f"effort={EFFORT}" in prep["prompt"]
    run = run_directory(tmp_path, prep, control)
    # Preserve the envelope hash while removing its actual backend identity;
    # recording must still bind the exact prepared invocation prompt.
    changed = prep["prompt"].replace(f"model={MODEL}", "model=guess-the-backend")
    (run / "prompt.txt").write_text(changed)
    receipt_file = run / "native-run.json"
    receipt = json.loads(receipt_file.read_bytes())
    receipt["prompt_sha256"] = hashlib.sha256(changed.encode()).hexdigest()
    receipt_file.write_text(json.dumps(receipt))
    with pytest.raises(SkillEvalError, match="exact prepared judge prompt"):
        if kind == "calibration":
            judge.record_agentic_calibration(bundle, packet, run, tmp_path / "records")
        else:
            judge.record_agentic_review(bundle, report, packet, run, tmp_path / "records")


def attach_native_audit(run: Path) -> bytes:
    receipt_file = run / "native-run.json"
    receipt = json.loads(receipt_file.read_bytes())
    identity = {"runtime": {"version": "synthetic-fixture", "sha256": "a" * 64}}
    audit = json.dumps(
        {**receipt, "identity": identity, "steps": [{"label": "full audit evidence"}]}
    ).encode()
    canonical_identity = evidence._json_bytes(identity)
    receipt["identity"] = {
        "bytes": len(canonical_identity),
        "sha256": hashlib.sha256(canonical_identity).hexdigest(),
        "audit_field": "identity",
    }
    (run / "native-audit.json").write_bytes(audit)
    receipt["audit"] = {
        "path": "native-audit.json",
        "bytes": len(audit),
        "sha256": hashlib.sha256(audit).hexdigest(),
    }
    receipt_file.write_text(json.dumps(receipt))
    return audit


def test_referenced_native_audit_is_verified_and_retained(tmp_path: Path) -> None:
    bundle, packet, prep = prepared(tmp_path)
    run = run_directory(tmp_path, prep, document(bundle, prep))
    audit = attach_native_audit(run)
    result = judge.record_agentic_calibration(bundle, packet, run, tmp_path / "records")
    assert (Path(result["path"]).parent / "native-audit.json").read_bytes() == audit
    assert (
        json.loads((Path(result["path"]).parent / "native-run.json").read_bytes())["audit"][
            "sha256"
        ]
        == hashlib.sha256(audit).hexdigest()
    )


@pytest.mark.parametrize(
    "mutation", ["missing", "changed", "swapped", "path", "bytes", "digest", "oversized"]
)
def test_native_audit_drift_is_rejected_before_recording(tmp_path: Path, mutation: str) -> None:
    bundle, packet, prep = prepared(tmp_path)
    run = run_directory(tmp_path, prep, document(bundle, prep))
    audit = attach_native_audit(run)
    audit_file = run / "native-audit.json"
    receipt_file = run / "native-run.json"
    receipt = json.loads(receipt_file.read_bytes())
    if mutation == "missing":
        audit_file.unlink()
    elif mutation == "changed":
        audit_file.write_bytes(audit.replace(b"full audit", b"fake audit"))
    elif mutation == "swapped":
        swapped = json.loads(audit)
        swapped["input_digest"] = "0" * 64
        replacement = json.dumps(swapped).encode()
        audit_file.write_bytes(replacement)
        # Even a coherent replacement reference cannot borrow this invocation.
        receipt["audit"]["bytes"] = len(replacement)
        receipt["audit"]["sha256"] = hashlib.sha256(replacement).hexdigest()
    elif mutation == "path":
        receipt["audit"]["path"] = "../native-audit.json"
    elif mutation == "bytes":
        receipt["audit"]["bytes"] += 1
    elif mutation == "digest":
        receipt["audit"]["sha256"] = "g" * 64
    else:
        receipt["audit"]["bytes"] = evidence.MAX_FILE_BYTES + 1
    receipt_file.write_text(json.dumps(receipt))
    with pytest.raises(SkillEvalError, match="native audit"):
        judge.record_agentic_calibration(bundle, packet, run, tmp_path / "records")
    assert not (tmp_path / "records").exists()


@pytest.mark.parametrize("drift", ["coherent-wrong-model", "wrong-effort"])
def test_native_recording_requires_fixed_model_and_effort_even_when_claims_are_coherent(
    tmp_path: Path, drift: str
) -> None:
    bundle, packet, prep = prepared(tmp_path)
    result = document(bundle, prep)
    if drift == "coherent-wrong-model":
        result["model"] = "other-backend-model"
    run = run_directory(tmp_path, prep, result)
    receipt_file = run / "native-run.json"
    receipt = json.loads(receipt_file.read_bytes())
    if drift == "coherent-wrong-model":
        receipt["model"] = result["model"]
    else:
        receipt["effort"] = "low"
    # The actual fixed-backend prompt remains byte-identical and hash-bound.
    assert (run / "prompt.txt").read_bytes() == prep["prompt"].encode()
    assert f"model={MODEL}\neffort={EFFORT}" in prep["prompt"]
    receipt_file.write_text(json.dumps(receipt))
    with pytest.raises(SkillEvalError, match="native invocation model or effort"):
        judge.record_agentic_calibration(bundle, packet, run, tmp_path / "records")
    assert not (tmp_path / "records").exists()


@pytest.mark.parametrize("mutation", ["swapped-audit", "size", "field"])
def test_compact_runtime_identity_cannot_borrow_coherently_swapped_audit(
    tmp_path: Path, mutation: str
) -> None:
    bundle, packet, prep = prepared(tmp_path)
    run = run_directory(tmp_path, prep, document(bundle, prep))
    audit = json.loads(attach_native_audit(run))
    receipt_file = run / "native-run.json"
    receipt = json.loads(receipt_file.read_bytes())
    if mutation == "swapped-audit":
        audit["identity"]["runtime"]["sha256"] = "b" * 64
        replacement = json.dumps(audit).encode()
        (run / "native-audit.json").write_bytes(replacement)
        receipt["audit"]["bytes"] = len(replacement)
        receipt["audit"]["sha256"] = hashlib.sha256(replacement).hexdigest()
    elif mutation == "size":
        receipt["identity"]["bytes"] += 1
    else:
        receipt["identity"]["audit_field"] = "unrelated-field"
    receipt_file.write_text(json.dumps(receipt))
    with pytest.raises(SkillEvalError, match="native audit runtime identity"):
        judge.record_agentic_calibration(bundle, packet, run, tmp_path / "records")
    assert not (tmp_path / "records").exists()


def test_completed_run_cannot_claim_incomplete_native_audit_metadata(tmp_path: Path) -> None:
    bundle, packet, prep = prepared(tmp_path)
    run = run_directory(tmp_path, prep, document(bundle, prep))
    audit = json.loads(attach_native_audit(run))
    audit["metadata_complete"] = False
    # Keep both the normal identity and outer audit reference coherent; a
    # completed invocation must not borrow the partial-only diagnostic escape.
    replacement = json.dumps(audit).encode()
    (run / "native-audit.json").write_bytes(replacement)
    receipt_file = run / "native-run.json"
    receipt = json.loads(receipt_file.read_bytes())
    receipt["audit"]["bytes"] = len(replacement)
    receipt["audit"]["sha256"] = hashlib.sha256(replacement).hexdigest()
    receipt_file.write_text(json.dumps(receipt))
    with pytest.raises(SkillEvalError, match="incomplete native audit"):
        judge.record_agentic_calibration(bundle, packet, run, tmp_path / "records")
    assert not (tmp_path / "records").exists()
