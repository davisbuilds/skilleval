"""Preparation tests keep target application bytes inert on the host."""

import importlib.util
import json
from pathlib import Path

import pytest
from test_repair import task as task
from test_runner import route as route
from test_task_package import package_spec

from skilleval import acceptance, evidence, repair, task_package
from skilleval.errors import SkillEvalError

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/document/operational-metrics"


@pytest.fixture
def helper():
    spec = importlib.util.spec_from_file_location(
        "implementation_preparation", EXAMPLE / "prepare_implementation.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def prepared(helper, task, route, tmp_path):
    source = tmp_path / "source-with-frontend"
    repair._write(source, repair._inputs(task["spec"]))
    for name in ("frontend/package.json", "src/api/v2/router.ts", "src/db/v2-queries.ts"):
        file = source / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("inert authored source")
    captured = evidence.snapshot(source, tmp_path / "baseline-with-frontend")
    changed = task["spec"].model_copy(
        update={
            "input_bundle": str(tmp_path / "baseline-with-frontend"),
            "input_sha256": captured["manifest_sha256"],
        }
    )
    frozen = repair.prepare_repair(changed, tmp_path / "source-task.json")
    task = {
        **task,
        "task": Path(frozen["path"]),
        "digest": frozen["sha256"],
        "spec": changed,
        "base": tmp_path / "baseline-with-frontend",
    }
    package = task_package.prepare_package(
        package_spec(task, route), tmp_path / "original-package.json"
    )
    return helper.prepare_contract(
        Path(package["path"]),
        package["sha256"],
        task["base"],
        task["spec"].input_sha256,
        "Implement the requested read-only feature.",
        tmp_path / "prepared",
    )


def test_implementation_requirements_use_original_need_and_repair_semantics(prepared, task):
    ref = prepared["acceptance_map"]
    mapped = acceptance.load_acceptance(Path(ref["path"]), ref["sha256"])
    contract = json.loads((EXAMPLE / "controls/downstream-contract.json").read_text())
    assert {row.requirement_id for row in mapped.requirements} == {
        row["observation_id"] for row in contract["required_observations"]
    }
    assert {r.criterion for row in mapped.requirements for r in row.semantic} == {
        "task-contract",
        "scope-and-regressions",
    }
    assert "cannot redefine success" in mapped.purpose
    assert prepared["original_request"] == task["spec"].prompt
    assert prepared["scored_admitted"] is False
    assert "raise RuntimeError" in (task["base"] / "code.py").read_text()
    assert mapped.unmapped_probes[0].probe_id == "source-present"


def test_preparation_cannot_overwrite_or_leak_into_solver_input(helper, task, route, tmp_path):
    package = task_package.prepare_package(
        package_spec(task, route), tmp_path / "original-package.json"
    )
    args = (
        Path(package["path"]),
        package["sha256"],
        task["base"],
        task["spec"].input_sha256,
        "Implement.",
    )
    with pytest.raises(SkillEvalError, match="outside solver input"):
        helper.prepare_contract(*args, task["base"] / "leaked")
    helper.prepare_contract(*args, tmp_path / "fresh")
    with pytest.raises(FileExistsError):
        helper.prepare_contract(*args, tmp_path / "fresh")


def test_generated_build_and_test_files_are_retained_but_sibling_directory_is_rejected(
    prepared, tmp_path
):
    ref = prepared["task"]
    contract = repair.load_task(Path(ref["path"]), ref["sha256"])
    candidate = tmp_path / "generated-source"
    repair._write(candidate, repair._inputs(contract))
    generated = {
        "dist/index.js": b"export const built = true;\n",
        "node_modules/.vite/results.json": b'{"cached": true}\n',
        "playwright-report/index.html": b"<html>Retained test report</html>\n",
        "test-results/.last-run.json": b'{"status": "passed"}\n',
    }
    for name, raw in generated.items():
        path = candidate / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    captured = evidence.snapshot(candidate, tmp_path / "generated-candidate")
    result = repair.collect_control(
        Path(ref["path"]),
        ref["sha256"],
        tmp_path / "generated-candidate",
        captured["manifest_sha256"],
        tmp_path / "generated-result",
    )
    assert result["status"] == "collected"
    record, files = repair.result_files(
        contract, ref["sha256"], Path(result["path"]), result["sha256"]
    )
    assert record["candidate_sha256"] == captured["manifest_sha256"]
    retained = {name: raw for name, raw, _ in files}
    assert {name: retained[name] for name in generated} == generated

    unrelated = candidate / "dist-extra/output.js"
    unrelated.parent.mkdir()
    unrelated.write_text("unlisted sibling directory\n")
    captured = evidence.snapshot(candidate, tmp_path / "unlisted-candidate")
    result = repair.collect_control(
        Path(ref["path"]),
        ref["sha256"],
        tmp_path / "unlisted-candidate",
        captured["manifest_sha256"],
        tmp_path / "unlisted-result",
    )
    assert result["status"] == "invalid-scope"
    record = json.loads((Path(result["path"]) / "repair-result.json").read_bytes())
    assert record["out_of_scope"] == ["dist-extra/output.js"]
    with pytest.raises(SkillEvalError, match="outside the frozen scope"):
        repair.result_files(contract, ref["sha256"], Path(result["path"]), result["sha256"])


def test_observation_package_binds_candidate_and_retains_nonoracle_status(
    helper, prepared, task, tmp_path, monkeypatch
):
    taskref, package = prepared["task"], prepared["package"]
    contract = repair.load_task(Path(taskref["path"]), taskref["sha256"])
    result = repair.collect_control(
        Path(taskref["path"]),
        taskref["sha256"],
        Path(contract.input_bundle),
        contract.input_sha256,
        tmp_path / "candidate-result",
    )
    from types import SimpleNamespace

    # This tests packing, not browser behavior; the real lane helper is qualified separately.
    monkeypatch.setattr(
        helper,
        "_helper",
        lambda: SimpleNamespace(
            lane_argv=lambda *args, **kwargs: ("python3", "-I", "-c", "print(1)")
        ),
    )
    saved = helper.prepare_observation(
        Path(taskref["path"]),
        taskref["sha256"],
        Path(result["path"]),
        result["sha256"],
        Path(package["path"]),
        package["sha256"],
        tmp_path / "driver.json",
        tmp_path / "observation-package.json",
    )
    selected = task_package.load_package(Path(saved["path"]), saved["sha256"])
    assert selected.environment.profile == "solver-isolated-v1"
    record, _ = repair.result_files(
        contract, taskref["sha256"], Path(result["path"]), result["sha256"]
    )
    assert selected.validation[1].input_sha256 == record["candidate_sha256"]
    assert selected.validation[1].lanes[0].expected == {}
    assert "not feature correctness" in " ".join(selected.limitations)
