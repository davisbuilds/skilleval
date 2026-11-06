"""Acceptance traceability is offline metadata, not semantic qualification."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_repair import task as task
from test_runner import route as route
from test_task_package import package_spec

from skilleval import acceptance, evidence, repair, task_package
from skilleval.errors import SkillEvalError
from skilleval.identity import file_digest


@pytest.fixture
def mapped(task, route, tmp_path):
    package = task_package.prepare_package(package_spec(task, route), tmp_path / "package.json")
    data = {
        "map_id": "authored-map",
        "task": str(task["task"]),
        "task_sha256": task["digest"],
        "package": package["path"],
        "package_sha256": package["sha256"],
        "purpose": "Make the ordinary requested repair observable.",
        "population": "authored development control",
        "selection_rationale": "Synthetic packaging case, not representative sampling.",
        "exposure_history": "Author saw source and expected behavior.",
        "authored_after_outcomes": True,
        "requirements": [
            {
                "requirement_id": "R1",
                "statement": "Return the requested result.",
                "role": "core",
                "criticality": "required",
                "source_rationale": "Task prompt: Repair the defect; worker result protocol.",
                "mechanical": [{"probe_id": "result", "pointer": ""}],
                "semantic": [
                    {"criterion": "task-contract", "question": "Does the repair meet the brief?"}
                ],
            }
        ],
        "controls": [
            {
                "control_id": "baseline",
                "purpose": "Distinguish the unfixed behavior.",
                "requirement_ids": ["R1"],
                "expected_discrimination": "Unfixed result is false.",
                "limitations": ["Authored control; execution evidence is pending."],
            }
        ],
        "open_questions": ["Does this discriminate plausible wrong fixes?"],
        "limitations": ["Finite authored traceability does not establish independent validity."],
    }
    return data


def spec(data):
    return acceptance.AcceptanceMapSpec.model_validate_json(json.dumps(data))


def prepare(data, tmp_path, name="map.json"):
    return acceptance.prepare_acceptance(spec(data), tmp_path / name)


def test_round_trip_and_render_preserve_declarations_without_execution(mapped, task, tmp_path):
    # Real source and worker fixtures raise if imported. Both remain inert data.
    before = {
        name: path.read_bytes()
        for name, path in {
            "task": task["task"],
            "worker": Path(task["spec"].worker),
            "source": task["base"] / "code.py",
            "package": Path(mapped["package"]),
        }.items()
    }
    saved = prepare(mapped, tmp_path)
    loaded = acceptance.load_acceptance(Path(saved["path"]), saved["sha256"])
    assert loaded == spec(mapped)
    assert saved["semantic_validity"] == "unassessed"
    assert saved["provenance"] == "declared-unverified"
    assert saved["scored_admitted"] is False
    assert saved["controls"] == [{"control_id": "baseline", "status": "pending"}]
    rendered = acceptance.render_acceptance(loaded)
    assert "R1" in rendered and "task-contract" in rendered
    assert "Authored after outcomes: true" in rendered
    assert "independent audit" in rendered
    assert before == {
        name: path.read_bytes()
        for name, path in {
            "task": task["task"],
            "worker": Path(task["spec"].worker),
            "source": task["base"] / "code.py",
            "package": Path(mapped["package"]),
        }.items()
    }
    with pytest.raises(FileExistsError):
        prepare(mapped, tmp_path)


def test_map_and_attachment_tampering_is_rejected(mapped, tmp_path):
    attached = tmp_path / "review.txt"
    attached.write_text("An authored review declaration, not independent audit.")
    mapped["review_evidence"] = [{"path": str(attached), "sha256": file_digest(attached)}]
    mapped["controls"][0]["evidence"] = mapped["review_evidence"][0]
    saved = prepare(mapped, tmp_path)
    assert saved["review_state"] == "evidence-attached"
    assert saved["controls"][0]["status"] == "evidence-attached"
    assert saved["semantic_validity"] == "unassessed"
    attached.write_text("changed")
    with pytest.raises(SkillEvalError, match="evidence digest"):
        acceptance.load_acceptance(Path(saved["path"]), saved["sha256"])
    Path(saved["path"]).write_text("{}")
    with pytest.raises(SkillEvalError, match="map digest"):
        acceptance.load_acceptance(Path(saved["path"]), saved["sha256"])


def test_foreign_task_is_rejected_before_publication(mapped, task, tmp_path):
    other = repair.prepare_repair(
        task["spec"].model_copy(update={"task_id": "other"}), tmp_path / "other-task.json"
    )
    mapped.update(task=other["path"], task_sha256=other["sha256"])
    with pytest.raises(SkillEvalError, match="another package task"):
        prepare(mapped, tmp_path)
    assert not (tmp_path / "map.json").exists()


@pytest.mark.parametrize("pointer", ["/missing", "not-a-pointer", "/~2", "/value/0"])
def test_pointer_must_select_actual_frozen_expected_value(mapped, tmp_path, pointer):
    mapped["requirements"][0]["mechanical"][0]["pointer"] = pointer
    with pytest.raises(SkillEvalError, match="pointer"):
        prepare(mapped, tmp_path)


@pytest.mark.parametrize("mutation", ["unknown", "unmapped", "both", "duplicate"])
def test_every_probe_is_mapped_or_explicitly_unmapped(mapped, tmp_path, mutation):
    if mutation == "unknown":
        mapped["requirements"][0]["mechanical"][0]["probe_id"] = "foreign"
    elif mutation == "unmapped":
        mapped["requirements"][0]["mechanical"] = []
    elif mutation == "both":
        mapped["unmapped_probes"] = [{"probe_id": "result", "reason": "Excluded."}]
    else:
        mapped["unmapped_probes"] = [
            {"probe_id": "result", "reason": "Excluded."},
            {"probe_id": "result", "reason": "Excluded twice."},
        ]
    with pytest.raises((SkillEvalError, ValidationError)):
        prepare(mapped, tmp_path)


def test_unmapped_probe_and_explicit_gap_remain_visible(mapped, tmp_path):
    requirement = mapped["requirements"][0]
    requirement.update(mechanical=[], semantic=[], gaps=["Runtime coverage is pending."])
    mapped["unmapped_probes"] = [{"probe_id": "result", "reason": "Known coverage gap."}]
    saved = prepare(mapped, tmp_path)
    assert saved["requirements_with_gaps"] == ["R1"]
    assert saved["unmapped_probes"] == ["result"]
    assert "Runtime coverage is pending" in acceptance.render_acceptance(spec(mapped))
    requirement["gaps"] = []
    with pytest.raises((SkillEvalError, ValidationError), match=r"route|gap"):
        prepare(mapped, tmp_path, "uncovered.json")


def test_semantic_criteria_follow_the_package_family(mapped, tmp_path):
    mapped["requirements"][0]["semantic"][0]["criterion"] = "factual-support"
    with pytest.raises(SkillEvalError, match="criterion"):
        prepare(mapped, tmp_path)
    package = json.loads(Path(mapped["package"]).read_bytes())
    package["spec"]["family"] = "document"
    target = tmp_path / "document-package.json"
    mapped.update(package=str(target), package_sha256=repair._publish(target, package))
    saved = prepare(mapped, tmp_path)
    assert saved["semantic_validity"] == "unassessed"
    mapped["requirements"][0]["semantic"][0]["criterion"] = "task-contract"
    with pytest.raises(SkillEvalError, match="criterion"):
        prepare(mapped, tmp_path, "wrong-family.json")


@pytest.mark.parametrize(
    "mutation", ["requirement-id", "control-id", "control-requirement", "route"]
)
def test_duplicate_and_foreign_requirement_routes_are_rejected(mapped, tmp_path, mutation):
    if mutation == "requirement-id":
        mapped["requirements"].append(mapped["requirements"][0])
    elif mutation == "control-id":
        mapped["controls"].append(mapped["controls"][0])
    elif mutation == "control-requirement":
        mapped["controls"][0]["requirement_ids"] = ["foreign"]
    else:
        mapped["requirements"][0]["mechanical"].append(mapped["requirements"][0]["mechanical"][0])
    with pytest.raises((SkillEvalError, ValidationError)):
        prepare(mapped, tmp_path)


def test_map_and_evidence_stay_outside_every_solver_capture(mapped, task, tmp_path):
    with pytest.raises(SkillEvalError, match="outside solver inputs"):
        acceptance.prepare_acceptance(spec(mapped), task["base"] / "map.json")
    mapped["review_evidence"] = [
        {
            "path": str(task["base"] / "README.md"),
            "sha256": file_digest(task["base"] / "README.md"),
        }
    ]
    with pytest.raises(SkillEvalError, match="outside solver inputs"):
        prepare(mapped, tmp_path)
    assert not (task["base"] / "map.json").exists()


@pytest.mark.parametrize("field", ["selection_rationale", "exposure_history", "purpose"])
def test_empty_declarations_are_rejected(mapped, field):
    mapped[field] = "  "
    with pytest.raises(ValidationError):
        spec(mapped)


@pytest.mark.parametrize("pointer", ["/a~1b/~0key/0", "/", "/a~1b/~0key", ""])
def test_nested_pointer_escapes_and_root_are_valid(mapped, task, tmp_path, pointer):
    raw = task["spec"].model_dump(mode="json")
    raw["probes"][0]["expected"] = {"a/b": {"~key": [True, False]}, "": "empty-key"}
    nested = repair.prepare_repair(
        repair.RepairSpec.model_validate_json(json.dumps(raw)), tmp_path / "nested-task.json"
    )
    package = json.loads(Path(mapped["package"]).read_bytes())
    package["spec"].update(task=nested["path"], task_sha256=nested["sha256"])
    target = tmp_path / "nested-package.json"
    mapped.update(
        task=nested["path"],
        task_sha256=nested["sha256"],
        package=str(target),
        package_sha256=repair._publish(target, package),
    )
    mapped["requirements"][0]["mechanical"][0]["pointer"] = pointer
    saved = prepare(mapped, tmp_path)
    assert (
        acceptance.load_acceptance(Path(saved["path"]), saved["sha256"])
        .requirements[0]
        .mechanical[0]
        .pointer
        == pointer
    )
    if pointer == "/a~1b/~0key/0":
        for invalid in (
            "/a~1b/~0key/01",
            "/a~1b/~0key/-1",
            "/a~1b/~0key/2",
            "/a~1b/~0key/\u0661",
            "/a~1b/~0key/-",
        ):
            mapped["requirements"][0]["mechanical"][0]["pointer"] = invalid
            with pytest.raises(SkillEvalError, match="pointer"):
                prepare(mapped, tmp_path, "invalid.json")


def test_reference_capture_also_excludes_map_and_control_evidence(mapped, task, tmp_path):
    reference = evidence.snapshot(task["source"], tmp_path / "reference-input")
    package = json.loads(Path(mapped["package"]).read_bytes())
    extra = dict(package["spec"]["validation"][0])
    extra.update(
        case_id="reference",
        role="reference",
        input_bundle=str(tmp_path / "reference-input"),
        input_sha256=reference["manifest_sha256"],
    )
    package["spec"]["validation"].append(extra)
    target = tmp_path / "reference-package.json"
    mapped.update(package=str(target), package_sha256=repair._publish(target, package))
    with pytest.raises(SkillEvalError, match="outside solver inputs"):
        acceptance.prepare_acceptance(spec(mapped), tmp_path / "reference-input/map.json")
    control = tmp_path / "reference-input/README.md"
    mapped["controls"][0]["evidence"] = {"path": str(control), "sha256": file_digest(control)}
    with pytest.raises(SkillEvalError, match="outside solver inputs"):
        prepare(mapped, tmp_path)


def test_relative_paths_freeze_as_absolute_and_prose_never_becomes_control_verdict(
    mapped, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    attached = tmp_path / "evidence.py"
    attached.write_text("raise RuntimeError('attachment is data, never executable')\n")
    mapped["task"] = Path(mapped["task"]).name
    mapped["package"] = Path(mapped["package"]).name
    mapped["review_evidence"] = [{"path": attached.name, "sha256": file_digest(attached)}]
    mapped["controls"][0]["evidence"] = mapped["review_evidence"][0]
    saved = prepare(mapped, tmp_path)
    loaded = acceptance.load_acceptance(Path(saved["path"]), saved["sha256"])
    assert Path(loaded.task).is_absolute() and Path(loaded.package).is_absolute()
    assert Path(loaded.review_evidence[0].path).is_absolute()
    assert loaded.controls[0].evidence == loaded.review_evidence[0]
    mapped["controls"][0]["passed"] = True
    with pytest.raises(ValidationError, match="Extra inputs"):
        spec(mapped)


def test_review_write_is_exclusive_and_excludes_solver_captures(mapped, task, tmp_path):
    with pytest.raises(SkillEvalError, match="outside solver inputs"):
        acceptance.write_acceptance_review(spec(mapped), task["base"] / "review.md")
    assert not (task["base"] / "review.md").exists()
    output = tmp_path / "review.md"
    digest = acceptance.write_acceptance_review(spec(mapped), output)
    assert digest == file_digest(output)
    assert output.read_text() == acceptance.render_acceptance(spec(mapped))
    assert "Source rationale:" in output.read_text()
    assert "Expected discrimination:" in output.read_text()
    assert "Open questions" in output.read_text()
    with pytest.raises(FileExistsError):
        acceptance.write_acceptance_review(spec(mapped), output)
    assert file_digest(output) == digest
    assert not list(tmp_path.glob(".acceptance-*"))
