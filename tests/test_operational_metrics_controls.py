"""Authored offline controls exercise collection/contracts, not semantic accuracy."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_repair import task as task
from test_runner import route as route
from test_task_package import package_spec

from skilleval import acceptance, repair, task_package
from skilleval.errors import SkillEvalError
from skilleval.identity import file_digest
from skilleval.repair_assessment import DOCUMENT_CRITERIA, parse_spec

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/document/operational-metrics"


@pytest.fixture
def worker():
    spec = importlib.util.spec_from_file_location(
        "authored_metrics_observer", EXAMPLE / "worker.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_authored_alternatives_and_semantic_defects_share_mechanical_result(worker, tmp_path):
    manifest = json.loads((EXAMPLE / "controls/manifest.json").read_text())
    for control in manifest["cases"]:
        root = tmp_path / control["case_id"]
        directory = root / "docs/specs/arbitrary-subdirectory"
        directory.mkdir(parents=True)
        if control["candidate"] is not None:
            # Collection does not depend on the authored control's own filename.
            (directory / "another-design.markdown").write_bytes(
                (EXAMPLE / "controls" / control["candidate"]).read_bytes()
            )
        assert worker.observe(root) == control["expected_observation"]
    # These are author expectations for future semantic assessment, not results
    # inferred by the worker or proved by this deterministic test.
    assert manifest["independent_audit"] == "required-pending"
    assert manifest["observed_judge_results"] is None
    cases = {case["case_id"]: case for case in manifest["cases"]}
    assert cases["table-first"]["expected"] == cases["summary-first"]["expected"]
    for case in manifest["cases"]:
        assert set(case["expected"]) == set(DOCUMENT_CRITERIA)


def test_content_style_and_procedure_tokens_are_not_mechanical_gates(worker, tmp_path):
    directory = tmp_path / "docs/specs"
    directory.mkdir(parents=True)
    output = directory / "unrelated-filename.md"
    for text in (
        "A compact proposed design.",
        "Which design should I specify?",
        "Blocked; no specification delivered.",
        "TODO later",
        "# A template\n\nStatus: ready\n\nAcceptance: completed\nUse write-spec.",
        "任意の仕様書。\n",
    ):
        output.write_text(text)
        assert worker.observe(tmp_path) == {"candidate_found": True, "artifacts_readable": True}


def test_missing_empty_malformed_and_collection_limits_are_distinct_from_quality(worker, tmp_path):
    assert worker.observe(tmp_path) == {"candidate_found": False, "artifacts_readable": False}
    directory = tmp_path / "docs/specs"
    directory.mkdir(parents=True)
    output = directory / "design.md"
    for raw in (b"", b" \n\t", b"\xff\xfe", b"x" * (worker.MAX_FILE_BYTES + 1)):
        output.write_bytes(raw)
        assert worker.observe(tmp_path) == {"candidate_found": True, "artifacts_readable": False}
    output.write_text("Readable design.")
    assert worker.observe(tmp_path) == {"candidate_found": True, "artifacts_readable": True}
    (directory / "appendix.md").write_text("Another independently named supporting document.")
    assert worker.observe(tmp_path)["artifacts_readable"] is True


def test_observer_never_imports_candidate_or_reads_external_symlink(worker, tmp_path):
    directory = tmp_path / "docs/specs"
    directory.mkdir(parents=True)
    (tmp_path / "hostile.py").write_text("raise RuntimeError('must never import candidate')\n")
    (directory / "design.md").write_text("Readable design.")
    outside = tmp_path / "outside.md"
    outside.write_text("External content must not be collected through the link.")
    (directory / "external.md").symlink_to(outside)
    assert worker.observe(tmp_path) == {"candidate_found": True, "artifacts_readable": False}
    (directory / "external.md").unlink()
    assert worker.observe(tmp_path) == {"candidate_found": True, "artifacts_readable": True}


def test_worker_protocol_uses_requested_observation_and_rejects_unknown_check(tmp_path):
    directory = tmp_path / "docs/specs"
    directory.mkdir(parents=True)
    (directory / "design.md").write_text("Readable design.")
    result = subprocess.run(
        [sys.executable, str(EXAMPLE / "worker.py"), str(tmp_path)],
        input=json.dumps({"requests": [{"check": "spec-artifact"}]}),
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout) == {
        "observations": [{"candidate_found": True, "artifacts_readable": True}]
    }
    failed = subprocess.run(
        [sys.executable, str(EXAMPLE / "worker.py"), str(tmp_path)],
        input=json.dumps({"requests": [{"check": "semantic-completion"}]}),
        text=True,
        capture_output=True,
        check=False,
    )
    assert failed.returncode != 0
    assert "unknown operational-metrics document check" in failed.stderr


def test_operator_acceptance_template_binds_real_task_routes_without_execution(
    task, route, tmp_path
):
    expected = {"candidate_found": True, "artifacts_readable": True}
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
                    expected=expected,
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
    data = json.loads((EXAMPLE / "acceptance-map.template.json").read_text())
    data.update(
        task=prepared["path"],
        task_sha256=prepared["sha256"],
        package=package["path"],
        package_sha256=package["sha256"],
    )
    mapped = acceptance.AcceptanceMapSpec.model_validate_json(json.dumps(data))
    saved = acceptance.prepare_acceptance(mapped, tmp_path / "acceptance.json")
    assert saved["semantic_validity"] == "unassessed"
    assert saved["scored_admitted"] is False
    assert {r.probe_id for req in mapped.requirements for r in req.mechanical} == {"spec-artifact"}
    assert {s.criterion for req in mapped.requirements for s in req.semantic} == set(
        DOCUMENT_CRITERIA
    )
    with pytest.raises(SkillEvalError, match="outside solver inputs"):
        acceptance.prepare_acceptance(mapped, task["base"] / "leaked-map.json")
    # Its raising candidate source and old worker were never run on the host.
    assert (task["base"] / "code.py").read_text().startswith("raise RuntimeError")


def test_assessment_template_selects_versioned_profile_and_downstream_plan_keeps_authority():
    spec = parse_spec((EXAMPLE / "assessment-spec.template.json").read_bytes())
    assert spec.rubric_profile == "specification-v1"
    assert spec.tool_evidence_policy == "execution-context-v3"
    plan = json.loads((EXAMPLE / "controls/downstream-contract.json").read_text())
    assert plan["implemented_executable_coverage"] is True
    assert plan["guest_qualification"] == "required-pending-for-this-observer-generation"
    assert plan["semantic_calibration"] == "required-pending"
    assert plan["independent_audit"] == "required-pending"
    assert len({row["observation_id"] for row in plan["required_observations"]}) == len(
        plan["required_observations"]
    )
    rows = plan["api_fixture"]["metrics"]
    assert any(row["occurrences"] != row["total_value"] for row in rows)
    assert any(row["attrs"] is None for row in rows)
    assert any(row["attrs"] == {"state": "skipped_rate_limit"} for row in rows)
