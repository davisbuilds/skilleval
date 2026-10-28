"""Explicit invocation changes only the declared arm; old plans keep their meaning."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from test_runner import route as route
from test_study import prepared as prepared

from skilleval import evidence, study
from skilleval.errors import SkillEvalError


def test_explicit_skill_request_reaches_actual_native_prompt(prepared: dict[str, Any]) -> None:
    plan = json.loads(prepared["plan_path"].read_text())
    result = study.run_study(**prepared, development=True)
    prompts = {
        cell["arm"]: (Path(cell["attempts"][0]["path"]) / "native/prompt.txt").read_text()
        for cell in result["cells"]
    }
    assert prompts["absent"] == plan["spec"]["tasks"][0]["prompt"]
    assert prompts["available"] == "Use $study-control for this task.\n\n" + prompts["absent"]
    assert plan["schema_version"] == 2
    assert plan["invocations"]["task"] == prompts
    assert result["invocation_policy"] == "explicit-use"
    assert result["cells"][0]["attempts"][0]["task_quality"] == "unassessed"


def test_legacy_plan_without_policy_is_still_natural_availability(
    prepared: dict[str, Any],
) -> None:
    path = prepared["plan_path"]
    plan = json.loads(path.read_text())
    plan["schema_version"] = 1
    for key in ("invocation_policy", "price_schedule"):
        plan["spec"].pop(key, None)
    for key in ("invocations", "skill_name"):
        plan.pop(key, None)
    raw = json.dumps(plan).encode()
    path.write_bytes(raw)
    prepared["expected_sha256"] = hashlib.sha256(raw).hexdigest()
    _, spec = study._plan(path, prepared["expected_sha256"])
    assert spec.invocation_policy == "natural-availability"
    result = study.run_study(**prepared, development=True)
    prompts = [
        (Path(cell["attempts"][0]["path"]) / "native/prompt.txt").read_text()
        for cell in result["cells"]
    ]
    assert prompts == ["Review the change.", "Review the change."]


def test_native_accounting_flows_into_paired_report(prepared: dict[str, Any]) -> None:
    path = prepared["plan_path"]
    plan = json.loads(path.read_text())
    source = path.parent / "task"
    (source / "scenario.json").write_text('{"fault":"","complete_usage":true}')
    frozen = path.parent / "full-usage-input"
    task = evidence.snapshot(source, frozen)
    plan["spec"]["tasks"][0].update(input_bundle=str(frozen), input_sha256=task["manifest_sha256"])
    schedule = json.loads(Path("examples/pricing/gpt-6.1-sol-standard-2026-10-28.json").read_text())
    plan["spec"]["price_schedule"] = schedule
    new_plan = path.parent / "priced-plan.json"
    receipt = study.prepare_study(
        study.StudySpec.model_validate_json(json.dumps(plan["spec"])), new_plan
    )
    prepared.update(plan_path=new_plan, expected_sha256=receipt["sha256"])
    result = study.run_study(**prepared, development=True)
    for cell in result["cells"]:
        attempt = cell["attempts"][0]
        assert attempt["usage_accounting"]["status"] == "complete"
        assert attempt["usage_accounting"]["provider_reconciliation"]["status"] == "matched"
        assert attempt["cost"]["estimated_api_equivalent_usd"] == "0.0000241"
        assert attempt["cost"]["cash_cost_usd"] is None
        assert attempt["timings"]["agent_seconds"] == 0.01
        assert attempt["timings"]["total_seconds"] > attempt["timings"]["agent_seconds"]
    pair = result["pairs"][0]["efficiency"]
    assert pair["token_deltas"]["total_tokens"] == 0
    assert pair["estimated_api_equivalent_usd_delta"] == "0"
    assert pair["task_quality_difference"] is None
    for arm in ("available", "absent"):
        ledger = result["efficiency_ledger"]["arms"][arm]
        assert ledger["tokens"]["total_tokens"]["total"] == 5
        assert ledger["api_equivalent_cost"]["total_usd"] == "0.0000241"
    assert study.report_study(new_plan, receipt["sha256"], prepared["output"]) == result


@pytest.mark.parametrize("mutation", ["missing-policy", "changed-prompt", "invalid-name"])
def test_invalid_frozen_invocation_cannot_dispatch(prepared: dict[str, Any], mutation: str) -> None:
    path = prepared["plan_path"]
    plan = json.loads(path.read_text())
    if mutation == "missing-policy":
        del plan["spec"]["invocation_policy"]
    elif mutation == "changed-prompt":
        plan["invocations"]["task"]["available"] += " Different task."
    else:
        plan["skill_name"] = 42
    raw = json.dumps(plan).encode()
    path.write_bytes(raw)
    prepared["expected_sha256"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(SkillEvalError):
        study.run_study(**prepared, development=True)
    assert not prepared["output"].exists()
