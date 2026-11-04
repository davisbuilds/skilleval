"""Exercise study -> repair -> judge -> explicitly selected report together."""

import copy
import json
from pathlib import Path

import pytest
from test_judge import run_directory
from test_repair_assessment import judgment
from test_runner import route as route
from test_study import prepared as prepared

from skilleval import cli, repair, study
from skilleval.errors import SkillEvalError
from skilleval.identity import file_digest
from skilleval.repair_assessment import (
    RepairAssessmentSpec,
    prepare_assessment,
    prepare_citation_correction,
    record_assessment,
)
from skilleval.repair_check import check_repair
from skilleval.repair_outcomes import _judge_consumption, attach_outcomes, render_review


def _judge_receipt(directory: Path, *, tokens: int | None, seconds: float, requests: int) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    native = {
        "status": "completed",
        "usage_accounting": (
            {
                "status": "complete",
                "input_tokens": tokens,
                "cached_input_tokens": 0,
                "uncached_input_tokens": tokens,
                "output_tokens": 5,
                "total_tokens": tokens + 5,
            }
            if tokens is not None
            else {"status": "unknown", "input_tokens": 99}
        ),
        "timings": {"agent_seconds": seconds},
        "request_count": requests,
    }
    (directory / "native-run.json").write_text(json.dumps(native))
    return native


def test_selected_judge_consumption_retains_both_linked_invocations(tmp_path):
    original = _judge_receipt(tmp_path, tokens=100, seconds=4, requests=3)
    corrected = _judge_receipt(tmp_path / "correction/run", tokens=20, seconds=2, requests=1)
    _judge_receipt(tmp_path / "unselected-repeat", tokens=1000, seconds=40, requests=30)
    consumption = _judge_consumption(tmp_path, {"citation_correction": {"schema_version": 1}})
    assert consumption["scope"] == "linked-selected-judge-invocations"
    assert consumption["attempt_count"] == 2
    assert consumption["usage_accounting"] == {
        "status": "complete",
        "input_tokens": 120,
        "cached_input_tokens": 0,
        "uncached_input_tokens": 120,
        "output_tokens": 10,
        "total_tokens": 130,
    }
    assert consumption["timings"]["agent_seconds"] == 6
    assert consumption["timings"]["total_seconds"] is None
    assert consumption["request_count"] == 4
    assert consumption["summary"]["tokens"]["total_tokens"] == {
        "known_subtotal": 130,
        "total": 130,
        "known_attempt_count": 2,
        "unknown_attempt_count": 0,
    }
    for attempt, native in zip(consumption["attempts"], (original, corrected), strict=True):
        assert attempt["usage_accounting"] == native["usage_accounting"]
        assert attempt["timings"] == native["timings"]
        assert attempt["request_count"] == native["request_count"]


def test_selected_judge_consumption_keeps_missing_usage_unknown(tmp_path):
    _judge_receipt(tmp_path, tokens=100, seconds=4, requests=3)
    _judge_receipt(tmp_path / "correction/run", tokens=None, seconds=2, requests=1)
    consumption = _judge_consumption(tmp_path, {"citation_correction": {"schema_version": 1}})
    assert consumption["usage_accounting"]["status"] == "unknown"
    assert consumption["usage_accounting"]["input_tokens"] is None
    assert consumption["summary"]["tokens"]["input_tokens"] == {
        "known_subtotal": 100,
        "total": None,
        "known_attempt_count": 1,
        "unknown_attempt_count": 1,
    }
    assert consumption["timings"]["agent_seconds"] == 6
    assert consumption["request_count"] == 4


def test_legacy_selected_judge_consumption_is_unchanged(tmp_path):
    native = _judge_receipt(tmp_path, tokens=100, seconds=4, requests=3)
    _judge_receipt(tmp_path / "correction/run", tokens=20, seconds=2, requests=1)
    expected = {key: native[key] for key in ("usage_accounting", "timings", "request_count")}
    assert json.dumps(_judge_consumption(tmp_path, {})) == json.dumps(expected)


@pytest.mark.parametrize("correction_tokens,total", [(20, "120"), (None, "unknown")])
def test_review_shows_linked_judge_attempts_and_complete_or_unknown_total(
    tmp_path, correction_tokens, total
):
    _judge_receipt(tmp_path, tokens=100, seconds=4, requests=3)
    _judge_receipt(tmp_path / "correction/run", tokens=correction_tokens, seconds=2, requests=1)
    consumption = _judge_consumption(tmp_path, {"citation_correction": {"schema_version": 1}})
    review = render_review(
        {
            "cells": [
                {
                    "task_id": "repair",
                    "arm": "absent",
                    "attempts": [
                        {
                            "path": str(tmp_path),
                            "repair_outcome": {
                                "artifact_path": str(tmp_path / "artifact"),
                                "checks": [],
                                "semantic": {"status": "satisfied-in-reviewed-scope"},
                                "judge_consumption": consumption,
                            },
                        }
                    ],
                }
            ]
        }
    )
    assert "2 linked invocations (original and citation correction)" in review
    assert "[original]" in review and "[citation-correction]" in review
    assert f"Input {total};" in review
    assert "Agent seconds 6; requests 4." in review
    if correction_tokens is None:
        assert "input_tokens known subtotal 100; 1 known, 1 unknown invocations." in review


@pytest.mark.parametrize("corrected", [False, True])
def test_explicit_selection_binds_attempt_and_keeps_unassessed_arm(
    prepared, route, tmp_path, capsys, corrected
):
    plan = json.loads(prepared["plan_path"].read_text())
    plan["spec"]["tasks"][0].update(mode="repair", prompt="Repair the code.")
    target = tmp_path / "repair-plan.json"
    prep = study.prepare_study(
        study.StudySpec.model_validate_json(json.dumps(plan["spec"])), target
    )
    prepared.update(plan_path=target, expected_sha256=prep["sha256"])
    report = study.run_study(**prepared, development=True)
    task_data = plan["spec"]["tasks"][0]
    worker = tmp_path / "worker.py"
    worker.write_text('raise RuntimeError("VM-only worker")\n')
    contract = tmp_path / "repair-task.json"
    task = repair.prepare_repair(
        repair.RepairSpec(
            task_id="repair",
            input_bundle=task_data["input_bundle"],
            input_sha256=task_data["input_sha256"],
            prompt=task_data["prompt"],
            editable_paths=("code.py", "investigation/probe.txt"),
            worker=str(worker),
            worker_sha256=file_digest(worker),
            probes=(
                repair.RepairProbe(probe_id="control", request={"value": False}, expected=False),
            ),
            source_url="https://example.invalid",
            source_revision="authored",
        ),
        contract,
    )
    cell = report["cells"][0]
    attempt = Path(cell["attempts"][0]["path"])
    collected = repair.collect_repair(
        contract, task["sha256"], attempt / "native", tmp_path / "repair-result"
    )
    check_repair(
        contract,
        task["sha256"],
        Path(collected["path"]),
        collected["sha256"],
        tmp_path / "check",
        runtime=route["runtime"],
        image=route["image"],
        image_sha256=route["image_sha256"],
    )
    ref = {
        "task": str(contract),
        "task_sha256": task["sha256"],
        "result": collected["path"],
        "result_sha256": collected["sha256"],
    }
    check = ref | {
        "report": str(tmp_path / "check/repair-check.json"),
        "report_sha256": file_digest(tmp_path / "check/repair-check.json"),
    }
    spec = RepairAssessmentSpec.model_validate_json(json.dumps(ref | {"checks": [check]}))
    packet = prepare_assessment(spec, tmp_path / "packet")
    value = judgment(packet)
    if corrected:
        value["judgments"][0]["citations"][0]["line_end"] = 2
    native_judge = run_directory(tmp_path, packet, value)
    if corrected:
        correction = prepare_citation_correction(
            spec, tmp_path / "packet", native_judge, tmp_path / "correction-packet"
        )
        fixed = copy.deepcopy(value)
        fixed["judgments"][0]["citations"][0]["line_end"] = 1
        correction_run = run_directory(tmp_path, correction, fixed, run_name="correction-run")
        receipt_path = correction_run / "native-run.json"
        receipt = json.loads(receipt_path.read_text())
        receipt.update(
            native_timeout_seconds=180,
            gateway_limits={"max_requests": 4},
            request_count=2,
            request_budget={"admitted_limit": 4, "admitted": 2},
        )
        receipt_path.write_text(json.dumps(receipt))
        saved = record_assessment(
            spec,
            tmp_path / "packet",
            native_judge,
            tmp_path / "assessments",
            correction_packet=tmp_path / "correction-packet",
            correction_run=correction_run,
        )
    else:
        saved = record_assessment(spec, tmp_path / "packet", native_judge, tmp_path / "assessments")
    selection = {
        "study_plan_sha256": prep["sha256"],
        "entries": [
            {
                "cell_id": cell["cell_id"],
                "attempt": 1,
                "checks": [check],
                "assessment": {"path": saved["path"], "sha256": saved["sha256"]},
            }
        ],
    }
    selection_path = tmp_path / "selection.json"
    selection_path.write_text(json.dumps(selection))
    digest = file_digest(selection_path)
    linked = attach_outcomes(copy.deepcopy(report), prep["sha256"], selection_path, digest)
    assert (
        linked["cells"][0]["primary_repair_outcome"]["semantic"]["status"]
        == "satisfied-in-reviewed-scope"
    )
    assert linked["cells"][1]["primary_repair_outcome"] is None
    consumption = linked["cells"][0]["primary_repair_outcome"]["judge_consumption"]
    if corrected:
        assert consumption["attempt_count"] == 2
        assert consumption["request_count"] is None
        assert consumption["summary"]["request_count"] == {
            "known_subtotal": 2,
            "total": None,
            "known_attempt_count": 1,
            "unknown_attempt_count": 1,
        }
        assert consumption["attempts"][1]["native_run"] == str(
            Path(saved["path"]) / "correction/run/native-run.json"
        )
    else:
        assert consumption == {"usage_accounting": None, "timings": None, "request_count": None}
    assert linked["efficiency_ledger"] == report["efficiency_ledger"]
    assert linked["pairs"][0]["task_quality_difference"] is None
    assert (
        linked["cells"][0]["attempts"][0]["status"] == report["cells"][0]["attempts"][0]["status"]
    )
    assert study.report_study(target, prep["sha256"], prepared["output"]) == report
    assert (
        cli.main(
            [
                "study",
                "report",
                str(target),
                "--sha256",
                prep["sha256"],
                "--output",
                str(prepared["output"]),
                "--outcomes",
                str(selection_path),
                "--outcomes-sha256",
                digest,
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["repair_selection"]["selected_attempts"] == 1
    selection["entries"][0]["cell_id"] = report["cells"][1]["cell_id"]
    selection_path.write_text(json.dumps(selection))
    with pytest.raises(SkillEvalError, match="another attempt"):
        attach_outcomes(
            copy.deepcopy(report), prep["sha256"], selection_path, file_digest(selection_path)
        )
