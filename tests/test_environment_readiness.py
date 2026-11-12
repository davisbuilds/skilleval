"""Retained execution contracts qualify only their unchanged offline conditions.

Authored records and synthetic runtime inventories exercise replay without VMs,
imported task execution, inference, or host credential access.
"""

import copy
import itertools
import json
import shutil
from pathlib import Path

import pytest
from test_repair import task as task
from test_runner import route as route
from test_task_package import package_spec, solver_process

from skilleval import evidence, identity, repair, study, task_package, treatments
from skilleval.errors import SkillEvalError
from skilleval.identity import file_digest, object_digest


def publish_package(state, spec):
    destination = state["root"] / f"package-{next(state['sequence'])}.json"
    return task_package.prepare_package(spec, destination)


def record_validation(state, spec):
    original = publish_package(state, spec)
    output = state["root"] / f"validation-{next(state['sequence'])}"
    output.mkdir()
    captured = identity.snapshot_identity(str(state["route"]["runtime"]))
    rows = []
    for case in spec.validation:
        directory = output / case.case_id
        directory.mkdir()
        lanes = []
        for lane in case.lanes:
            process = solver_process(argv=list(lane.argv), stdout='{"ready":true}')
            compared = task_package.compare_lane(lane, process, spec.environment.profile)
            lanes.append(compared)
            (directory / f"lane-{lane.lane_id}.json").write_text(json.dumps(process))
        row = {
            "case_id": case.case_id,
            "role": case.role,
            "input_sha256": case.input_sha256,
            "identity": captured,
            "readiness_identity": identity.environment_identity(captured, spec.environment.profile),
            "image": {"sha256": spec.environment.image_sha256},
            "cleanup_complete": True,
            "lanes": lanes,
            "status": "matched" if all(x["status"] == "matched" for x in lanes) else "unexpected",
        }
        rows.append(row)
        (directory / "validation.json").write_text(json.dumps(row))
        (directory / "recovery.json").write_text(
            json.dumps(
                {
                    "kind": "skilleval-lifecycle-recovery",
                    "schema_version": 1,
                    "output": str(directory),
                    "phase": "recovered",
                    "cleanup_complete": True,
                    "runtime_home_removed": True,
                    "runtime_home": str(output / "removed-home"),
                    "create_attempted": True,
                    "observer_positive": True,
                    "observed_absent": True,
                    "identity": captured,
                    "image": row["image"],
                }
            )
        )
    contract = task_package.execution_contract(spec)
    report = {
        "schema_version": 2,
        "kind": "skilleval-task-validation",
        "package": original["path"],
        "package_sha256": original["sha256"],
        "execution_contract": contract,
        "execution_contract_sha256": object_digest(contract),
        "family": spec.family,
        "cases": rows,
        "status": "matched" if all(row["status"] == "matched" for row in rows) else "incomplete",
        "coverage_gaps": list(spec.environment.coverage_gaps),
        "limitations": list(spec.limitations),
        "inference_requests": 0,
        "scored_admitted": False,
    }
    report_path = output / "task-validation.json"
    report_path.write_text(json.dumps(report))
    return {"package": original, "spec": spec, "report": report_path, "value": report}


@pytest.fixture
def readiness(task, route, tmp_path, monkeypatch):
    # Hash isolated copies rather than modifying the active execution checkout.
    source_root = Path(identity.__file__).resolve().parents[2]
    copied = tmp_path / "control-source"
    for name in identity.SOURCES:
        target = copied / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_root / name, target)
    monkeypatch.setattr(identity, "__file__", str(copied / "src/skilleval/identity.py"))
    base = package_spec(task, route)
    lanes = tuple(
        task_package.ValidationLane(
            lane_id=f"probe-{number}",
            purpose="Authored execution observation",
            argv=("python3", "-c", f"print({number})"),
            parser="json",
            expected_exit_code=0,
            expected={"ready": True},
        )
        for number in (1, 2)
    )
    baseline = base.validation[0].model_copy(update={"lanes": lanes})
    reference = baseline.model_copy(update={"case_id": "reference", "role": "reference"})
    spec = base.model_copy(
        update={
            "environment": base.environment.model_copy(update={"profile": "solver-isolated-v1"}),
            "validation": (baseline, reference),
        }
    )
    state = {
        "root": tmp_path,
        "task": task,
        "route": route,
        "source": copied,
        "sequence": itertools.count(),
    }
    state["selected"] = record_validation(state, spec)
    return state


def checked(state, spec=None, selected=None):
    selected = selected or state["selected"]
    package = selected["package"] if spec is None else publish_package(state, spec)
    return task_package.checked_validation(
        Path(package["path"]),
        package["sha256"],
        selected["report"],
        file_digest(selected["report"]),
    )


def change_lanes(spec, **updates):
    return spec.model_copy(
        update={
            "validation": tuple(
                case.model_copy(
                    update={"lanes": tuple(lane.model_copy(update=updates) for lane in case.lanes)}
                )
                for case in spec.validation
            )
        }
    )


def test_selected_v2_receipt_replays_without_rewriting_original_evidence(readiness):
    selected = readiness["selected"]
    before = selected["report"].read_bytes()
    spec, value = checked(readiness)
    assert spec == selected["spec"]
    assert value == selected["value"]
    assert selected["report"].read_bytes() == before


@pytest.mark.parametrize("change", ["brief", "checker", "edit_scope", "prose"])
def test_grading_and_descriptive_changes_reuse_the_same_execution(readiness, change):
    selected = readiness["selected"]
    spec = selected["spec"]
    artifact = readiness["task"]["spec"]
    if change == "brief":
        artifact = artifact.model_copy(update={"prompt": "Implement the revised documented brief."})
    elif change == "checker":
        worker = readiness["root"] / "replacement-worker.py"
        worker.write_text("raise RuntimeError('replacement checker is VM-only')\n")
        artifact = artifact.model_copy(
            update={"worker": str(worker), "worker_sha256": file_digest(worker)}
        )
    elif change == "edit_scope":
        artifact = artifact.model_copy(update={"edit_scope": "repository", "editable_paths": ()})
    else:
        spec = spec.model_copy(
            update={
                "package_id": "renamed",
                "family": "document",
                "environment": spec.environment.model_copy(
                    update={
                        "setup": ("Reworded descriptive setup notes.",),
                        "coverage_gaps": ("An additional disclosed gap.",),
                        "required_paths": ("code.py",),
                    }
                ),
                "limitations": ("Updated task limitations.",),
            }
        )
        spec = change_lanes(spec, purpose="Reworded observation purpose")
    if change != "prose":
        task_path = readiness["root"] / "revised-task.json"
        prepared = repair.prepare_repair(artifact, task_path)
        spec = spec.model_copy(update={"task": str(task_path), "task_sha256": prepared["sha256"]})
        if change == "checker":
            Path(readiness["task"]["spec"].worker).unlink()
    selected_spec, retained = checked(readiness, spec)
    assert selected_spec == spec
    assert retained == selected["value"]
    assert retained["package_sha256"] == selected["package"]["sha256"]


def test_current_parser_and_expectations_replay_retained_processes(readiness):
    spec = readiness["selected"]["spec"]
    parsed = change_lanes(
        spec,
        parser="unittest",
        expected={"tests": 2, "failures": [], "errors": [], "skips": []},
    )
    assert checked(readiness, parsed)[0] == parsed
    with pytest.raises(SkillEvalError):
        checked(
            readiness,
            change_lanes(parsed, expected={"tests": 3, "failures": [], "errors": [], "skips": []}),
        )
    with pytest.raises(SkillEvalError):
        checked(readiness, change_lanes(spec, expected={"ready": False}))


def test_new_expectations_can_match_originally_unexpected_complete_observations(readiness):
    current = readiness["selected"]["spec"]
    original = change_lanes(current, expected={"ready": False})
    selected = record_validation(readiness, original)
    assert selected["value"]["status"] == "incomplete"
    assert all(row["status"] == "unexpected" for row in selected["value"]["cases"])
    _, retained = checked(readiness, current, selected)
    assert retained == selected["value"]
    assert retained["status"] == "incomplete"


@pytest.mark.parametrize(
    "change",
    [
        "input",
        "profile",
        "argv",
        "timeout",
        "image",
        "case_order",
        "case_id",
        "case_role",
        "lane_order",
        "lane_id",
    ],
)
def test_changed_executed_conditions_require_new_environment_evidence(readiness, change):
    spec = readiness["selected"]["spec"]
    assert checked(readiness)
    if change in {"argv", "timeout", "lane_id"}:
        update = {
            "argv": {"argv": ("python3", "-c", "print('changed')")},
            "timeout": {"timeout_seconds": 59},
            "lane_id": {"lane_id": "changed"},
        }[change]
        case = spec.validation[0]
        lane = case.lanes[0].model_copy(update=update)
        spec = spec.model_copy(
            update={
                "validation": (
                    case.model_copy(update={"lanes": (lane, case.lanes[1])}),
                    spec.validation[1],
                )
            }
        )
    elif change == "profile":
        spec = spec.model_copy(
            update={
                "environment": spec.environment.model_copy(update={"profile": "native-root-v1"})
            }
        )
    elif change == "image":
        # This is a real valid Docker-save archive with different pinned bytes.
        from test_qualification import docker_archive

        image, _digest = docker_archive(readiness["root"] / "new-image")
        # Fixture writer is deterministic; append tar padding to distinguish content.
        image.write_bytes(image.read_bytes() + b"\0" * 1024)
        spec = spec.model_copy(
            update={
                "environment": spec.environment.model_copy(
                    update={"image_archive": str(image), "image_sha256": file_digest(image)}
                )
            }
        )
    elif change == "case_order":
        spec = spec.model_copy(update={"validation": tuple(reversed(spec.validation))})
    elif change == "case_role":
        spec = spec.model_copy(
            update={
                "validation": tuple(
                    case.model_copy(
                        update={"role": "reference" if case.role == "baseline" else "baseline"}
                    )
                    for case in spec.validation
                )
            }
        )
    elif change == "case_id":
        spec = spec.model_copy(
            update={
                "validation": (
                    spec.validation[0],
                    spec.validation[1].model_copy(update={"case_id": "another-reference"}),
                )
            }
        )
    elif change == "lane_order":
        spec = spec.model_copy(
            update={
                "validation": tuple(
                    case.model_copy(update={"lanes": tuple(reversed(case.lanes))})
                    for case in spec.validation
                )
            }
        )
    else:
        source = readiness["root"] / "changed-reference-source"
        source.mkdir()
        (source / "code.py").write_text("changed = True\n")
        (source / "README.md").write_text("ordinary context\n")
        bundle = readiness["root"] / "changed-reference-input"
        captured = evidence.snapshot(source, bundle)
        reference = spec.validation[1].model_copy(
            update={"input_bundle": str(bundle), "input_sha256": captured["manifest_sha256"]}
        )
        spec = spec.model_copy(update={"validation": (spec.validation[0], reference)})
    with pytest.raises(SkillEvalError):
        checked(readiness, spec)


def rewrite_report(selected, value):
    selected["report"].write_text(json.dumps(value))
    for row in value["cases"]:
        directory = selected["report"].parent / row["case_id"]
        (directory / "validation.json").write_text(json.dumps(row))


@pytest.mark.parametrize(
    "change",
    [
        "missing_proof",
        "forged_proof",
        "missing_source",
        "contract",
        "origin",
        "raw_lane",
        "cleanup",
        "incomplete_execution",
    ],
)
def test_changed_original_evidence_cannot_be_reused(readiness, change):
    selected = readiness["selected"]
    assert checked(readiness)
    value = copy.deepcopy(selected["value"])
    row = value["cases"][0]
    directory = selected["report"].parent / row["case_id"]
    if change == "missing_proof":
        del row["readiness_identity"]
    elif change == "forged_proof":
        row["readiness_identity"]["runtime"] = {"changed": True}
    elif change == "missing_source":
        row["identity"]["sources"] = {}
        recovery = json.loads((directory / "recovery.json").read_text())
        recovery["identity"] = row["identity"]
        (directory / "recovery.json").write_text(json.dumps(recovery))
    elif change == "contract":
        value["execution_contract"]["image_sha256"] = "f" * 64
        value["execution_contract_sha256"] = object_digest(value["execution_contract"])
    elif change == "origin":
        value["package"] = str(readiness["root"] / "missing-original-package.json")
    elif change == "raw_lane":
        raw_path = directory / f"lane-{row['lanes'][0]['lane_id']}.json"
        raw = json.loads(raw_path.read_text())
        raw["stdout"] = '{"ready":false}'
        raw_path.write_text(json.dumps(raw))
    elif change == "cleanup":
        recovery = json.loads((directory / "recovery.json").read_text())
        recovery["observed_absent"] = False
        (directory / "recovery.json").write_text(json.dumps(recovery))
    else:
        case = selected["spec"].validation[0]
        process = row["lanes"][0]["process"]
        process["timed_out"] = True
        compared = task_package.compare_lane(
            case.lanes[0], process, selected["spec"].environment.profile
        )
        row["lanes"][0] = compared
        row["status"] = "incomplete"
        value["status"] = "incomplete"
        (directory / f"lane-{case.lanes[0].lane_id}.json").write_text(json.dumps(process))
    rewrite_report(selected, value)
    with pytest.raises(SkillEvalError):
        checked(readiness)


def study_spec(state, selected):
    skill = state["root"] / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: readiness-control\ndescription: Review work.\n---\nReview the work.\n"
    )
    skill_bundle = state["root"] / "skill-bundle"
    captured = treatments.capture_skill(
        skill,
        None,
        skill_bundle,
        source_url="https://example.invalid/control",
        source_revision="authored-v1",
    )
    artifact = state["task"]["spec"]
    return study.StudySpec(
        study_id="readiness-control",
        tasks=(
            study.StudyTask(
                mode="repair",
                task_id=artifact.task_id,
                input_bundle=artifact.input_bundle,
                input_sha256=artifact.input_sha256,
                prompt=artifact.prompt,
                package=selected["package"]["path"],
                package_sha256=selected["package"]["sha256"],
                environment_validation=str(selected["report"]),
                environment_validation_sha256=file_digest(selected["report"]),
            ),
        ),
        skill_bundle=str(skill_bundle),
        skill_sha256=captured["manifest_sha256"],
        runtime=str(state["route"]["runtime"]),
        image=str(state["route"]["image"]),
        image_sha256=state["route"]["image_sha256"],
        catalog=str(state["route"]["catalog"]),
        order_seed=23,
    )


def test_study_prepares_new_plan_after_unrelated_source_change_but_execution_stays_exact(readiness):
    spec = study_spec(readiness, readiness["selected"])
    first_path = readiness["root"] / "first-plan.json"
    study.prepare_study(spec, first_path)
    first = json.loads(first_path.read_text())
    accounting = readiness["source"] / "src/skilleval/accounting.py"
    accounting.write_bytes(accounting.read_bytes() + b"\n# unrelated reporting change\n")
    second_path = readiness["root"] / "second-plan.json"
    study.prepare_study(spec, second_path)
    second = json.loads(second_path.read_text())
    assert first["identity"] != second["identity"]
    assert (
        first["identity"]["sources"]["src/skilleval/accounting.py"]
        != second["identity"]["sources"]["src/skilleval/accounting.py"]
    )
    with pytest.raises(SkillEvalError, match="execution identity changed"):
        study._dispatch_check(first, spec)
    assert study._dispatch_check(second, spec) is None
    boundary = readiness["source"] / "src/skilleval/solver_boundary.py"
    original = boundary.read_bytes()
    boundary.write_bytes(original + b"\n# changed effective boundary\n")
    with pytest.raises(SkillEvalError):
        study.prepare_study(spec, readiness["root"] / "changed-boundary-plan.json")
    boundary.write_bytes(original)
    runtime = Path(spec.runtime)
    runtime.write_text(runtime.read_text() + "\n# changed runtime\n")
    with pytest.raises(SkillEvalError):
        study.prepare_study(spec, readiness["root"] / "changed-runtime-plan.json")


def test_schema_one_does_not_acquire_cross_package_or_scoped_source_compatibility(readiness):
    selected = readiness["selected"]
    value = copy.deepcopy(selected["value"])
    value["schema_version"] = 1
    for key in ("package", "execution_contract", "execution_contract_sha256"):
        del value[key]
    for row in value["cases"]:
        del row["readiness_identity"]
    rewrite_report(selected, value)
    assert checked(readiness)[1] == value
    with pytest.raises(SkillEvalError):
        checked(readiness, selected["spec"].model_copy(update={"limitations": ("changed",)}))
    spec = study_spec(readiness, selected)
    study.prepare_study(spec, readiness["root"] / "legacy-plan.json")
    accounting = readiness["source"] / "src/skilleval/accounting.py"
    accounting.write_bytes(accounting.read_bytes() + b"\n# unrelated change\n")
    with pytest.raises(SkillEvalError):
        study.prepare_study(spec, readiness["root"] / "legacy-changed-source-plan.json")


def test_study_keeps_original_failure_and_disclosures_distinct_from_new_selection(readiness):
    original = change_lanes(readiness["selected"]["spec"], expected={"ready": False})
    retained = record_validation(readiness, original)
    current = readiness["selected"]["spec"].model_copy(
        update={"limitations": ("New selection retains an additional limitation.",)}
    )
    selected = {
        "package": publish_package(readiness, current),
        "report": retained["report"],
        "spec": current,
    }
    spec = study_spec(readiness, selected)
    destination = readiness["root"] / "reinterpreted-plan.json"
    study.prepare_study(spec, destination)
    plan = json.loads(destination.read_text())
    selection = plan["environment_readiness"][spec.tasks[0].task_id]
    assert selection["original_package_sha256"] == retained["package"]["sha256"]
    assert selection["selected_package_sha256"] == selected["package"]["sha256"]
    assert selection["original_status"] == "incomplete"
    assert selection["selected_expectations_status"] == "matched"
    assert selection["original_limitations"] == list(original.limitations)
    assert selection["selected_limitations"] == list(current.limitations)
    assert study._dispatch_check(plan, spec) is None
    selection["original_status"] = "matched"
    with pytest.raises(SkillEvalError, match="readiness selection"):
        study._dispatch_check(plan, spec)
