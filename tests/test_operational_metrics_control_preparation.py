"""Real file/packet contracts with synthetic guest receipts, never model validity."""

import importlib.util
import json
from pathlib import Path

import pytest
from test_repair import task as task
from test_runner import route as route
from test_task_package import package_spec

from skilleval import acceptance, repair, task_package
from skilleval.errors import SkillEvalError
from skilleval.identity import file_digest
from skilleval.repair_check import check_repair

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/document/operational-metrics"


@pytest.fixture
def controls(task, route, tmp_path):
    module_spec = importlib.util.spec_from_file_location(
        "prepare_authored_metrics_controls", EXAMPLE / "prepare_controls.py"
    )
    helper = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(helper)
    spec = task["spec"].model_copy(
        update={
            "worker": str(EXAMPLE / "worker.py"),
            "worker_sha256": file_digest(EXAMPLE / "worker.py"),
            "editable_paths": ("docs/specs/placeholder.md",),
            "editable_directories": ("docs/specs",),
            "probes": (
                repair.RepairProbe(
                    probe_id="spec-artifact",
                    request={"check": "spec-artifact"},
                    expected={"candidate_found": True, "artifacts_readable": True},
                ),
            ),
        }
    )
    prepared = repair.prepare_repair(spec, tmp_path / "document-task.json")
    bound = {**task, "task": Path(prepared["path"]), "digest": prepared["sha256"], "spec": spec}
    package = task_package.prepare_package(
        package_spec(bound, route).model_copy(update={"family": "document"}),
        tmp_path / "document-package.json",
    )
    mapped = json.loads((EXAMPLE / "acceptance-map.template.json").read_text())
    mapped.update(
        task=prepared["path"],
        task_sha256=prepared["sha256"],
        package=package["path"],
        package_sha256=package["sha256"],
    )
    saved = acceptance.prepare_acceptance(
        acceptance.AcceptanceMapSpec.model_validate_json(json.dumps(mapped)),
        tmp_path / "acceptance.json",
    )
    receipt = helper.prepare(
        Path(prepared["path"]),
        prepared["sha256"],
        Path(saved["path"]),
        saved["sha256"],
        tmp_path / "controls",
        runtime=route["runtime"],
        image=route["image"],
        image_sha256=route["image_sha256"],
    )
    return helper, receipt, bound


def test_prepares_eight_authored_candidates_without_executing_source(controls):
    _, receipt, bound = controls
    assert len(receipt["cases"]) == 8
    for row in receipt["cases"]:
        ref = row["source"]
        result, files = repair.result_files(
            bound["spec"], ref["task_sha256"], Path(ref["result"]), ref["result_sha256"]
        )
        assert result["provenance"]["kind"] == "authored-control"
        assert result["provenance"]["model_attempt"] is False
        assert len(row["expected_requirements"]) == 7
        assert {n: raw for n, raw, _ in files}["code.py"].startswith(b"raise RuntimeError")
        assert result["changed_paths"] == (
            [] if row["case_id"] == "missing" else ["docs/specs/authored-candidate.markdown"]
        )
        assert not Path(row["check_report"]).exists()
        assert "repair" in row["check_argv"] and "check" in row["check_argv"]


def test_finalize_refuses_missing_or_changed_receipts_before_writing(controls, tmp_path):
    helper, receipt, _ = controls
    output = tmp_path / "assessments"
    with pytest.raises((FileNotFoundError, SkillEvalError)):
        helper.finalize(Path(receipt["path"]), receipt["sha256"], output)
    assert not output.exists()
    with pytest.raises(SkillEvalError, match="preparation digest differs"):
        helper.finalize(Path(receipt["path"]), "0" * 64, output)
    assert not output.exists()


def test_finalize_consumes_checked_reports_and_withholds_author_labels(controls, route, tmp_path):
    helper, receipt, _ = controls
    # The existing synthetic runtime generates legitimate checker/recovery
    # envelopes for deterministic protocol tests; it does not run real source.
    for row in receipt["cases"]:
        ref = row["source"]
        observed = check_repair(
            Path(ref["task"]),
            ref["task_sha256"],
            Path(ref["result"]),
            ref["result_sha256"],
            Path(row["check_report"]).parent,
            runtime=route["runtime"],
            image=route["image"],
            image_sha256=route["image_sha256"],
        )
        assert observed["status"] == "completed"
    finalized = helper.finalize(Path(receipt["path"]), receipt["sha256"], tmp_path / "assessments")
    assert len(finalized["cases"]) == 8
    for row in finalized["cases"]:
        spec = json.loads(Path(row["spec"]).read_text())
        assert spec["requirement_policy"] == "acceptance-requirements-v1"
        packet = Path(row["packet"])
        assert (packet / "case/requirements.json").is_file()
        metadata = json.loads((packet / "packet.json").read_text())
        assert metadata["rubric_version"] == "specification-requirements-agentic-v1"
        tools = json.loads((packet / "case/tools.json").read_text())
        assert tools["origin"] == "authored-control" and tools["items"] == []
        for path in packet.rglob("*"):
            if path.is_file():
                assert b"expected_requirements" not in path.read_bytes()
                assert b"completed-but-defective-specification" not in path.read_bytes()
