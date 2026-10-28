"""Repair byte and protocol contracts; synthetic runtimes do not prove isolation."""

import json
from pathlib import Path
from typing import Any

import pytest
from test_runner import route as route

from skilleval import cli, evidence, repair, runner
from skilleval.errors import SkillEvalError
from skilleval.identity import file_digest
from skilleval.repair_check import check_repair, compare_observations


@pytest.fixture
def task(tmp_path: Path) -> dict[str, Any]:
    source = tmp_path / "source"
    source.mkdir()
    (source / "code.py").write_text("raise RuntimeError('must never execute on host')\n")
    (source / "README.md").write_text("ordinary context\n")
    (source / "scenario.json").write_text(
        json.dumps(
            {
                "fault": "",
                "repair_files": {"code.py": "repaired = True\n", "new.py": "new = True\n"},
            }
        )
    )
    frozen = tmp_path / "input"
    captured = evidence.snapshot(source, frozen)
    worker = tmp_path / "worker.py"
    worker.write_text("raise RuntimeError('VM-only explicit checker')\n")
    spec = repair.RepairSpec(
        task_id="authored",
        input_bundle=str(frozen),
        input_sha256=captured["manifest_sha256"],
        prompt="Repair the defect.",
        editable_paths=("code.py", "new.py"),
        worker=str(worker),
        worker_sha256=file_digest(worker),
        probes=(repair.RepairProbe(probe_id="result", request={"value": False}, expected=True),),
        source_url="https://example.invalid/authored",
        source_revision="v1",
    )
    plan = tmp_path / "task.json"
    receipt = repair.prepare_repair(spec, plan)
    return {
        "task": plan,
        "digest": receipt["sha256"],
        "spec": spec,
        "source": source,
        "base": frozen,
    }


def test_collects_actual_bytes_and_git_patch_without_running_candidate(task, route, tmp_path):
    run = tmp_path / "native"
    spec = task["spec"]
    result = runner.run_native(
        task["base"], spec.input_sha256, spec.prompt, run, mode="repair", **route
    )
    assert result["status"] == "completed"
    out = tmp_path / "collected"
    receipt = repair.collect_repair(task["task"], task["digest"], run, out)
    assert receipt["status"] == "collected"
    assert receipt["changed_paths"] == ["code.py", "new.py"]
    patch = (out / "changes.patch").read_text()
    assert "repaired = True" in patch and "new file mode 100644" in patch
    assert (out / "candidate/code.py").read_text() == "repaired = True\n"
    record, _ = repair.result_files(spec, task["digest"], out, receipt["sha256"])
    assert (
        record["provenance"]["kind"] == "native-repair" and record["task_quality"] == "unassessed"
    )
    assert (task["base"] / "code.py").read_text().startswith("raise RuntimeError")


def test_timed_out_repair_retains_candidate_without_claiming_completion(task, route, tmp_path):
    scenario = task["source"] / "scenario.json"
    value = json.loads(scenario.read_text())
    value["fault"] = "timed-out"
    scenario.write_text(json.dumps(value))
    frozen = tmp_path / "timeout-input"
    snapshot = evidence.snapshot(task["source"], frozen)
    spec = task["spec"].model_copy(
        update={
            "input_bundle": str(frozen),
            "input_sha256": snapshot["manifest_sha256"],
        }
    )
    contract = tmp_path / "timeout-task.json"
    prepared = repair.prepare_repair(spec, contract)
    run = tmp_path / "native"
    result = runner.run_native(frozen, spec.input_sha256, spec.prompt, run, mode="repair", **route)
    assert result["status"] == "timed-out"
    output = tmp_path / "timeout-result"
    collected = repair.collect_repair(contract, prepared["sha256"], run, output)
    assert collected["status"] == "collected"
    record = json.loads((output / "repair-result.json").read_text())
    assert record["provenance"]["execution_status"] == "timed-out"
    assert record["task_quality"] == "unassessed"


def test_out_of_scope_control_is_retained_but_cannot_be_checked(task, tmp_path):
    (task["source"] / "README.md").write_text("changed outside scope\n")
    candidate = tmp_path / "changed"
    captured = evidence.snapshot(task["source"], candidate)
    output = tmp_path / "control"
    result = repair.collect_control(
        task["task"], task["digest"], candidate, captured["manifest_sha256"], output
    )
    assert result["status"] == "invalid-scope"
    assert "README.md" in (output / "changes.patch").read_text()
    with pytest.raises(SkillEvalError, match="outside"):
        repair.result_files(task["spec"], task["digest"], output, result["sha256"])


def test_control_and_offline_checker_keep_expected_answers_on_host(task, route, tmp_path):
    result_dir = tmp_path / "control"
    receipt = repair.collect_control(
        task["task"], task["digest"], task["base"], task["spec"].input_sha256, result_dir
    )
    report = check_repair(
        task["task"],
        task["digest"],
        result_dir,
        receipt["sha256"],
        tmp_path / "checks",
        runtime=route["runtime"],
        image=route["image"],
        image_sha256=route["image_sha256"],
    )
    assert report["status"] == "completed" and report["cleanup_complete"]
    assert report["accepted_by_checks"] is False
    assert report["checks"] == [
        {"probe_id": "result", "observed": False, "expected": True, "pass": False}
    ]
    assert report["task_quality"] == "unassessed" and report["inference_requests"] == 0


def test_missing_or_forged_observation_protocol_cannot_claim_pass():
    probes = [{"probe_id": "one", "expected": True}]
    for raw in (
        '{"observations":[]}',
        '{"observations":[true],"pass":true}',
        '{"observations":[true],"observations":[false]}',
    ):
        with pytest.raises(SkillEvalError):
            compare_observations(probes, raw)
    assert not compare_observations(probes, '{"observations":[1]}')[0]["pass"]
    assert compare_observations(probes, '{"observations":[true]}')[0]["pass"]


def test_changed_worker_or_result_is_refused_before_vm_creation(task, tmp_path):
    candidate = tmp_path / "control"
    receipt = repair.collect_control(
        task["task"], task["digest"], task["base"], task["spec"].input_sha256, candidate
    )
    (candidate / "candidate/code.py").chmod(0o600)
    (candidate / "candidate/code.py").write_text("tampered")
    with pytest.raises(SkillEvalError):
        repair.result_files(task["spec"], task["digest"], candidate, receipt["sha256"])
    Path(task["spec"].worker).write_text("changed checker")
    with pytest.raises(SkillEvalError, match="worker digest"):
        repair.load_task(task["task"], task["digest"])


def test_patch_preserves_binary_deletion_and_executable_changes():
    before = [
        ("bin.dat", b"\x00old", False),
        ("gone.py", b"old\n", False),
        ("run.sh", b"exit 0\n", False),
    ]
    after = [("bin.dat", b"\x00new", False), ("run.sh", b"exit 0\n", True)]
    patch = repair._patch(before, after)
    assert b"GIT binary patch" in patch
    assert b"deleted file mode 100644" in patch
    assert b"new mode 100755" in patch


def test_unsupported_result_version_is_not_reinterpreted(task, tmp_path):
    output = tmp_path / "control"
    repair.collect_control(
        task["task"], task["digest"], task["base"], task["spec"].input_sha256, output
    )
    path = output / "repair-result.json"
    value = json.loads(path.read_text())
    value["schema_version"] = 99
    path.write_text(json.dumps(value))
    with pytest.raises(SkillEvalError, match="unsupported"):
        repair.result_files(task["spec"], task["digest"], output, file_digest(path))


def test_checker_refuses_output_inside_runtime_before_creating_it(task, route, tmp_path):
    result_dir = tmp_path / "control"
    receipt = repair.collect_control(
        task["task"], task["digest"], task["base"], task["spec"].input_sha256, result_dir
    )
    output = route["runtime"].parent / "checker-output"
    with pytest.raises(SkillEvalError, match="outside"):
        check_repair(
            task["task"],
            task["digest"],
            result_dir,
            receipt["sha256"],
            output,
            runtime=route["runtime"],
            image=route["image"],
            image_sha256=route["image_sha256"],
        )
    assert not output.exists()


def test_cli_failed_checks_and_scope_violations_fail_shell(task, route, tmp_path, capsys):
    result_dir = tmp_path / "control"
    receipt = repair.collect_control(
        task["task"], task["digest"], task["base"], task["spec"].input_sha256, result_dir
    )
    assert (
        cli.main(
            [
                "repair",
                "check",
                str(task["task"]),
                "--task-sha256",
                task["digest"],
                "--result",
                str(result_dir),
                "--result-sha256",
                receipt["sha256"],
                "--runtime",
                str(route["runtime"]),
                "--image-archive",
                str(route["image"]),
                "--image-sha256",
                route["image_sha256"],
                "--output",
                str(tmp_path / "checks"),
            ]
        )
        == 1
    )
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "completed" and report["accepted_by_checks"] is False
    (task["source"] / "README.md").write_text("out of scope")
    candidate = tmp_path / "changed"
    snapshot = evidence.snapshot(task["source"], candidate)
    assert (
        cli.main(
            [
                "repair",
                "control",
                str(task["task"]),
                "--task-sha256",
                task["digest"],
                "--candidate",
                str(candidate),
                "--candidate-sha256",
                snapshot["manifest_sha256"],
                "--output",
                str(tmp_path / "invalid-scope"),
            ]
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out)["status"] == "invalid-scope"


@pytest.mark.parametrize("original,revised", [(b"old\r\n", b"old\n"), (b"old\n", b"old\r\n")])
def test_patch_preserves_raw_bytes_despite_source_attributes(original, revised, tmp_path):
    from skilleval.workspace import _git

    attributes = (".gitattributes", b"*.txt text\n", False)
    before = [attributes, ("nested/file.txt", original, False)]
    after = [attributes, ("nested/file.txt", revised, False)]
    patch = repair._patch(before, after)
    assert patch, "line-ending changes must remain visible"
    repair._write(tmp_path, before)
    _git(tmp_path, "init", "--quiet", "--template=")
    _git(tmp_path, "apply", "--whitespace=nowarn", "-", data=patch)
    assert (tmp_path / "nested/file.txt").read_bytes() == revised


@pytest.mark.parametrize("reverse", [False, True])
def test_patch_round_trips_file_directory_replacements(tmp_path, reverse):
    from skilleval.workspace import _git

    before, after = [("pkg/code.py", b"old\n", False)], [("pkg", b"new\n", False)]
    if reverse:
        before, after = after, before
    patch = repair._patch(before, after)
    repair._write(tmp_path, before)
    _git(tmp_path, "init", "--quiet", "--template=")
    _git(tmp_path, "apply", "--whitespace=nowarn", "-", data=patch)
    assert (tmp_path / after[0][0]).read_bytes() == after[0][1]


def test_unsupported_context_path_is_refused_at_preparation(task, tmp_path):
    (task["source"] / "Design Notes.md").write_text("ordinary context")
    source = tmp_path / "unsupported"
    captured = evidence.snapshot(task["source"], source)
    spec = task["spec"].model_copy(
        update={
            "input_bundle": str(source),
            "input_sha256": captured["manifest_sha256"],
        }
    )
    output = tmp_path / "unsupported-task.json"
    with pytest.raises(SkillEvalError, match="unsupported"):
        repair.prepare_repair(spec, output)
    assert not output.exists()


def test_collection_accepts_verified_recovery_without_rewriting_native_outcome(
    task, route, tmp_path
):
    from skilleval.lifecycle import recover

    scenario = task["source"] / "scenario.json"
    value = json.loads(scenario.read_text())
    value["fault"] = "stop-failure"
    scenario.write_text(json.dumps(value))
    frozen = tmp_path / "recovery-input"
    captured = evidence.snapshot(task["source"], frozen)
    spec = task["spec"].model_copy(
        update={
            "input_bundle": str(frozen),
            "input_sha256": captured["manifest_sha256"],
        }
    )
    contract = tmp_path / "recovery-task.json"
    prepared = repair.prepare_repair(spec, contract)
    run = tmp_path / "native"
    result = runner.run_native(frozen, spec.input_sha256, spec.prompt, run, mode="repair", **route)
    assert result["status"] == "partial" and result["cleanup_complete"] is False
    output = tmp_path / "recovered-result"
    with pytest.raises(SkillEvalError, match="cleanup"):
        repair.collect_repair(contract, prepared["sha256"], run, output)
    (run / "runtime-state/scenario.json").write_text("{}")
    assert recover(run)["cleanup_complete"]
    original_receipt = file_digest(run / "native-run.json")
    collected = repair.collect_repair(contract, prepared["sha256"], run, output)
    assert collected["status"] == "collected"
    record = json.loads((output / "repair-result.json").read_text())
    assert record["provenance"]["execution_status"] == "partial"
    assert record["provenance"]["native_cleanup_complete"] is False
    assert record["provenance"]["cleanup_complete"] is True
    assert record["provenance"]["recovery_sha256"] == file_digest(run / "recovery.json")
    assert file_digest(run / "native-run.json") == original_receipt
