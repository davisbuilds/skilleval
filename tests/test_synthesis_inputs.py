"""Real source and retained-receipt checks; no actual VM or model invocation."""

import hashlib
import json
import os
from pathlib import Path

import pytest
from test_handoff import writer as writer
from test_handoff import writer_factory as writer_factory
from test_handoff_report import linked as linked
from test_judge import run_directory
from test_repair_assessment import judgment
from test_runner import route as route

from skilleval import handoff, repair, repair_assessment, runner, synthesis_inputs
from skilleval.errors import SkillEvalError
from skilleval.identity import file_digest
from skilleval.repair_check import check_repair
from skilleval.synthesis_inputs import SynthesisSelection, compile_selection, load_selection


def selection_file(tmp_path: Path, linked) -> Path:
    source = tmp_path / "request.txt"
    source.write_text("Implement the requested export feature.\n")
    report_spec = tmp_path / "handoff-report.json"
    report_data = linked["spec"].model_dump(mode="json")
    report_data["handoff"] = os.path.relpath(report_data["handoff"], tmp_path)
    for key in ("plan", "output"):
        report_data["writer_study"][key] = os.path.relpath(
            report_data["writer_study"][key], tmp_path
        )
    for ref in (*report_data["implementation_runs"], *report_data["judge_runs"]):
        ref["path"] = os.path.relpath(ref["path"], tmp_path)
    report_spec.write_text(json.dumps(report_data, indent=2))
    path = tmp_path / "selection.json"
    path.write_text(
        json.dumps(
            {
                "title": "A retained development pair",
                "objective": "Investigate quality independently of accounting.",
                "sources": [
                    {
                        "id": "request",
                        "label": "Original request",
                        "role": "request",
                        "path": "request.txt",
                        "sha256": file_digest(source),
                    }
                ],
                "arms": [
                    {
                        "id": "off",
                        "label": "OFF",
                        "handoff_report_spec": {
                            "path": "handoff-report.json",
                            "sha256": file_digest(report_spec),
                        },
                        "assessments": [],
                    }
                ],
                "coverage": {
                    "selection_policy": "Explicit original attempts",
                    "known_omissions": [],
                    "limitations": ["One authored task; independent audit pending."],
                },
                "price_schedule": linked["spec"].price_schedule.model_dump(mode="json"),
            }
        )
    )
    return path


def test_selection_rejects_changed_text_before_compiling_authority(linked, tmp_path):
    path = selection_file(tmp_path, linked)
    spec = load_selection(path)
    # WHY: The selection itself stays intact; a retained source changes afterward.
    (tmp_path / "request.txt").write_text("A different work request.\n")
    with pytest.raises(SkillEvalError, match="digest"):
        compile_selection(spec)


def test_compile_freezes_full_text_and_canonical_accounting(linked, tmp_path):
    path = selection_file(tmp_path, linked)
    spec = load_selection(path)
    assert Path(spec.sources[0].path) == tmp_path / "request.txt"
    spec = spec.model_copy(
        update={"arms": (spec.arms[0].model_copy(update={"label": "L" * 16000}),)}
    )
    compiled = compile_selection(spec)
    assert (
        compiled["payloads"]["case/sources/request.txt"] == (tmp_path / "request.txt").read_bytes()
    )
    for source in compiled["sources"]:
        raw = compiled["payloads"][source["path"]]
        assert len(raw) == source["bytes"]
        assert hashlib.sha256(raw).hexdigest() == source["sha256"]
    arm = compiled["arms"][0]
    assert "Writer primary: completed" in arm["status"]
    assert "No selected semantic assessment" in arm["status"]
    assert "Combined agents (2 selected attempt(s)): 10 tokens total." in arm["usage"]
    assert any("USD 0.0000122 total" in row for row in arm["cost"])
    assert any("Judge overhead (separate)" in row for row in arm["cost"])
    timing_report = json.loads(compiled["payloads"]["case/sources/derived-off-accounting.txt"])
    seconds = timing_report["report"]["combined_agents"]["timings_seconds"]["total_seconds"][
        "total"
    ]
    display_seconds = f"{seconds:.3f}".rstrip("0").rstrip(".")
    assert any(f"Combined agents: {display_seconds} seconds total" in row for row in arm["timing"])
    accounting = json.loads(compiled["payloads"]["case/sources/derived-off-accounting.txt"])
    assert next(iter(accounting)) == "canonical_display"
    assert accounting["canonical_display"] == {
        key: arm[key] for key in ("status", "usage", "cost", "timing")
    }
    canonical_excerpt = "\n".join(
        compiled["payloads"]["case/sources/derived-off-accounting.txt"].decode().splitlines()[:200]
    )
    for value in [arm["status"], *arm["usage"], *arm["cost"], *arm["timing"]]:
        assert json.dumps(value) in canonical_excerpt
    assert all(len(row["label"]) <= 16000 for row in compiled["sources"])
    assert accounting["handoff_report_spec_sha256"] == spec.arms[0].handoff_report_spec.sha256
    assert accounting["report"]["implementation"]["primary_attempt"] is None
    lineage = json.loads((linked["bundle"] / "handoff.json").read_text())["lineage"]
    spec_source = next(
        row for row in compiled["sources"] if row["id"] == "derived-off-selected-spec"
    )
    assert spec_source["sha256"] == lineage["spec_sha256"]
    assert spec_source["role"] == "artifact"
    assert (
        compiled["payloads"][spec_source["path"]]
        == (linked["bundle"] / "input" / lineage["spec_path"]).read_bytes()
    )
    assert accounting["selected_spec"]["sha256"] == lineage["spec_sha256"]
    assert compiled["coverage"]["limitations"] == ["One authored task; independent audit pending."]
    assert compiled["price_schedule"] == linked["spec"].price_schedule.model_dump(mode="json")


def test_unknown_usage_is_not_replaced_by_raw_diagnostic_or_known_subtotal(linked, tmp_path):
    incomplete = linked["spec"].model_copy(
        update={
            "implementation_runs": (
                *linked["spec"].implementation_runs,
                handoff.NativeRunReference(path=str(tmp_path / "unfinished")),
            ),
        }
    )
    linked = {**linked, "spec": incomplete}
    path = selection_file(tmp_path, linked)
    diagnostic = tmp_path / "raw.txt"
    diagnostic.write_text("raw total_tokens: 999999999; cost: USD 0\n")
    data = json.loads(path.read_text())
    data["sources"].append(
        {
            "id": "raw",
            "role": "trace",
            "label": "Raw diagnostic",
            "path": "raw.txt",
            "sha256": file_digest(diagnostic),
        }
    )
    path.write_text(json.dumps(data))
    arm = compile_selection(load_selection(path))["arms"][0]
    assert any(
        "Unknown tokens total; known subtotal 10 tokens; 1 attempt(s) unknown" in row
        for row in arm["usage"]
    )
    assert any(
        "Unknown total; known schedule-group subtotal(s): USD 0.0000122" in row
        for row in arm["cost"]
    )
    assert not any("999999999" in row for row in arm["usage"])
    assert "unfinished=1" in arm["status"]


def test_selected_report_specification_must_match_its_pinned_bytes(linked, tmp_path):
    path = selection_file(tmp_path, linked)
    spec = load_selection(path)
    (tmp_path / "handoff-report.json").write_text("{}\n")
    with pytest.raises(SkillEvalError, match="handoff report specification digest"):
        compile_selection(spec)


def record_stage(linked, root: Path, *, implementation=False, native=None):
    root.mkdir()
    bundle = json.loads((linked["bundle"] / "handoff.json").read_text())
    if implementation:
        original = linked["writer"]["task"]
        task_spec = original.model_copy(
            update={
                "input_bundle": str(linked["bundle"] / "input"),
                "input_sha256": bundle["input_sha256"],
                "prompt": bundle["prompt"],
            }
        )
        task_path = root / "task.json"
        prepared = repair.prepare_repair(task_spec, task_path)
        native = native or Path(linked["spec"].implementation_runs[0].path)
        result = repair.collect_repair(task_path, prepared["sha256"], native, root / "result")
        source = {
            "task": str(task_path),
            "task_sha256": prepared["sha256"],
            "result": result["path"],
            "result_sha256": result["sha256"],
        }
    else:
        selected = handoff.SpecHandoffSpec.model_validate_json(json.dumps(bundle["source_spec"]))
        source = selected.model_dump(mode="json", exclude={"spec_path", "spec_sha256"})
    check = root / "check"
    check_repair(
        Path(source["task"]),
        source["task_sha256"],
        Path(source["result"]),
        source["result_sha256"],
        check,
        runtime=linked["writer"]["route"]["runtime"],
        image=linked["writer"]["route"]["image"],
        image_sha256=linked["writer"]["route"]["image_sha256"],
    )
    check_path = check / "repair-check.json"
    assessment_spec = repair_assessment.RepairAssessmentSpec.model_validate_json(
        json.dumps(
            {
                **source,
                "checks": [
                    {**source, "report": str(check_path), "report_sha256": file_digest(check_path)}
                ],
            }
        )
    )
    prepared = repair_assessment.prepare_assessment(assessment_spec, root / "packet")
    run = run_directory(root, prepared, judgment(prepared))
    return repair_assessment.record_assessment(
        assessment_spec, root / "packet", run, root / "records"
    )


def with_assessment(path: Path, recorded, stage="specification"):
    data = json.loads(path.read_text())
    data["arms"][0]["assessments"] = [
        {
            "id": "quality",
            "stage": stage,
            "directory": recorded["path"],
            "sha256": recorded["sha256"],
        }
    ]
    path.write_text(json.dumps(data))
    return load_selection(path)


def test_selected_writer_assessment_is_bound_and_not_replaced_by_other_valid_record(
    linked, tmp_path
):
    path = selection_file(tmp_path, linked)
    recorded = record_stage(linked, tmp_path / "right")
    compiled = compile_selection(with_assessment(path, recorded))
    assert "specification quality: satisfied-in-reviewed-scope" in compiled["arms"][0]["status"]
    projection = json.loads(compiled["payloads"]["case/sources/derived-off-quality.txt"])
    assert projection["provenance"]["record_sha256"] == recorded["sha256"]
    assert projection["assessment"]["independent_audit"] == "required-pending"
    # WHY: Both arms are real retained study attempts with identical authored output;
    # a valid judgment for the other arm must not be substituted by content alone.
    writer_ref = linked["spec"].writer_study
    plan = json.loads(Path(writer_ref.plan).read_text())
    other_cell = next(row for row in plan["assignments"] if row["cell_id"] != writer_ref.cell_id)
    other_native = (
        Path(writer_ref.output) / "attempts" / other_cell["cell_id"] / "attempt-001/native"
    )
    writer_source = linked["writer"]["spec"]
    other_result = repair.collect_repair(
        Path(writer_source.task), writer_source.task_sha256, other_native, tmp_path / "other-result"
    )
    other_bundle = tmp_path / "other-handoff"
    handoff.prepare_handoff(
        writer_source.model_copy(
            update={
                "result": other_result["path"],
                "result_sha256": other_result["sha256"],
            }
        ),
        other_bundle,
    )
    other = record_stage({**linked, "bundle": other_bundle}, tmp_path / "wrong")
    with pytest.raises(SkillEvalError, match="differs from handoff writer"):
        compile_selection(with_assessment(path, other))


def test_selected_implementation_assessment_requires_selected_native_attempt(linked, tmp_path):
    path = selection_file(tmp_path, linked)
    recorded = record_stage(linked, tmp_path / "right", implementation=True)
    compiled = compile_selection(with_assessment(path, recorded, "implementation"))
    assert "implementation quality: satisfied-in-reviewed-scope" in compiled["arms"][0]["status"]
    other_native = tmp_path / "other-native"
    frozen = linked["frozen"]
    runner.run_native(
        linked["bundle"] / "input",
        frozen["input_digest"],
        frozen["prompt"],
        other_native,
        mode="repair",
        **linked["writer"]["route"],
    )
    other = record_stage(linked, tmp_path / "wrong", implementation=True, native=other_native)
    with pytest.raises(SkillEvalError, match="differs from handoff attempts"):
        compile_selection(with_assessment(path, other, "implementation"))


@pytest.mark.parametrize("variant", ["digest", "utf8", "total-limit", "source-limit"])
def test_explicit_sources_are_pinned_utf8_and_bounded(linked, tmp_path, monkeypatch, variant):
    path = selection_file(tmp_path, linked)
    source = tmp_path / "request.txt"
    if variant == "digest":
        source.write_text("Different source.")
    elif variant == "utf8":
        source.write_bytes(b"\xff\xfe")
        data = json.loads(path.read_text())
        data["sources"][0]["sha256"] = file_digest(source)
        path.write_text(json.dumps(data))
    elif variant == "total-limit":
        monkeypatch.setattr(synthesis_inputs, "TOTAL_SOURCE_LIMIT", source.stat().st_size - 1)
    else:
        monkeypatch.setattr(synthesis_inputs, "SOURCE_LIMIT", source.stat().st_size - 1)
    spec = (
        load_selection(path)
        if variant != "source-limit"
        else SynthesisSelection.model_validate_json(
            path.read_text().replace('"request.txt"', json.dumps(str(source)))
        )
    )
    with pytest.raises(SkillEvalError):
        compile_selection(spec)


@pytest.mark.parametrize("variant", ["request", "duplicate", "reserved", "generated-collision"])
def test_selection_requires_request_and_unique_source_identity(linked, tmp_path, variant):
    path = selection_file(tmp_path, linked)
    data = json.loads(path.read_text())
    if variant == "request":
        data["sources"][0]["role"] = "context"
    elif variant == "duplicate":
        data["sources"] *= 2
    elif variant == "reserved":
        data["sources"][0]["id"] = "derived-arm-accounting"
    else:
        data["arms"][0]["assessments"] = [
            {
                "id": "accounting",
                "stage": "implementation",
                "directory": str(tmp_path),
                "sha256": "a" * 64,
            }
        ]
    with pytest.raises(ValueError):
        SynthesisSelection.model_validate_json(json.dumps(data))
