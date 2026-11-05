"""Acceptance maps bind operator dispatch without changing native inputs or grading."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from test_acceptance import mapped as mapped
from test_repair import task as task
from test_runner import route as route
from test_study import prepared as prepared

from skilleval import acceptance, runner, study, task_package, treatments
from skilleval.errors import SkillEvalError


def specification(**updates: Any) -> study.StudySpec:
    data = {
        "study_id": "mapped-control",
        "tasks": [
            {
                "task_id": "task",
                "input_bundle": "input",
                "input_sha256": "a" * 64,
                "prompt": "Repair the ordinary defect.",
            }
        ],
        "skill_bundle": "skill",
        "skill_sha256": "b" * 64,
        "runtime": "runtime",
        "image": "image",
        "image_sha256": "c" * 64,
        "catalog": "catalog",
        "order_seed": 42,
    }
    data.update(updates)
    return study.StudySpec.model_validate_json(json.dumps(data))


def test_mapped_policy_requires_packaged_mapped_repair_tasks() -> None:
    with pytest.raises(ValidationError, match="mapped acceptance"):
        specification(acceptance_policy="mapped-acceptance-v1")


@pytest.mark.parametrize("field", ["acceptance_map", "acceptance_map_sha256"])
def test_map_reference_requires_both_path_and_digest(field: str) -> None:
    original = specification().tasks[0].model_dump(mode="json")
    original[field] = "map.json" if field == "acceptance_map" else "d" * 64
    with pytest.raises(ValidationError, match="acceptance map requires"):
        specification(tasks=[original])


@pytest.mark.parametrize("missing", ["policy", "package", "mode"])
def test_maps_require_explicit_policy_and_packaged_repair_contract(missing: str) -> None:
    original = specification().tasks[0].model_dump(mode="json")
    original.update(
        mode="repair",
        acceptance_map="map.json",
        acceptance_map_sha256="d" * 64,
        package="package.json",
        package_sha256="e" * 64,
        environment_validation="validation.json",
        environment_validation_sha256="f" * 64,
    )
    policy = "mapped-acceptance-v1"
    if missing == "policy":
        policy = "legacy-unmapped"
    elif missing == "mode":
        original["mode"] = "review"
    else:
        for key in (
            "package",
            "package_sha256",
            "environment_validation",
            "environment_validation_sha256",
        ):
            original[key] = None
    with pytest.raises(ValidationError, match="mapped acceptance"):
        specification(tasks=[original], acceptance_policy=policy)


def test_absent_map_fields_and_legacy_default_do_not_change_spec_serialization() -> None:
    dumped = specification().model_dump(mode="json")
    assert "acceptance_policy" not in dumped
    assert "acceptance_map" not in dumped["tasks"][0]
    assert "acceptance_map_sha256" not in dumped["tasks"][0]


@pytest.fixture
def mapped_study(mapped, task, route, tmp_path, monkeypatch, request):
    """Real task/package/map bytes; validation-receipt integrity is covered separately."""
    package = task_package.load_package(Path(mapped["package"]), mapped["package_sha256"])
    package = package.model_copy(
        update={
            "environment": package.environment.model_copy(update={"profile": "solver-isolated-v1"}),
        }
    )
    if getattr(request, "param", "repair") == "document":
        package = package.model_copy(update={"family": "document"})
        mapped["requirements"][0]["semantic"][0]["criterion"] = "factual-support"
    packaged = task_package.prepare_package(package, tmp_path / "solver-package.json")
    marker = "PRIVATE-ACCEPTANCE-MARKER-471ac"
    mapped.update(package=packaged["path"], package_sha256=packaged["sha256"], purpose=marker)
    saved = acceptance.prepare_acceptance(
        acceptance.AcceptanceMapSpec.model_validate_json(json.dumps(mapped)), tmp_path / "map.json"
    )
    monkeypatch.setattr(
        task_package,
        "checked_validation",
        lambda path, digest, *_: (
            task_package.load_package(path, digest),
            {"cases": [{"identity": study.snapshot_identity(route["runtime"])}]},
        ),
    )
    skill = tmp_path / "skill-source"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: mapped-control\ndescription: Repair the ordinary defect.\n"
        "---\nInspect the code.\n"
    )
    captured = tmp_path / "skill-bundle"
    captured_skill = treatments.capture_skill(
        skill, None, captured, source_url="https://example.invalid/authored", source_revision="v1"
    )
    artifact = task["spec"]
    selected = study.StudyTask(
        mode="repair",
        task_id=artifact.task_id,
        input_bundle=artifact.input_bundle,
        input_sha256=artifact.input_sha256,
        prompt=artifact.prompt,
        package=packaged["path"],
        package_sha256=packaged["sha256"],
        environment_validation=str(tmp_path / "validation.json"),
        environment_validation_sha256="e" * 64,
        acceptance_map=saved["path"],
        acceptance_map_sha256=saved["sha256"],
    )
    spec = study.StudySpec(
        study_id="mapped-study",
        tasks=(selected,),
        skill_bundle=str(captured),
        skill_sha256=captured_skill["manifest_sha256"],
        runtime=str(route["runtime"]),
        image=str(route["image"]),
        image_sha256=route["image_sha256"],
        catalog=str(route["catalog"]),
        order_seed=42,
        acceptance_policy="mapped-acceptance-v1",
    )
    plan = tmp_path / "plan.json"
    prepared = study.prepare_study(spec, plan)
    return {
        "args": {
            "plan_path": plan,
            "expected_sha256": prepared["sha256"],
            "output": tmp_path / "study",
            "auth_file": route["auth_file"],
        },
        "spec": spec,
        "map": saved,
        "marker": marker,
    }


@pytest.mark.parametrize("mapped_study", ["repair", "document"], indirect=True)
def test_mapped_plan_and_operator_refs_do_not_reach_actual_native_inputs(mapped_study, monkeypatch):
    args = mapped_study["args"]
    plan = json.loads(args["plan_path"].read_text())
    assert plan["schema_version"] == 4
    assert plan["acceptance_policy"] == "mapped-acceptance-v1"
    assert plan["spec"]["tasks"][0]["acceptance_map"] == mapped_study["map"]["path"]
    original = runner.run_native
    calls = []

    def observed(*positional, **keywords):
        calls.append((positional, keywords))
        return original(*positional, **keywords)

    monkeypatch.setattr(runner, "run_native", observed)
    result = study.run_study(**args, development=True)
    assert result["primary_completed_cells"] == 2
    assert result["acceptance_policy"] == "mapped-acceptance-v1"
    assert result["acceptance_maps"] == [
        {
            "task_id": "authored",
            "acceptance_map": mapped_study["map"]["path"],
            "acceptance_map_sha256": mapped_study["map"]["sha256"],
            "semantic_validity": "unassessed",
        }
    ]
    assert result["human_audit"] == "required-pending" and not result["scored_admitted"]
    assert result["pairs"][0]["task_quality_difference"] is None
    assert len(calls) == 2
    for positional, keywords in calls:
        # Known-positive normal content establishes the inspected boundary.
        assert "Repair the defect." in positional[2]
        assert mapped_study["marker"] not in positional[2]
        assert "acceptance" not in str(keywords)
        assert "map.json" not in str(positional)
        assert "code.py" in {p.name for p in positional[0].rglob("*")}
        for root in (positional[0], keywords["skill_bundle"]):
            if root is not None:
                for file in root.rglob("*"):
                    if file.is_file():
                        assert mapped_study["marker"].encode() not in file.read_bytes()
        actual = (positional[3] / "prompt.txt").read_text()
        assert actual == positional[2]
    for cell in result["cells"]:
        dispatch = json.loads((Path(cell["attempts"][0]["path"]) / "dispatch.json").read_text())
        assert dispatch["acceptance_map_sha256"] == mapped_study["map"]["sha256"]
        assert dispatch["acceptance_policy"] == "mapped-acceptance-v1"
        assert not any("acceptance" in key for key in dispatch["native_expected"])
    # Reporting identifies selected declarations without requalifying or requiring live attachments.
    Path(mapped_study["map"]["path"]).unlink()
    assert study.report_study(args["plan_path"], args["expected_sha256"], args["output"]) == result
    from skilleval.repair_outcomes import render_review

    review = render_review(result)
    assert "Acceptance policy: mapped-acceptance-v1" in review
    assert mapped_study["map"]["sha256"] in review
    assert "Semantic validity remains unassessed" in review
    assert mapped_study["marker"] not in review


def test_foreign_map_is_refused_before_preparation(mapped_study, tmp_path):
    original = mapped_study["spec"]
    task = original.tasks[0]
    package = task_package.load_package(Path(task.package), task.package_sha256)
    other = task_package.prepare_package(
        package.model_copy(update={"package_id": "different-package"}),
        tmp_path / "other-package.json",
    )
    candidate = original.model_copy(
        update={
            "tasks": (
                task.model_copy(
                    update={
                        "package": other["path"],
                        "package_sha256": other["sha256"],
                    }
                ),
            )
        }
    )
    with pytest.raises(SkillEvalError, match="acceptance map differs"):
        study.prepare_study(candidate, tmp_path / "wrong-plan.json")
    assert not (tmp_path / "wrong-plan.json").exists()


@pytest.mark.parametrize("when", ["before-first", "between-dispatches"])
def test_map_tamper_is_refused_before_next_native_call(mapped_study, monkeypatch, when):
    original = runner.run_native
    calls = 0
    target = Path(mapped_study["map"]["path"])

    def observed(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        target.write_text("changed map")
        return result

    monkeypatch.setattr(runner, "run_native", observed)
    if when == "before-first":
        target.write_text("changed map")
    with pytest.raises(SkillEvalError, match="acceptance map digest"):
        study.run_study(**mapped_study["args"], development=True)
    assert calls == (0 if when == "before-first" else 1)


def _write_plan(path: Path, plan: dict[str, Any]) -> str:
    raw = json.dumps(plan).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("version", [1, 2, 3])
def test_old_plan_generations_keep_unmapped_meaning_and_bytes(prepared, version):
    path = prepared["plan_path"]
    plan = json.loads(path.read_text())
    plan["schema_version"] = version
    plan.pop("acceptance_policy")
    if version < 3:
        for task in plan["spec"]["tasks"]:
            task.pop("mode")
    if version == 1:
        for key in ("invocation_policy", "price_schedule"):
            plan["spec"].pop(key)
        for key in ("invocations", "skill_name"):
            plan.pop(key)
    digest = _write_plan(path, plan)
    raw = path.read_bytes()
    _, loaded = study._plan(path, digest)
    assert loaded.acceptance_policy == "legacy-unmapped"
    assert loaded.tasks[0].acceptance_map is None
    assert path.read_bytes() == raw
    for destination, key, value in (
        (plan, "acceptance_policy", "legacy-unmapped"),
        (plan, "acceptance_map", None),
        (plan, "acceptance_map_sha256", None),
        (plan["spec"], "acceptance_policy", "legacy-unmapped"),
        (plan["spec"]["tasks"][0], "acceptance_map", None),
        (plan["spec"]["tasks"][0], "acceptance_map_sha256", None),
    ):
        destination[key] = value
        with pytest.raises(SkillEvalError, match=r"legacy.*acceptance"):
            study._plan(path, _write_plan(path, plan))
        destination.pop(key)


@pytest.mark.parametrize("mutation", ["missing", "different"])
def test_generation_four_freezes_policy_without_silent_upgrade(prepared, mutation):
    path = prepared["plan_path"]
    plan = json.loads(path.read_text())
    assert plan["schema_version"] == 4
    assert plan["acceptance_policy"] == "legacy-unmapped"
    assert "acceptance_policy" not in plan["spec"]
    if mutation == "missing":
        plan.pop("acceptance_policy")
    else:
        plan["acceptance_policy"] = "mapped-acceptance-v1"
    with pytest.raises(SkillEvalError, match="frozen acceptance policy"):
        study._plan(path, _write_plan(path, plan))


def test_preparation_normalizes_map_path(mapped_study, tmp_path, monkeypatch):
    spec = mapped_study["spec"]
    selected = spec.tasks[0].model_copy(update={"acceptance_map": "map.json"})
    monkeypatch.chdir(tmp_path)
    receipt = study.prepare_study(
        spec.model_copy(update={"tasks": (selected,)}), tmp_path / "normalized-plan.json"
    )
    _, normalized = study._plan(Path(receipt["plan"]), receipt["sha256"])
    assert normalized.tasks[0].acceptance_map == str(tmp_path / "map.json")


def test_map_cannot_be_placed_inside_selected_skill_archive(mapped_study, tmp_path):
    spec = mapped_study["spec"]
    copied = Path(spec.skill_bundle) / "hidden-map.json"
    copied.write_bytes(Path(mapped_study["map"]["path"]).read_bytes())
    selected = spec.tasks[0].model_copy(update={"acceptance_map": str(copied)})
    with pytest.raises(SkillEvalError, match="outside input"):
        study.prepare_study(
            spec.model_copy(update={"tasks": (selected,)}), tmp_path / "leaking-plan.json"
        )
    assert not (tmp_path / "leaking-plan.json").exists()


def test_mapped_policy_does_not_allow_an_unmapped_second_task(mapped_study):
    spec = mapped_study["spec"]
    additional = specification().tasks[0].model_dump(mode="json")
    additional.update(task_id="second", input_sha256="f" * 64)
    data = spec.model_dump(mode="json")
    data["tasks"].append(additional)
    with pytest.raises(ValidationError, match="mapped acceptance requires every task"):
        study.StudySpec.model_validate_json(json.dumps(data))


def _read_study(args, reader):
    if reader == "resume":
        return study.run_study(**args, development=True)
    offline = {key: value for key, value in args.items() if key != "auth_file"}
    if reader == "report":
        return study.report_study(**offline)
    return study.reconcile_study(**offline)


@pytest.mark.parametrize("reader", ["report", "resume", "reconcile"])
@pytest.mark.parametrize("state", ["terminal", "unfinished"])
def test_consistent_dispatch_outcome_tamper_cannot_change_frozen_acceptance(
    mapped_study, monkeypatch, reader, state
):
    args = mapped_study["args"]
    result = study.run_study(**args, development=True)
    path = Path(result["cells"][0]["attempts"][0]["path"])
    dispatch_path = path / "dispatch.json"
    original_dispatch = json.loads(dispatch_path.read_text())
    outcome_path = path / "outcome.json"
    original_outcome = json.loads(outcome_path.read_text())
    if state == "unfinished":
        outcome_path.unlink()
    # This remains an offline identity check when live design artifacts disappear.
    Path(mapped_study["map"]["path"]).unlink()
    baseline = study.report_study(**{k: v for k, v in args.items() if k != "auth_file"})
    assert baseline["acceptance_policy"] == "mapped-acceptance-v1"

    def forbidden(*args, **kwargs):
        pytest.fail("retained dispatch mismatch must precede any native invocation")

    monkeypatch.setattr(runner, "run_native", forbidden)
    for field, value in (
        ("acceptance_policy", "legacy-unmapped"),
        ("acceptance_map", "/foreign/map.json"),
        ("acceptance_map_sha256", "0" * 64),
        ("acceptance_policy", None),
        ("acceptance_map", None),
        ("acceptance_map_sha256", None),
    ):
        changed = dict(original_dispatch)
        if value is None:
            changed.pop(field)
        else:
            changed[field] = value
        _write_plan(dispatch_path, changed)
        if state == "terminal":
            # Update its internal checksum too: hash consistency is not plan fidelity.
            outcome = {**original_outcome, "dispatch_sha256": study.file_digest(dispatch_path)}
            _write_plan(outcome_path, outcome)
        with pytest.raises(SkillEvalError, match=r"dispatch acceptance.*frozen"):
            _read_study(args, reader)
        if state == "unfinished":
            assert not outcome_path.exists()


@pytest.mark.parametrize("version", [1, 2, 3])
def test_historical_dispatch_without_acceptance_fields_remains_readable(
    prepared, monkeypatch, version
):
    path = prepared["plan_path"]
    plan = json.loads(path.read_text())
    plan["schema_version"] = version
    plan.pop("acceptance_policy")
    if version < 3:
        for task in plan["spec"]["tasks"]:
            task.pop("mode")
    if version == 1:
        for key in ("invocation_policy", "price_schedule"):
            plan["spec"].pop(key)
        for key in ("invocations", "skill_name"):
            plan.pop(key)
    prepared["expected_sha256"] = _write_plan(path, plan)
    result = study.run_study(**prepared, development=True)
    assert result["acceptance_policy"] == "legacy-unmapped"
    assert result["acceptance_maps"] == []
    attempt = Path(result["cells"][0]["attempts"][0]["path"])
    dispatch_path = attempt / "dispatch.json"
    original = json.loads(dispatch_path.read_text())
    assert not any(key.startswith("acceptance_") for key in original)
    original_outcome = json.loads((attempt / "outcome.json").read_text())

    def forbidden(*args, **kwargs):
        pytest.fail("historical replay must not load acceptance artifacts or invoke a native run")

    monkeypatch.setattr(acceptance, "load_acceptance", forbidden)
    monkeypatch.setattr(runner, "run_native", forbidden)
    for reader in ("report", "resume", "reconcile"):
        assert _read_study(prepared, reader) == result
    for field, value in (
        ("acceptance_policy", "legacy-unmapped"),
        ("acceptance_policy", "mapped-acceptance-v1"),
        ("acceptance_map", "/foreign/map.json"),
        ("acceptance_map_sha256", None),
    ):
        _write_plan(dispatch_path, {**original, field: value})
        _write_plan(
            attempt / "outcome.json",
            {
                **original_outcome,
                "dispatch_sha256": study.file_digest(dispatch_path),
            },
        )
        for reader in ("report", "resume", "reconcile"):
            with pytest.raises(SkillEvalError, match=r"dispatch acceptance.*frozen"):
                _read_study(prepared, reader)


def test_generation_four_unmapped_dispatch_cannot_acquire_map(prepared):
    result = study.run_study(**prepared, development=True)
    attempt = Path(result["cells"][0]["attempts"][0]["path"])
    dispatch_path = attempt / "dispatch.json"
    original = json.loads(dispatch_path.read_text())
    outcome = json.loads((attempt / "outcome.json").read_text())
    assert original["acceptance_policy"] == "legacy-unmapped"
    for field in ("acceptance_map", "acceptance_map_sha256"):
        _write_plan(dispatch_path, {**original, field: None})
        _write_plan(
            attempt / "outcome.json",
            {
                **outcome,
                "dispatch_sha256": study.file_digest(dispatch_path),
            },
        )
        with pytest.raises(SkillEvalError, match=r"dispatch acceptance.*frozen"):
            _read_study(prepared, "report")


def test_dispatch_changed_during_native_call_is_refused_before_recording(mapped_study, monkeypatch):
    original = runner.run_native
    calls = 0

    def changed(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        dispatch_path = args[3].parent / "dispatch.json"
        dispatch = json.loads(dispatch_path.read_text())
        dispatch["acceptance_policy"] = "legacy-unmapped"
        _write_plan(dispatch_path, dispatch)
        return result

    monkeypatch.setattr(runner, "run_native", changed)
    with pytest.raises(SkillEvalError, match=r"dispatch acceptance.*frozen"):
        study.run_study(**mapped_study["args"], development=True)
    assert calls == 1
    assert not list(mapped_study["args"]["output"].glob("attempts/*/*/outcome.json"))
