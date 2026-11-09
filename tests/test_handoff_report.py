"""Replay actual study accounting and linked native files without inference."""

import json
from pathlib import Path

import pytest
from test_handoff import writer as writer
from test_handoff import writer_factory as writer_factory
from test_runner import route as route
from test_treatments import captured

from skilleval import cli, handoff, repair, runner, study
from skilleval.accounting import PriceSchedule
from skilleval.errors import SkillEvalError
from skilleval.identity import file_digest


@pytest.fixture
def linked(writer, tmp_path, request):
    skill, skill_sha = captured(tmp_path)
    schedule = PriceSchedule.model_validate_json(
        json.dumps(
            {
                "model": "gpt-6.1-sol",
                "source_url": "https://example.invalid/prices",
                "retrieved_on": "2026-11-08",
                "input_per_million": "1",
                "cached_input_per_million": "0.1",
                "output_per_million": "2",
            }
        )
    )
    task = writer["task"]
    route = writer["route"]
    fail_once = tmp_path / "fail-once"
    runtime = route["runtime"]
    runtime.write_text(
        runtime.read_text().replace(
            "  fault=scenario.get('fault')",
            "  fault=scenario.get('fault')\n"
            f"  fail_once=pathlib.Path({str(fail_once)!r})\n"
            "  if fail_once.exists():fault='timed-out';fail_once.unlink()",
        )
    )
    retry = getattr(request, "param", False)
    if retry:
        fail_once.touch()
    spec = study.StudySpec(
        study_id="linked",
        tasks=(
            study.StudyTask(
                mode="repair",
                task_id=task.task_id,
                input_bundle=task.input_bundle,
                input_sha256=task.input_sha256,
                prompt=task.prompt,
            ),
        ),
        skill_bundle=str(skill),
        skill_sha256=skill_sha,
        runtime=str(route["runtime"]),
        image=str(route["image"]),
        image_sha256=route["image_sha256"],
        catalog=str(route["catalog"]),
        order_seed=1,
        max_attempts_per_cell=2,
        price_schedule=schedule,
    )
    plan = tmp_path / "plan.json"
    prepared = study.prepare_study(spec, plan)
    output = tmp_path / "study"
    report = study.run_study(
        plan, prepared["sha256"], output, auth_file=route["auth_file"], development=True
    )
    cell = next(
        row
        for row in report["cells"]
        if (len(row["attempts"]) == 2 if retry else row["arm"] == "absent")
    )
    native = Path(cell["attempts"][-1]["path"]) / "native"
    result = tmp_path / "study-result"
    collected = repair.collect_repair(
        Path(writer["spec"].task), writer["spec"].task_sha256, native, result
    )
    selected = writer["spec"].model_copy(
        update={
            "result": str(result),
            "result_sha256": collected["sha256"],
        }
    )
    bundle = tmp_path / "handoff"
    frozen = handoff.prepare_handoff(selected, bundle)
    implementation = tmp_path / "implementation"
    runner.run_native(
        bundle / "input",
        frozen["input_digest"],
        frozen["prompt"],
        implementation,
        mode="repair",
        **route,
    )
    report_spec = handoff.HandoffReportSpec(
        handoff=str(bundle),
        handoff_sha256=frozen["sha256"],
        writer_study=handoff.WriterStudyReference(
            plan=str(plan),
            plan_sha256=prepared["sha256"],
            output=str(output),
            cell_id=cell["cell_id"],
        ),
        implementation_runs=(
            handoff.NativeRunReference(
                path=str(implementation), sha256=file_digest(implementation / "native-run.json")
            ),
        ),
        price_schedule=schedule,
    )
    return {
        "spec": report_spec,
        "writer": writer,
        "bundle": bundle,
        "frozen": frozen,
        "fail_once": fail_once,
    }


def test_linked_report_uses_verified_stage_ledgers_and_separate_judge_spend(linked, tmp_path):
    report = handoff.report_handoff(linked["spec"])
    assert report["writer"]["selected_attempt"] == report["writer"]["primary_attempt"] == 1
    assert report["writer"]["consumption"]["tokens"]["total_tokens"]["total"] == 5
    assert report["implementation"]["consumption"]["tokens"]["total_tokens"]["total"] == 5
    assert report["combined_agents"]["tokens"]["total_tokens"]["total"] == 10
    assert report["combined_agents"]["api_equivalent_cost"]["total_usd"] == "0.0000122"
    assert report["judge_overhead"]["consumption"]["attempt_count"] == 0
    assert report["implementation"]["primary_attempt"] is None
    assert report["semantic_acceptance"] == "unassessed"
    judge = tmp_path / "judge"
    frozen = linked["frozen"]
    runner.run_native(
        linked["bundle"] / "input",
        frozen["input_digest"],
        "Assess this feature.",
        judge,
        mode="judge",
        investigation=False,
        **linked["writer"]["route"],
    )
    spec = linked["spec"].model_copy(
        update={
            "judge_runs": (
                handoff.NativeRunReference(
                    path=str(judge), sha256=file_digest(judge / "native-run.json")
                ),
            )
        }
    )
    with_judge = handoff.report_handoff(spec)
    assert with_judge["combined_agents"] == report["combined_agents"]
    assert with_judge["judge_overhead"]["consumption"]["attempt_count"] == 1
    assert with_judge["judge_overhead"]["consumption"]["tokens"]["total_tokens"]["total"] == 5


def test_unfinished_attempt_keeps_unknown_spend_and_never_replaces_primary(linked, tmp_path):
    spec = linked["spec"].model_copy(
        update={
            "implementation_runs": (
                handoff.NativeRunReference(path=str(tmp_path / "interrupted")),
                *linked["spec"].implementation_runs,
            )
        }
    )
    report = handoff.report_handoff(spec)
    implementation = report["implementation"]
    assert [row["status"] for row in implementation["attempts"]] == ["unfinished", "completed"]
    assert implementation["primary_attempt"] is None
    assert implementation["consumption"]["tokens"]["total_tokens"]["total"] is None
    assert implementation["consumption"]["tokens"]["total_tokens"]["known_subtotal"] == 5
    assert report["combined_agents"]["api_equivalent_cost"]["total_usd"] is None
    assert report["combined_agents"]["api_equivalent_cost"]["known_attempt_count"] == 2


def test_report_rejects_unrelated_writer_cell_and_implementation_prompt(linked, tmp_path):
    spec = linked["spec"]
    plan = json.loads(Path(spec.writer_study.plan).read_text())
    other = next(row for row in plan["assignments"] if row["cell_id"] != spec.writer_study.cell_id)
    wrong = spec.model_copy(
        update={"writer_study": spec.writer_study.model_copy(update={"cell_id": other["cell_id"]})}
    )
    with pytest.raises(SkillEvalError, match="writer attempt is absent"):
        handoff.report_handoff(wrong)
    wrong_run = tmp_path / "wrong-implementation"
    frozen = linked["frozen"]
    runner.run_native(
        linked["bundle"] / "input",
        frozen["input_digest"],
        "Different request.",
        wrong_run,
        mode="repair",
        **linked["writer"]["route"],
    )
    wrong = spec.model_copy(
        update={
            "implementation_runs": (
                handoff.NativeRunReference(
                    path=str(wrong_run), sha256=file_digest(wrong_run / "native-run.json")
                ),
            )
        }
    )
    with pytest.raises(SkillEvalError, match="input, brief or limits"):
        handoff.report_handoff(wrong)


def test_report_cli_replays_files_and_rejects_duplicate_or_unpinned_receipts(
    linked, tmp_path, capsys
):
    selected = tmp_path / "report-spec.json"
    selected.write_text(linked["spec"].model_dump_json())
    assert cli.main(["schema", "handoff-report-spec"]) == 0
    assert "writer_study" in json.loads(capsys.readouterr().out)["properties"]
    assert cli.main(["handoff", "report", str(selected)]) == 0
    assert json.loads(capsys.readouterr().out)["combined_agents"]["attempt_count"] == 2
    values = linked["spec"].model_dump(mode="json")
    values["implementation_runs"] *= 2
    with pytest.raises(ValueError, match="each native run only once"):
        handoff.HandoffReportSpec.model_validate_json(json.dumps(values))
    unpinned = linked["spec"].model_copy(
        update={
            "implementation_runs": (
                handoff.NativeRunReference(path=linked["spec"].implementation_runs[0].path),
            )
        }
    )
    with pytest.raises(SkillEvalError, match="retained digest"):
        handoff.report_handoff(unpinned)


def test_different_price_schedules_are_never_pooled(linked):
    from decimal import Decimal

    selected = linked["spec"].model_copy(
        update={
            "price_schedule": linked["spec"].price_schedule.model_copy(
                update={"input_per_million": Decimal("2")}
            )
        }
    )
    report = handoff.report_handoff(selected)
    assert report["combined_agents"]["tokens"]["total_tokens"]["total"] == 10
    costs = report["combined_agents"]["api_equivalent_cost"]
    assert len(costs["schedule_groups"]) == 2
    assert costs["total_usd"] is None


def test_failed_implementation_stays_in_the_operational_ledger(linked, tmp_path):
    linked["fail_once"].touch()
    failed = tmp_path / "failed-implementation"
    frozen = linked["frozen"]
    result = runner.run_native(
        linked["bundle"] / "input",
        frozen["input_digest"],
        frozen["prompt"],
        failed,
        mode="repair",
        **linked["writer"]["route"],
    )
    assert result["status"] == "timed-out"
    spec = linked["spec"].model_copy(
        update={
            "implementation_runs": (
                handoff.NativeRunReference(
                    path=str(failed), sha256=file_digest(failed / "native-run.json")
                ),
                *linked["spec"].implementation_runs,
            )
        }
    )
    report = handoff.report_handoff(spec)
    assert [row["status"] for row in report["implementation"]["attempts"]] == [
        "timed-out",
        "completed",
    ]
    assert report["implementation"]["consumption"]["tokens"]["total_tokens"]["total"] is None
    assert report["implementation"]["consumption"]["status_counts"] == {
        "timed-out": 1,
        "completed": 1,
    }
    assert report["combined_agents"]["attempt_count"] == 3


@pytest.mark.parametrize("linked", [True], indirect=True)
def test_selected_writer_retry_never_replaces_the_first_attempt(linked):
    report = handoff.report_handoff(linked["spec"])
    writer = report["writer"]
    assert writer["selected_attempt"] == 2
    assert writer["primary_attempt"] == 1
    assert writer["primary_status"] == "timed-out"
    assert [row["status"] for row in writer["attempts"]] == ["timed-out", "completed"]
    assert writer["consumption"]["tokens"]["total_tokens"]["total"] is None


def test_invalid_implementation_artifacts_remain_visible_with_unknown_usable_usage(linked):
    run = Path(linked["spec"].implementation_runs[0].path)
    (run / "artifacts.tar").write_bytes(b"invalid retained archive")
    report = handoff.report_handoff(linked["spec"])
    row = report["implementation"]["attempts"][0]
    assert row["status"] == "completed"
    assert row["evidence_status"] == "invalid-artifacts"
    assert row["artifacts_verified"] is False
    assert row["usage_accounting"] is None
    assert row["reported_usage_accounting"]["total_tokens"] == 5
    assert report["combined_agents"]["tokens"]["total_tokens"]["total"] is None
    assert report["combined_agents"]["api_equivalent_cost"]["total_usd"] is None


def test_writer_study_task_identity_must_match_handoff_lineage(linked, tmp_path):
    original = handoff.load_handoff(linked["bundle"], linked["spec"].handoff_sha256)
    source = handoff.SpecHandoffSpec.model_validate_json(json.dumps(original["source_spec"]))
    task = repair.load_task(Path(source.task), source.task_sha256)
    renamed = task.model_copy(update={"task_id": "different-task-identity"})
    task_file = tmp_path / "different-task.json"
    prepared_task = repair.prepare_repair(renamed, task_file)
    record = json.loads((Path(source.result) / "repair-result.json").read_text())
    result_dir = tmp_path / "different-task-result"
    result = repair.collect_repair(
        task_file, prepared_task["sha256"], Path(record["provenance"]["native_run"]), result_dir
    )
    selected = source.model_copy(
        update={
            "task": str(task_file),
            "task_sha256": prepared_task["sha256"],
            "result": str(result_dir),
            "result_sha256": result["sha256"],
        }
    )
    bundle = tmp_path / "different-task-handoff"
    prepared = handoff.prepare_handoff(selected, bundle)
    report_spec = linked["spec"].model_copy(
        update={
            "handoff": str(bundle),
            "handoff_sha256": prepared["sha256"],
        }
    )
    with pytest.raises(SkillEvalError, match="writer study differs"):
        handoff.report_handoff(report_spec)


def test_supported_native_metadata_overflow_preserves_unverified_unknown_attempt(
    linked, tmp_path, monkeypatch
):
    # Use the actual producer's supported reduced-receipt path. No model or VM
    # runs: the authored native subprocess fixture supplies representative bytes.
    monkeypatch.setattr(runner, "MAX_NATIVE_AUDIT_BYTES", 1)
    frozen = linked["frozen"]
    run = tmp_path / "metadata-overflow"
    receipt = runner.run_native(
        linked["bundle"] / "input",
        frozen["input_digest"],
        frozen["prompt"],
        run,
        mode="repair",
        **linked["writer"]["route"],
    )
    assert receipt["status"] == "partial"
    assert "native_timeout_seconds" not in receipt
    assert "skill_treatment" not in receipt
    reference = handoff.NativeRunReference(
        path=str(run), sha256=file_digest(run / "native-run.json")
    )
    spec = linked["spec"].model_copy(
        update={
            "implementation_runs": (
                reference,
                *linked["spec"].implementation_runs,
            )
        }
    )
    report = handoff.report_handoff(spec)
    row = report["implementation"]["attempts"][0]
    assert row["status"] == "partial"
    assert row["binding"] == "unverified-incomplete-native-metadata"
    assert row["evidence_status"] == "incomplete-native-metadata"
    assert row["usage_accounting"] is None
    assert row["reported_usage_accounting"] is None
    assert row["audit_overflow"] == receipt["audit_overflow"]
    assert row["cost"]["status"] == "unknown"
    assert report["combined_agents"]["tokens"]["total_tokens"]["total"] is None
    assert report["combined_agents"]["api_equivalent_cost"]["total_usd"] is None
    with pytest.raises(SkillEvalError):
        handoff.check_implementation(linked["bundle"], frozen["sha256"], run)
