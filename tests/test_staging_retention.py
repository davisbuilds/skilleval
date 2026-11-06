"""Staging retention operates on disposable real files, without runtime calls."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
from test_admission import evidence as evidence
from test_admission import seal
from test_repair import task as task
from test_repair_assessment import assessment as assessment
from test_retention import completed_run
from test_runner import invoke as invoke_native
from test_runner import route as route

from skilleval import admission, judge, lifecycle
from skilleval.repair_assessment import RepairAssessmentSpec, checked_report
from skilleval.retention import prune_staging, prune_staging_runs


def staging_run(root: Path, name: str) -> tuple[Path, Path, Path]:
    output, _, recovery = completed_run(root)
    ticket = json.loads(recovery.read_bytes())
    ticket["identity"] = {"runtime": {"binary": str(root / "smolvm")}}
    ticket["env"] = lifecycle._environment(output / "runtime-state", Path(ticket["runtime_home"]))
    ticket["machine_name"] = "workspace-test"
    ticket["steps"] = [
        {
            "label": label,
            "argv": [str(root / "smolvm"), "machine", "ls", "--json"],
            "returncode": 0,
            "stdout": json.dumps(names),
        }
        for label, names in (
            ("inventory_known_present", [{"name": "workspace-test"}]),
            ("recovery_inventory_after", []),
        )
    ]
    recovery.write_text(json.dumps(ticket))
    staging = output / "runtime-state" / name
    staging.write_bytes(b"disposable staged input")
    report = {
        "identity": ticket["identity"],
        "image": ticket["image"],
        "cleanup_complete": True,
        "schema_version": 1,
    }
    if name == "checker-input.tar":
        report["kind"] = "skilleval-repair-check"
        evidence = output / "repair-check.json"
    elif name == "validation-input.tar":
        report["case_id"] = output.name
        evidence = output / "validation.json"
        (output.parent / "task-validation.json").write_text(
            json.dumps(
                {"kind": "skilleval-task-validation", "schema_version": 1, "cases": [report]}
            )
        )
    else:
        report.update(
            kind="skilleval-native-run",
            tooling_sha256=hashlib.sha256(staging.read_bytes()).hexdigest(),
            recovery="recovery.json",
            status="completed",
        )
        evidence = output / "native-run.json"
    evidence.write_text(json.dumps(report))
    return output, staging, evidence


@pytest.mark.parametrize("name", ["checker-input.tar", "validation-input.tar", "tooling.tar"])
def test_staging_preview_apply_repeat_preserves_all_evidence(tmp_path: Path, name: str) -> None:
    output, staged, _ = staging_run(tmp_path, name)
    for path in (output / "artifacts.tar", output / "runtime-state/unknown.tar"):
        path.write_bytes(b"retain me")
    original = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file() and p != staged}
    preview = prune_staging(output)
    assert preview["status"] == "eligible", preview
    assert staged.is_file() and not (output / "staging-retention.json").exists()
    row = next(row for row in preview["files"] if row["path"] == "runtime-state/" + name)
    assert row["logical_bytes"] == staged.stat().st_size
    assert row["allocated_bytes"] == staged.stat().st_blocks * 512 > 0
    applied = prune_staging(output, apply=True)
    assert applied["status"] == "removed", applied
    assert not staged.exists()
    assert all(p.read_bytes() == raw for p, raw in original.items())
    receipt = (output / "staging-retention.json").read_bytes()
    assert prune_staging(output, apply=True)["status"] == "absent"
    assert (output / "staging-retention.json").read_bytes() == receipt


@pytest.mark.parametrize("name", ["checker-input.tar", "validation-input.tar", "tooling.tar"])
@pytest.mark.parametrize(
    "fault",
    [
        "active",
        "not_recovered",
        "home_present",
        "no_positive",
        "not_absent",
        "blind_positive",
        "blind_absence",
        "malformed_absence",
        "later_reappearance",
        "wrong_inventory_runtime",
        "wrong_namespace",
        "legacy",
        "malformed_ticket",
        "ticket_link",
        "producer_missing",
        "producer_link",
        "wrong_binding",
        "artifact_dependency",
        "staged_link",
        "staged_directory",
        "state_link",
    ],
)
def test_staging_refuses_unsafe_targets(tmp_path: Path, name: str, fault: str) -> None:
    output, staged, producer = staging_run(tmp_path, name)
    recovery = output / "recovery.json"
    ticket = json.loads(recovery.read_bytes())
    report = json.loads(producer.read_bytes())
    if fault == "active":
        ticket["cleanup_complete"] = False
    elif fault == "not_recovered":
        ticket["phase"] = "recovering"
    elif fault == "home_present":
        Path(ticket["runtime_home"]).mkdir()
    elif fault == "no_positive":
        ticket["observer_positive"] = False
    elif fault == "not_absent":
        ticket["observed_absent"] = False
    elif fault == "blind_positive":
        ticket["steps"][0]["stdout"] = "[]"
    elif fault == "blind_absence":
        ticket["steps"][-1]["output_truncated"] = True
    elif fault == "malformed_absence":
        ticket["steps"][-1]["stdout"] = []
    elif fault == "later_reappearance":
        ticket["steps"].append({**ticket["steps"][0], "label": "later_inventory"})
    elif fault == "wrong_inventory_runtime":
        ticket["steps"][-1]["argv"][0] = "/other/runtime"
    elif fault == "wrong_namespace":
        ticket["env"]["XDG_CONFIG_HOME"] = "/other/state/config"
    elif fault == "wrong_binding":
        report["identity"] = {"runtime": "some-other-source"}
    elif fault == "artifact_dependency":
        report["artifacts"] = {"runtime-state/" + name: "a" * 64}
    producer.write_text(json.dumps(report))
    recovery.write_text(json.dumps(ticket))
    if fault == "legacy":
        recovery.rename(output / "report.json")
    elif fault == "malformed_ticket":
        recovery.write_text("not JSON")
    elif fault in {"ticket_link", "producer_link"}:
        target = recovery if fault == "ticket_link" else producer
        saved = target.with_suffix(".saved")
        target.rename(saved)
        target.symlink_to(saved)
    elif fault == "producer_missing":
        producer.unlink()
    elif fault == "staged_link":
        saved = output / "canonical-input.tar"
        staged.rename(saved)
        staged.symlink_to(saved)
    elif fault == "staged_directory":
        staged.unlink()
        staged.mkdir()
    elif fault == "state_link":
        saved = output / "saved-state"
        staged.parent.rename(saved)
        staged.parent.symlink_to(saved, target_is_directory=True)
    original = staged.read_bytes() if staged.is_file() else None
    result = prune_staging(output, apply=True)
    assert result["status"] == "retained", result
    assert result.get("reason") or any(row.get("reason") for row in result["files"])
    assert staged.exists()
    if original is not None:
        assert staged.read_bytes() == original


def test_qualification_tooling_is_retained_even_beside_native_receipt(tmp_path: Path) -> None:
    output, staged, _ = staging_run(tmp_path, "tooling.tar")
    (output / "report.json").write_text(
        json.dumps({"artifacts": {"runtime-state/tooling.tar": "required-by-admission"}})
    )
    result = prune_staging(output, apply=True)
    assert result["status"] == "retained"
    assert "admission" in next(row["reason"] for row in result["files"] if row.get("reason"))
    assert staged.is_file()


def test_modified_native_tooling_is_retained(tmp_path: Path) -> None:
    output, staged, _ = staging_run(tmp_path, "tooling.tar")
    staged.write_bytes(b"modified diagnostics")
    result = prune_staging(output, apply=True)
    assert result["status"] == "retained"
    assert staged.read_bytes() == b"modified diagnostics"


@pytest.mark.parametrize("name", ["checker-input.tar", "validation-input.tar", "tooling.tar"])
def test_later_recovery_unlocks_failed_runs_without_rewriting_producer(
    tmp_path: Path, name: str
) -> None:
    output, staged, producer = staging_run(tmp_path, name)
    report = json.loads(producer.read_bytes())
    report["cleanup_complete"] = False
    producer.write_text(json.dumps(report))
    if name == "validation-input.tar":
        parent = output.parent / "task-validation.json"
        record = json.loads(parent.read_bytes())
        record["cases"] = [report]
        parent.write_text(json.dumps(record))
    recovery = output / "recovery.json"
    ticket = json.loads(recovery.read_bytes())
    ticket.update(cleanup_complete=False, phase="recovering")
    recovery.write_text(json.dumps(ticket))
    assert prune_staging(output, apply=True)["status"] == "retained"
    original = producer.read_bytes()
    ticket.update(cleanup_complete=True, phase="recovered")
    recovery.write_text(json.dumps(ticket))
    assert prune_staging(output, apply=True)["status"] == "removed"
    assert not staged.exists()
    assert producer.read_bytes() == original


@pytest.mark.parametrize(
    "fault", ["staging", "recovery", "producer", "home", "state", "qualification"]
)
def test_apply_rechecks_inputs_after_durable_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    output, staged, producer = staging_run(tmp_path, "tooling.tar")
    write = lifecycle._write
    mutated = False

    def change_after_intent(path: Path, value: dict[str, object]) -> None:
        nonlocal mutated
        write(path, value)
        if value["status"] != "removing" or mutated:
            return
        mutated = True
        if fault == "staging":
            staged.write_bytes(b"changed staging")
        elif fault in {"recovery", "producer"}:
            target = output / "recovery.json" if fault == "recovery" else producer
            target.write_bytes(target.read_bytes() + b"\n")
        elif fault == "home":
            ticket = json.loads((output / "recovery.json").read_bytes())
            Path(ticket["runtime_home"]).mkdir()
        elif fault == "state":
            staged.parent.rename(output / "moved-state")
            staged.parent.mkdir()
            staged.write_bytes(b"replacement staging")
        else:
            (output / "report.json").write_text("qualification")

    monkeypatch.setattr(lifecycle, "_write", change_after_intent)
    result = prune_staging(output, apply=True)
    assert mutated and result["status"] == "retained", result
    assert staged.is_file()
    if fault == "state":
        assert (output / "moved-state/tooling.tar").read_bytes() == b"disposable staged input"


def test_batch_partial_retry_and_hardlink_accounting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good, staged, _ = staging_run(tmp_path / "good", "tooling.tar")
    bad, retained, _ = staging_run(tmp_path / "bad", "checker-input.tar")
    canonical = tmp_path / "canonical.tar"
    os.link(staged, canonical)
    preview = prune_staging_runs([good, bad, good])
    assert len(preview["runs"]) == 2
    assert preview["eligible_allocated_bytes"] == retained.stat().st_blocks * 512
    unlink = os.unlink

    def fail_checker(name: str | Path, **kwargs: int) -> None:
        if name == "checker-input.tar":
            raise PermissionError("injected unlink failure")
        unlink(name, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "unlink", fail_checker)
        result = prune_staging_runs([good, bad], apply=True)
    assert result["status"] == "partial"
    assert [run["status"] for run in result["runs"]] == ["removed", "retained"]
    assert canonical.read_bytes() == b"disposable staged input"
    assert not staged.exists() and retained.exists()
    assert prune_staging_runs([good, bad], apply=True)["status"] == "complete"


@pytest.mark.parametrize("failed_cleanup", [False, True])
def test_native_producer_audit_and_artifact_reader_survive_staging_cleanup(
    tmp_path: Path, route: dict[str, Any], failed_cleanup: bool
) -> None:
    # This uses the normal native controller against the repository's synthetic
    # child-process runtime. It never starts a VM or calls a provider.
    report = invoke_native(tmp_path, route, "stop-failure" if failed_cleanup else "")
    output = tmp_path / "run"
    staged = output / "runtime-state/tooling.tar"
    before = judge._run_files(output, mode="review")
    if failed_cleanup:
        assert report["cleanup_complete"] is False
        assert prune_staging(output, apply=True)["status"] == "retained"
        (output / "runtime-state/scenario.json").write_text("{}")
        assert lifecycle.recover(output)["cleanup_complete"] is True
    else:
        assert report["cleanup_complete"] is True
    result = prune_staging(output, apply=True)
    assert result["status"] == "removed", result
    assert not staged.exists()
    assert judge._run_files(output, mode="review") == before


def test_precreate_recovery_needs_no_machine_observation(tmp_path: Path) -> None:
    output, staged, _ = staging_run(tmp_path, "checker-input.tar")
    recovery = output / "recovery.json"
    ticket = json.loads(recovery.read_bytes())
    ticket.update(create_attempted=False, observer_positive=False, observed_absent=False, steps=[])
    recovery.write_text(json.dumps(ticket))
    assert prune_staging(output, apply=True)["status"] == "removed"
    assert not staged.exists()


def test_qualification_admission_dependency_survives_cleanup(
    evidence: dict[str, Any],
) -> None:
    sealed = seal(evidence)
    native = evidence["native_path"].parent
    archive = native / "runtime-state/tooling.tar"
    original = archive.read_bytes()
    result = prune_staging(native, apply=True)
    assert result["status"] == "retained", result
    assert archive.read_bytes() == original
    assert admission.check_admission(evidence["output"], sealed["sha256"]) == sealed


def test_final_receipt_failure_reports_partial_after_actual_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, staged, _ = staging_run(tmp_path, "tooling.tar")
    write = lifecycle._write

    def fail_completion(path: Path, value: dict[str, Any]) -> None:
        if value["status"] == "removed":
            raise PermissionError("injected completion receipt failure")
        write(path, value)

    monkeypatch.setattr(lifecycle, "_write", fail_completion)
    result = prune_staging_runs([output], apply=True)
    assert result["status"] == "partial"
    assert result["runs"][0]["status"] == "removed"
    assert result["runs"][0]["receipt_error"]
    assert not staged.exists()
    assert json.loads((output / "staging-retention.json").read_bytes())["status"] == "removing"


def test_repair_check_reader_survives_checker_staging_cleanup(
    assessment: RepairAssessmentSpec,
) -> None:
    reference = assessment.checks[0]
    before = checked_report(reference)
    output = Path(reference.report).parent
    assert prune_staging(output, apply=True)["status"] == "removed"
    assert not (output / "runtime-state/checker-input.tar").exists()
    assert checked_report(reference) == before
