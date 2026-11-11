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


def test_repository_scope_retains_unprescribed_files_for_semantic_review(task, tmp_path):
    spec = task["spec"].model_copy(update={"edit_scope": "repository", "editable_paths": ()})
    contract = tmp_path / "repository-task.json"
    prepared = repair.prepare_repair(spec, contract)
    loaded = repair.load_task(contract, prepared["sha256"])
    source = task["source"]
    (source / "README.md").write_text("ordinary documentation update\n")
    (source / "code.py").unlink()
    for name in ("new-config.json", "new-package/helper.py", "dist-extra/generated.js"):
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("retained bytes, not host execution\n")
    candidate = tmp_path / "repository-candidate"
    captured = evidence.snapshot(source, candidate)
    output = tmp_path / "repository-result"
    result = repair.collect_control(
        contract, prepared["sha256"], candidate, captured["manifest_sha256"], output
    )
    assert result["status"] == "collected"
    record, files = repair.result_files(loaded, prepared["sha256"], output, result["sha256"])
    assert record["changed_paths"] == [
        "README.md",
        "code.py",
        "dist-extra/generated.js",
        "new-config.json",
        "new-package/helper.py",
    ]
    assert record["task_quality"] == "unassessed"
    assert "code.py" not in {name for name, _, _ in files}
    assert (task["base"] / "code.py").exists()
    narrow = loaded.model_copy(
        update={"edit_scope": "declared-paths", "editable_paths": ("code.py",)}
    )
    with pytest.raises(SkillEvalError, match="retained evidence"):
        repair.result_files(narrow, prepared["sha256"], output, result["sha256"])


def test_repository_scope_does_not_grant_capture_metadata(task, tmp_path):
    spec = task["spec"].model_copy(update={"edit_scope": "repository"})
    contract = tmp_path / "repository-task.json"
    prepared = repair.prepare_repair(spec, contract)
    metadata = task["source"] / "nested/manifest.json"
    metadata.parent.mkdir()
    metadata.write_text("{}")
    candidate = tmp_path / "candidate"
    captured = evidence.snapshot(task["source"], candidate)
    result = repair.collect_control(
        contract, prepared["sha256"], candidate, captured["manifest_sha256"], tmp_path / "result"
    )
    assert result["status"] == "invalid-scope"


def test_editable_directories_allow_existing_new_and_removed_descendants(task, tmp_path):
    source = task["source"]
    (source / "src").mkdir()
    (source / "src/existing.py").write_text("old existing source\n")
    (source / "src/removed.py").write_text("source to remove\n")
    baseline = tmp_path / "directory-input"
    captured = evidence.snapshot(source, baseline)
    spec = repair.RepairSpec.model_validate_json(
        json.dumps(
            {
                **task["spec"].model_dump(mode="json"),
                "input_bundle": str(baseline),
                "input_sha256": captured["manifest_sha256"],
                "editable_directories": ["src", "new-package"],
            }
        )
    )
    contract = tmp_path / "directory-task.json"
    prepared = repair.prepare_repair(spec, contract)
    loaded = repair.load_task(contract, prepared["sha256"])
    assert loaded.editable_directories == ("src", "new-package")
    (source / "src/existing.py").write_text("revised existing source\n")
    (source / "src/removed.py").unlink()
    (source / "src/nested").mkdir()
    (source / "src/nested/helper.py").write_text("independently named helper\n")
    (source / "new-package").mkdir()
    (source / "new-package/code.py").write_text("new package\n")
    (source / "code.py").write_text("exact-path edit remains allowed\n")
    candidate = tmp_path / "directory-candidate"
    snapshot = evidence.snapshot(source, candidate)
    output = tmp_path / "directory-result"
    result = repair.collect_control(
        contract, prepared["sha256"], candidate, snapshot["manifest_sha256"], output
    )
    assert result["status"] == "collected"
    record, files = repair.result_files(loaded, prepared["sha256"], output, result["sha256"])
    assert record["changed_paths"] == [
        "code.py",
        "new-package/code.py",
        "src/existing.py",
        "src/nested/helper.py",
        "src/removed.py",
    ]
    assert "src/removed.py" not in {name for name, _, _ in files}
    # A retained result cannot borrow a broader scope during readback.
    narrow = loaded.model_copy(update={"editable_directories": ()})
    with pytest.raises(SkillEvalError, match="retained evidence"):
        repair.result_files(narrow, prepared["sha256"], output, result["sha256"])


@pytest.mark.parametrize("changed_name", ["src-other/code.py", "src", "src/nested/manifest.json"])
def test_editable_directory_rejects_siblings_replacements_and_metadata(
    task, tmp_path, changed_name
):
    spec = task["spec"].model_copy(update={"editable_directories": ("src",)})
    contract = tmp_path / "prefix-task.json"
    prepared = repair.prepare_repair(spec, contract)
    changed = task["source"] / changed_name
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_text("outside the directory grant\n")
    candidate = tmp_path / "prefix-candidate"
    captured = evidence.snapshot(task["source"], candidate)
    output = tmp_path / "prefix-result"
    result = repair.collect_control(
        contract, prepared["sha256"], candidate, captured["manifest_sha256"], output
    )
    assert result["status"] == "invalid-scope"
    retained = json.loads((output / "repair-result.json").read_text())
    assert retained["out_of_scope"] == [changed_name]


@pytest.mark.parametrize(
    "directories",
    [
        ["."],
        ["/src"],
        ["src/"],
        ["src//nested"],
        ["src/../tests"],
        ["src", "SRC"],
        [".git"],
        ["src/.git/objects"],
        ["__pycache__"],
        ["src/__pycache__/nested"],
        ["src/__PYCACHE__"],
        [".pytest_cache"],
        ["src/.pytest_cache"],
        ["manifest.json"],
        ["src/manifest.json/nested"],
        ["path with spaces"],
    ],
)
def test_editable_directory_declarations_reject_unsafe_or_metadata_paths(task, directories):
    from pydantic import ValidationError

    data = {**task["spec"].model_dump(mode="json"), "editable_directories": directories}
    with pytest.raises((ValidationError, SkillEvalError)):
        repair.RepairSpec.model_validate_json(json.dumps(data))


def test_empty_directory_scope_preserves_legacy_task_bytes_and_round_trip(task):
    legacy = {
        "schema_version": 1,
        "task_id": "authored",
        "input_bundle": str(task["base"]),
        "input_sha256": task["spec"].input_sha256,
        "prompt": "Repair the defect.",
        "editable_paths": ["code.py", "new.py"],
        "worker": task["spec"].worker,
        "worker_sha256": task["spec"].worker_sha256,
        "probes": [
            {
                "schema_version": 1,
                "probe_id": "result",
                "request": {"value": False},
                "expected": True,
            }
        ],
        "timeout_seconds": 60,
        "source_url": "https://example.invalid/authored",
        "source_revision": "v1",
    }
    assert task["spec"].model_dump(mode="json") == legacy
    assert json.loads(task["spec"].model_dump_json()) == legacy
    expected = {"schema_version": 1, "kind": "skilleval-repair-task", "spec": legacy}
    assert task["task"].read_bytes() == evidence._json_bytes(expected)
    loaded = repair.load_task(task["task"], task["digest"])
    assert loaded.editable_directories == ()
    assert loaded.model_dump(mode="json") == legacy
    explicit = repair.RepairSpec.model_validate_json(
        json.dumps({**legacy, "editable_directories": []})
    )
    assert explicit.model_dump(mode="json") == legacy


def test_directory_scope_keeps_nonempty_exact_paths_and_bounded_declarations(task):
    from pydantic import ValidationError

    data = task["spec"].model_dump(mode="json")
    with pytest.raises(ValidationError):
        repair.RepairSpec.model_validate_json(
            json.dumps({**data, "editable_paths": [], "editable_directories": ["src"]})
        )
    with pytest.raises(ValidationError):
        repair.RepairSpec.model_validate_json(
            json.dumps({**data, "editable_directories": [f"src-{n}" for n in range(101)]})
        )


@pytest.mark.parametrize("profile", ["node", "unbounded", "", None, 1, {}, []])
def test_observer_profile_is_an_explicit_fixed_choice(task, profile):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        repair.RepairSpec.model_validate_json(
            json.dumps(
                {**task["spec"].model_dump(mode="json"), "observer_resource_profile": profile}
            )
        )
    default = repair.RepairSpec.model_validate_json(
        json.dumps(
            {**task["spec"].model_dump(mode="json"), "observer_resource_profile": "python-v1"}
        )
    )
    assert default.model_dump(mode="json") == task["spec"].model_dump(mode="json")


def test_node_observer_profile_reaches_guest_report_and_readback(
    task, route, tmp_path, monkeypatch
):
    import tarfile

    from skilleval.repair_assessment import (
        RepairAssessmentSpec,
        RepairCheckSource,
        checked_report,
        packet,
    )

    spec = task["spec"].model_copy(update={"observer_resource_profile": "node-v1"})
    contract = tmp_path / "node-task.json"
    prepared = repair.prepare_repair(spec, contract)
    assert repair.load_task(contract, prepared["sha256"]).observer_resource_profile == "node-v1"
    candidate = tmp_path / "node-control"
    result = repair.collect_control(
        contract, prepared["sha256"], task["base"], spec.input_sha256, candidate
    )
    output = tmp_path / "node-check"
    report = check_repair(
        contract,
        prepared["sha256"],
        candidate,
        result["sha256"],
        output,
        runtime=route["runtime"],
        image=route["image"],
        image_sha256=route["image_sha256"],
    )
    assert report["status"] == "completed"
    resources = {
        "profile": "node-v1",
        "address_space_bytes": 2147483648,
        "file_size_bytes": 8388608,
        "open_files": 256,
        "processes": 32,
        "core_bytes": 0,
        "output_bytes_per_stream": 131072,
    }
    assert report["observer_resources"] == report["process"]["observer_resources"] == resources
    with tarfile.open(output / "runtime-state/checker-input.tar") as archive:
        settings = json.loads(archive.extractfile("native/requests.json").read())
    assert settings == {
        "timeout_seconds": 60,
        "requests": [{"value": False}],
        "observer_resource_profile": "node-v1",
    }
    source = {
        "task": str(contract),
        "task_sha256": prepared["sha256"],
        "result": str(candidate),
        "result_sha256": result["sha256"],
    }
    report_path = output / "repair-check.json"
    reference = RepairCheckSource(
        **source, report=str(report_path), report_sha256=file_digest(report_path)
    )
    assert checked_report(reference) == report
    assessment = RepairAssessmentSpec(
        **source, checks=(reference,), authored_statement="Authored control."
    )
    payloads, _, _ = packet(assessment)
    observed = json.loads(payloads["case/checks/check-001/observations.json"])
    assert observed["observer_resources"] == resources
    from skilleval.lifecycle import Lifecycle

    original_guest = Lifecycle.guest

    def missing_profile(session, label, *args, **kwargs):
        response = original_guest(session, label, *args, **kwargs)
        if label == "observe_candidate":
            envelope = json.loads(response["stdout"])
            del envelope["observer_resources"]
            response["stdout"] = json.dumps(envelope)
        return response

    monkeypatch.setattr(Lifecycle, "guest", missing_profile)
    incomplete = check_repair(
        contract,
        prepared["sha256"],
        candidate,
        result["sha256"],
        tmp_path / "missing-profile-check",
        runtime=route["runtime"],
        image=route["image"],
        image_sha256=route["image_sha256"],
    )
    assert incomplete["status"] == "incomplete"
    assert incomplete["accepted_by_checks"] is None
    assert incomplete["checks"] == []
    assert "resource profile" in incomplete["error"]
    for target, fault in (
        ("report", "missing"),
        ("process", "missing"),
        ("process", "wrong-file-limit"),
    ):
        changed = json.loads(json.dumps(report))
        location = changed if target == "report" else changed["process"]
        if fault == "missing":
            del location["observer_resources"]
        else:
            location["observer_resources"]["file_size_bytes"] = 131072
        report_path.write_text(json.dumps(changed))
        altered = reference.model_copy(update={"report_sha256": file_digest(report_path)})
        with pytest.raises(SkillEvalError, match="resource profile"):
            checked_report(altered)


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
    assert "observer_resources" not in report
    assert "observer_resources" not in report["process"]
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
