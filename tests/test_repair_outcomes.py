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
from skilleval.repair_assessment import RepairAssessmentSpec, prepare_assessment, record_assessment
from skilleval.repair_check import check_repair
from skilleval.repair_outcomes import attach_outcomes


def test_explicit_selection_binds_attempt_and_keeps_unassessed_arm(
    prepared, route, tmp_path, capsys
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
    native_judge = run_directory(tmp_path, packet, judgment(packet))
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
