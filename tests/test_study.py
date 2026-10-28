"""Study contracts with the real local runner and synthetic child-process runtime."""

import json
from pathlib import Path
from typing import Any

import pytest
from test_runner import route as route

from skilleval import evidence, runner, study, treatments
from skilleval.errors import SkillEvalError


@pytest.fixture
def prepared(tmp_path: Path, route: dict[str, Any]) -> dict[str, Any]:
    source = tmp_path / "task"
    source.mkdir()
    (source / "scenario.json").write_text('{"fault":""}')
    (source / "code.py").write_text("raise RuntimeError('never run on host')\n")
    frozen = tmp_path / "input"
    task = evidence.snapshot(source, frozen)
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: study-control\ndescription: Inspect a review.\n---\nRead the change.\n"
    )
    captured = tmp_path / "skill-bundle"
    treatment = treatments.capture_skill(
        skill,
        None,
        captured,
        source_url="https://example.invalid/control",
        source_revision="authored-test-v1",
    )
    spec = study.StudySpec(
        study_id="control",
        tasks=(
            study.StudyTask(
                task_id="task",
                input_bundle=str(frozen),
                input_sha256=task["manifest_sha256"],
                prompt="Review the change.",
            ),
        ),
        skill_bundle=str(captured),
        skill_sha256=treatment["manifest_sha256"],
        runtime=str(route["runtime"]),
        image=str(route["image"]),
        image_sha256=route["image_sha256"],
        catalog=str(route["catalog"]),
        order_seed=42,
        max_attempts_per_cell=2,
    )
    plan = tmp_path / "plan.json"
    receipt = study.prepare_study(spec, plan)
    return {
        "plan_path": plan,
        "expected_sha256": receipt["sha256"],
        "output": tmp_path / "study",
        "auth_file": route["auth_file"],
    }


def test_frozen_matrix_has_exact_matched_cells_and_declared_budgets(
    prepared: dict[str, Any],
) -> None:
    plan = json.loads(prepared["plan_path"].read_text())
    cells = plan["assignments"]
    assert len(cells) == 2
    assert {c["arm"] for c in cells} == {"absent", "available"}
    assert len({c["pair_id"] for c in cells}) == 1
    assert len({c["cell_id"] for c in cells}) == 2
    assert study._matrix(study.StudySpec.model_validate_json(json.dumps(plan["spec"]))) == cells


def test_real_runner_pair_and_idempotent_resume(prepared: dict[str, Any]) -> None:
    result = study.run_study(**prepared, development=True)
    assert result["status"] == "complete"
    assert result["planned_cells"] == result["primary_completed_cells"] == 2
    assert result["pairs"][0]["both_primary_executions_completed"]
    assert result["pairs"][0]["task_quality_difference"] is None
    assert result["human_audit"] == "required-pending" and not result["scored_admitted"]
    assert all(len(c["attempts"]) == 1 for c in result["cells"])
    assert study.run_study(**prepared, development=True) == result


def test_scored_refusal_precedes_output_or_auth_access(prepared: dict[str, Any]) -> None:
    with pytest.raises(SkillEvalError, match="scored dispatch unavailable"):
        study.run_study(**prepared)
    assert not prepared["output"].exists()


def test_changed_plan_or_source_cannot_dispatch(prepared: dict[str, Any]) -> None:
    plan = json.loads(prepared["plan_path"].read_text())
    task = Path(plan["spec"]["tasks"][0]["input_bundle"]) / "code.py"
    task.chmod(0o600)
    task.write_text("changed input")
    with pytest.raises(SkillEvalError):
        study.run_study(**prepared, development=True)
    assert not list(prepared["output"].glob("attempts/*/*/dispatch.json"))


def test_retry_success_never_replaces_first_attempt(
    prepared: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = runner.run_native
    calls = 0

    def fail_once(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise SkillEvalError("injected pre-create failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(runner, "run_native", fail_once)
    result = study.run_study(**prepared, development=True)
    assert result["status"] == "complete" and calls == 3
    assert result["primary_completed_cells"] == 1
    assert len(result["cells"][0]["attempts"]) == 2
    assert result["cells"][0]["primary"] is None
    assert not result["pairs"][0]["both_primary_executions_completed"]


def test_interrupted_dispatch_requires_explicit_reconcile(
    prepared: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = runner.run_native

    def crash_after_native(*args: Any, **kwargs: Any) -> dict[str, Any]:
        original(*args, **kwargs)
        raise SystemExit("simulated abrupt coordinator exit")

    with monkeypatch.context() as patch:
        patch.setattr(runner, "run_native", crash_after_native)
        with pytest.raises(SystemExit):
            study.run_study(**prepared, development=True)
    with pytest.raises(SkillEvalError, match="unfinished attempt"):
        study.run_study(**prepared, development=True)
    args = {k: v for k, v in prepared.items() if k != "auth_file"}
    reconciled = study.reconcile_study(**args)
    assert reconciled["cells"][0]["primary"]["elapsed_seconds"] is None
    assert study.run_study(**prepared, development=True)["primary_completed_cells"] == 2


def test_second_controller_is_refused(prepared: dict[str, Any]) -> None:
    prepared["output"].mkdir()
    with study._lock(prepared["output"]), pytest.raises(SkillEvalError, match="another controller"):
        study.run_study(**prepared, development=True)


def test_keyboard_interrupt_after_receipt_keeps_report_readable(
    prepared: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = runner.run_native

    def interrupt_after_native(*args: Any, **kwargs: Any) -> dict[str, Any]:
        original(*args, **kwargs)
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(runner, "run_native", interrupt_after_native)
        result = study.run_study(**prepared, development=True)
    assert result["status"] == "partial"
    assert sum(len(cell["attempts"]) for cell in result["cells"]) == 1
    assert result["cells"][0]["attempts"][0]["error_kind"] == "controller-interrupted"
    assert result["cells"][0]["primary"]["status"] == "completed"
    assert study.run_study(**prepared, development=True)["primary_completed_cells"] == 2


def test_cancelled_native_run_stops_all_further_dispatch(
    prepared: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from skilleval.lifecycle import LifecycleInterruptedError

    required = runner._required

    def terminate(result: dict[str, Any], label: str) -> None:
        if label == "collect_artifacts_bounded":
            raise LifecycleInterruptedError("test SIGTERM unwind")
        required(result, label)

    monkeypatch.setattr(runner, "_required", terminate)
    result = study.run_study(**prepared, development=True)
    assert result["status"] == "partial"
    assert sum(len(cell["attempts"]) for cell in result["cells"]) == 1
    assert result["cells"][0]["attempts"][0]["error_kind"] == "controller-interrupted"
    assert result["cells"][0]["attempts"][0]["cleanup_complete"]


def test_symlink_parent_refusal_cannot_publish_a_plan(tmp_path: Path) -> None:
    protected = tmp_path / "protected"
    protected.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(protected, target_is_directory=True)
    with pytest.raises(OSError):
        study._new(alias / "plan.json", {"must": "not appear"})
    assert list(protected.iterdir()) == []


def test_failed_dispatch_publication_reserves_no_attempt(
    prepared: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise OSError("injected publication failure")

    with monkeypatch.context() as patch:
        patch.setattr(evidence, "_publish", fail)
        with pytest.raises(OSError):
            study.run_study(**prepared, development=True)
    assert not list(prepared["output"].glob("attempts/*/attempt-*"))
    assert study.run_study(**prepared, development=True)["primary_completed_cells"] == 2


def test_terminal_cleanup_failure_requires_recovery_without_rewriting_outcome(
    prepared: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from skilleval.lifecycle import Lifecycle, recover

    prepared = {**prepared, "output": prepared["output"].parent / "unused" / ".." / "study"}
    original = Lifecycle.cleanup

    def fail_cleanup(session: Lifecycle) -> dict[str, Any]:
        if session.ticket["create_attempted"]:
            (Path(session.ticket["runtime_state"]) / "scenario.json").write_text(
                '{"fault":"stop-failure"}'
            )
        return original(session)

    with monkeypatch.context() as patch:
        patch.setattr(Lifecycle, "cleanup", fail_cleanup)
        result = study.run_study(**prepared, development=True)
    path = Path(result["cells"][0]["attempts"][0]["path"])
    before = (path / "outcome.json").read_bytes()
    args = {key: value for key, value in prepared.items() if key != "auth_file"}
    with pytest.raises(SkillEvalError, match="recover the native VM"):
        study.reconcile_study(**args)
    (path / "native/runtime-state/scenario.json").write_text("{}")
    assert recover(path / "native")["cleanup_complete"]
    result = study.reconcile_study(**args)
    assert result["cells"][0]["attempts"][0]["cleanup_complete"]
    assert (path / "outcome.json").read_bytes() == before
    assert study.run_study(**prepared, development=True)["status"] == "complete"


def test_requested_admission_is_checked_before_native_dispatch(
    prepared: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = json.loads(prepared["plan_path"].read_text())
    spec = study.StudySpec.model_validate_json(
        json.dumps({**plan["spec"], "admission": "/missing-seal", "admission_sha256": "a" * 64})
    )
    new_plan = prepared["plan_path"].with_name("with-admission.json")
    receipt = study.prepare_study(spec, new_plan)
    called = []

    def refuse(path: Path, digest: str) -> dict[str, Any]:
        called.append((path, digest))
        raise SkillEvalError("expired admission test control")

    monkeypatch.setattr(study, "check_admission", refuse)
    with pytest.raises(SkillEvalError, match="expired admission"):
        study.run_study(
            **{**prepared, "plan_path": new_plan, "expected_sha256": receipt["sha256"]},
            development=True,
        )
    assert called == [(Path("/missing-seal"), "a" * 64)]
    assert not list(prepared["output"].glob("attempts/*/attempt-*"))
