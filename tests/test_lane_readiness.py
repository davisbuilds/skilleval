"""Pinned lane receipt replay; authored process controls, no VM isolation proof."""

import json
import shutil
from pathlib import Path

import pytest
from test_lane_validation import (
    creations,
    independent_package,
    remove_runtime_home,
)
from test_lane_validation import (
    validation_setup as validation_setup,
)
from test_qualification import docker_archive
from test_repair import task as task
from test_runner import route as route

from skilleval import cli, evidence, identity, lane_readiness, repair, task_package
from skilleval.errors import SkillEvalError
from skilleval.identity import file_digest


def test_lane_contract_distinguishes_executed_argv_and_timeout(validation_setup, tmp_path):
    spec, _, _, _ = independent_package(validation_setup, tmp_path / "independent.json")
    case = spec.validation[0]
    lane = case.lanes[0]
    original = lane_readiness.lane_contract(spec, case, lane)
    changed_argv = lane.model_copy(update={"argv": ("authored-lane", "changed")})
    changed_timeout = lane.model_copy(update={"timeout_seconds": lane.timeout_seconds + 1})
    assert lane_readiness.lane_contract(spec, case, changed_argv) != original
    assert lane_readiness.lane_contract(spec, case, changed_timeout) != original


def publish_spec(spec, destination):
    digest = repair._publish(
        destination,
        {
            "schema_version": 1,
            "kind": "skilleval-task-package",
            "spec": spec.model_dump(mode="json"),
        },
    )
    return destination, digest


@pytest.fixture
def retained(validation_setup, tmp_path):
    spec, package, digest, runtime = independent_package(
        validation_setup, tmp_path / "independent.json"
    )
    directory = tmp_path / "original"
    report = task_package.validate_environment(package, digest, directory, runtime=runtime)
    path = directory / "task-validation.json"
    assert report["status"] == "matched"
    assert len(creations(runtime)) == 2
    return spec, package, digest, runtime, path, file_digest(path), report


def plan(retained, package=None, digest=None):
    _, original, original_digest, runtime, prior, prior_digest, _ = retained
    return lane_readiness.plan_validation(
        package or original, digest or original_digest, runtime, prior, prior_digest
    )[1]


def changed_lane(spec, **updates):
    case = spec.validation[0]
    return spec.model_copy(
        update={
            "validation": (
                case.model_copy(
                    update={"lanes": (case.lanes[0], case.lanes[1].model_copy(update=updates))}
                ),
            )
        }
    )


def test_report_readback_resolves_original_package_and_single_lane_execution(retained):
    spec, _, _, _, path, digest, report = retained
    assert lane_readiness.read_report(path, digest) == (spec, report)
    for selected, original_lane in zip(
        report["cases"][0]["lanes"], spec.validation[0].lanes, strict=True
    ):
        original, case, lane, row = lane_readiness.read_execution(selected["source"])
        assert original == spec
        assert case == spec.validation[0]
        assert lane == original_lane
        assert row["cleanup_complete"] is True
        assert row["status"] == "matched"
        assert [item["lane_id"] for item in row["lanes"]] == [lane.lane_id]
        assert selected["readiness_identity"] == row["readiness_identity"]
    selected_spec, checked = task_package.checked_validation(retained[1], retained[2], path, digest)
    assert selected_spec == spec
    assert checked == report


def test_study_identity_readback_uses_each_lane_execution_footprint(retained):
    spec, _, _, runtime, _, _, report = retained
    current = identity.snapshot_identity(str(runtime))
    task_package.check_environment_identity(report, spec.environment.profile, current)
    current["sources"]["src/skilleval/study.py"] = "f" * 64
    task_package.check_environment_identity(report, spec.environment.profile, current)
    current["sources"]["src/skilleval/lane_validation.py"] = "f" * 64
    with pytest.raises(SkillEvalError, match="execution identity changed"):
        task_package.check_environment_identity(report, spec.environment.profile, current)


@pytest.mark.parametrize(
    "updates", [{"argv": ("authored-lane", "changed")}, {"timeout_seconds": 61}]
)
def test_plan_executes_only_lane_with_changed_execution_contract(retained, tmp_path, updates):
    package, digest = publish_spec(changed_lane(retained[0], **updates), tmp_path / "changed.json")
    rows = plan(retained, package, digest)
    assert [row["action"] for row in rows] == ["reuse", "execute"]
    assert rows[0]["source"] == retained[-1]["cases"][0]["lanes"][0]["source"]
    assert rows[1]["source"] is None
    with pytest.raises(SkillEvalError):
        task_package.checked_validation(package, digest, retained[4], retained[5])
    assert len(creations(retained[3])) == 2


def test_new_expectations_reuse_raw_bytes_but_replay_selected_verdict(retained, tmp_path):
    spec, _, _, runtime, prior, prior_digest, original_report = retained
    changed = changed_lane(spec, expected={"prior_lanes": 1})
    package, digest = publish_spec(changed, tmp_path / "different-expectations.json")
    assert [row["action"] for row in plan(retained, package, digest)] == ["reuse", "reuse"]
    original_bytes = prior.read_bytes()
    output = tmp_path / "reinterpreted"
    report = task_package.validate_environment(
        package, digest, output, runtime=runtime, reuse=prior, reuse_sha256=prior_digest
    )
    assert report["status"] == "incomplete"
    assert [lane["comparison"]["status"] for lane in report["cases"][0]["lanes"]] == [
        "matched",
        "unexpected",
    ]
    assert [lane["source"] for lane in report["cases"][0]["lanes"]] == [
        lane["source"] for lane in original_report["cases"][0]["lanes"]
    ]
    assert prior.read_bytes() == original_bytes
    assert len(creations(runtime)) == 2
    with pytest.raises(SkillEvalError):
        task_package.checked_validation(package, digest, prior, prior_digest)


def test_parser_and_brief_changes_reuse_execution_and_keep_original_disclosures(
    retained, task, tmp_path
):
    spec, _, _, runtime, prior, prior_digest, report = retained
    original_task = task["spec"]
    new_task = original_task.model_copy(
        update={"prompt": "Repair the defect with clearer documentation."}
    )
    task_path = tmp_path / "new-task.json"
    task_digest = repair.prepare_repair(new_task, task_path)["sha256"]
    changed = changed_lane(spec, parser="unittest", purpose="new interpretation")
    changed = changed.model_copy(
        update={
            "task": str(task_path),
            "task_sha256": task_digest,
            "limitations": ("New selected limitation.",),
            "environment": changed.environment.model_copy(
                update={"coverage_gaps": ("New selected coverage gap.",)}
            ),
        }
    )
    package, digest = publish_spec(changed, tmp_path / "new-brief-parser.json")
    assert [row["action"] for row in plan(retained, package, digest)] == ["reuse", "reuse"]
    original_receipts = {
        lane["source"]["receipt"]: Path(lane["source"]["receipt"]).read_bytes()
        for lane in report["cases"][0]["lanes"]
    }
    selected = task_package.validate_environment(
        package,
        digest,
        tmp_path / "new-interpretation",
        runtime=runtime,
        reuse=prior,
        reuse_sha256=prior_digest,
    )
    assert selected["cases"][0]["lanes"][1]["comparison"]["status"] == "incomplete"
    assert len(creations(runtime)) == 2
    assert {name: Path(name).read_bytes() for name in original_receipts} == original_receipts
    original, _, _, _ = lane_readiness.read_execution(selected["cases"][0]["lanes"][1]["source"])
    assert original.limitations == spec.limitations
    assert original.environment.coverage_gaps == spec.environment.coverage_gaps
    assert original.task == spec.task
    assert selected["limitations"] == ["New selected limitation."]
    assert selected["coverage_gaps"] == ["New selected coverage gap."]


@pytest.mark.parametrize("dependency", ["image", "runtime", "execution-source"])
def test_changed_effective_execution_dependency_invalidates_all_lanes(
    retained, tmp_path, monkeypatch, dependency
):
    spec, package, digest, runtime, *_ = retained
    assert [row["action"] for row in plan(retained)] == ["reuse", "reuse"]
    if dependency == "image":
        image, image_digest = docker_archive(
            tmp_path / "changed-image", ("oci-layout", b'{"imageLayoutVersion":"1.0.0"}')
        )
        spec = spec.model_copy(
            update={
                "environment": spec.environment.model_copy(
                    update={"image_archive": str(image), "image_sha256": image_digest}
                )
            }
        )
        package, digest = publish_spec(spec, tmp_path / "new-image.json")
    elif dependency == "runtime":
        (runtime.parent / "smolvm-bin").write_text("changed fixture binary bytes")
    else:
        source_root = Path(identity.__file__).resolve().parents[2]
        copied = tmp_path / "source-copy"
        for name in identity.SOURCES:
            target = copied / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_root / name, target)
        monkeypatch.setattr(identity, "__file__", str(copied / "src/skilleval/identity.py"))
        assert [row["action"] for row in plan(retained)] == ["reuse", "reuse"]
        with (copied / "src/skilleval/task_environment.py").open("a") as stream:
            stream.write("\n# authored execution-source drift\n")
    assert [row["action"] for row in plan(retained, package, digest)] == ["execute", "execute"]
    assert len(creations(runtime)) == 2


def test_changed_reference_input_invalidates_only_its_case(retained, task, tmp_path):
    # The original report has no reference execution; add one without changing
    # the baseline, then change only that reference's snapshot on the next plan.
    spec, _, _, runtime, prior, prior_digest, _ = retained
    case = spec.validation[0]
    reference = case.model_copy(
        update={"case_id": "reference", "role": "reference", "lanes": (case.lanes[0],)}
    )
    spec = spec.model_copy(update={"validation": (case, reference)})
    package, digest = publish_spec(spec, tmp_path / "with-reference.json")
    out = tmp_path / "reference-original"
    report = task_package.validate_environment(
        package, digest, out, runtime=runtime, reuse=prior, reuse_sha256=prior_digest
    )
    assert [lane["action"] for lane in report["cases"][0]["lanes"]] == ["reuse", "reuse"]
    assert report["cases"][1]["lanes"][0]["action"] == "execute"
    (task["source"] / "README.md").write_text("new ordinary reference context\n")
    new_input = tmp_path / "reference-input"
    snapshot = evidence.snapshot(task["source"], new_input)
    changed = spec.model_copy(
        update={
            "validation": (
                case,
                reference.model_copy(
                    update={
                        "input_bundle": str(new_input),
                        "input_sha256": snapshot["manifest_sha256"],
                    }
                ),
            )
        }
    )
    package, digest = publish_spec(changed, tmp_path / "new-reference.json")
    _, rows = lane_readiness.plan_validation(
        package,
        digest,
        runtime,
        out / "task-validation.json",
        file_digest(out / "task-validation.json"),
    )
    assert [(row["case_id"], row["action"]) for row in rows] == [
        ("base", "reuse"),
        ("base", "reuse"),
        ("reference", "execute"),
    ]


@pytest.mark.parametrize(
    "artifact, mutation",
    [("raw", "missing"), ("raw", "tamper"), ("receipt", "missing"), ("receipt", "tamper")],
)
def test_broken_retained_chain_refuses_before_creating_output(
    retained, tmp_path, artifact, mutation
):
    _, package, digest, runtime, prior, prior_digest, report = retained
    receipt = Path(report["cases"][0]["lanes"][0]["source"]["receipt"])
    target = receipt if artifact == "receipt" else receipt.parent / "lane-first.json"
    if mutation == "missing":
        target.unlink()
    else:
        value = json.loads(target.read_text())
        value["authored_tamper"] = True
        target.write_text(json.dumps(value))
    output = tmp_path / "must-not-exist"
    with pytest.raises(SkillEvalError):
        task_package.validate_environment(
            package, digest, output, runtime=runtime, reuse=prior, reuse_sha256=prior_digest
        )
    assert not output.exists()
    assert len(creations(runtime)) == 2


@pytest.mark.parametrize("mutation", ["bool-schema", "duplicate-lane", "missing-source"])
def test_rehashed_malformed_aggregate_cannot_select_reuse(retained, tmp_path, mutation):
    _, package, digest, runtime, _, _, original = retained
    report = json.loads(json.dumps(original))
    if mutation == "bool-schema":
        report["schema_version"] = True
    elif mutation == "duplicate-lane":
        report["cases"][0]["lanes"][1] = report["cases"][0]["lanes"][0]
    else:
        del report["cases"][0]["lanes"][0]["source"]
    path = tmp_path / "malformed.json"
    report_digest = repair._publish(path, report)
    output = tmp_path / "must-not-exist"
    with pytest.raises(SkillEvalError):
        task_package.validate_environment(
            package, digest, output, runtime=runtime, reuse=path, reuse_sha256=report_digest
        )
    assert not output.exists()
    assert len(creations(runtime)) == 2


def test_reuse_sources_stay_flat_across_generations(retained, tmp_path):
    _, package, digest, runtime, prior, prior_digest, original = retained
    sources = [lane["source"] for lane in original["cases"][0]["lanes"]]
    for generation in range(2):
        directory = tmp_path / f"generation-{generation}"
        report = task_package.validate_environment(
            package, digest, directory, runtime=runtime, reuse=prior, reuse_sha256=prior_digest
        )
        assert [lane["source"] for lane in report["cases"][0]["lanes"]] == sources
        prior = directory / "task-validation.json"
        prior_digest = file_digest(prior)
        assert lane_readiness.read_report(prior, prior_digest)[1] == report
        assert not (directory / "executions").exists()
    assert len(creations(runtime)) == 2


def test_legacy_shared_case_report_cannot_be_promoted_to_lane_reuse(validation_setup, tmp_path):
    _, legacy, legacy_digest, runtime = validation_setup
    old = tmp_path / "legacy"
    report = task_package.validate_environment(legacy, legacy_digest, old, runtime=runtime)
    assert report["schema_version"] == 2
    assert len(creations(runtime)) == 1
    _, package, digest, _ = independent_package(validation_setup, tmp_path / "independent.json")
    output = tmp_path / "must-not-exist"
    with pytest.raises(SkillEvalError):
        task_package.validate_environment(
            package,
            digest,
            output,
            runtime=runtime,
            reuse=old / "task-validation.json",
            reuse_sha256=file_digest(old / "task-validation.json"),
        )
    assert not output.exists()
    assert len(creations(runtime)) == 1


@pytest.mark.parametrize("failure", ["timeout", "cleanup-failure"])
def test_incomplete_execution_is_retained_but_never_reused(validation_setup, tmp_path, failure):
    spec, _, _, runtime = independent_package(
        validation_setup, tmp_path / "failure-package.json", argv=("authored-lane", failure)
    )
    if failure == "timeout":
        content = runtime.read_text()
        needle = "'timed_out':False"
        assert needle in content
        runtime.write_text(content.replace(needle, "'timed_out':spec['argv'][1]=='timeout'"))
    package, digest = publish_spec(spec, tmp_path / "failure-selected.json")
    output = tmp_path / "incomplete-original"
    try:
        report = task_package.validate_environment(package, digest, output, runtime=runtime)
        assert report["status"] == "incomplete"
        assert report["cases"][0]["lanes"][1]["execution_complete"] is False
        assert len(creations(runtime)) == 2
        prior = output / "task-validation.json"
        assert lane_readiness.read_report(prior, file_digest(prior))[1] == report
        _, rows = lane_readiness.plan_validation(
            package, digest, runtime, prior, file_digest(prior)
        )
        assert [row["action"] for row in rows] == ["reuse", "execute"]
        assert rows[1]["source"] is None
    finally:
        if failure == "cleanup-failure":
            remove_runtime_home(output / "executions/base/second")


def test_selected_matching_expectations_preserve_original_unexpected_receipt(
    validation_setup, tmp_path
):
    spec, _, _, runtime = independent_package(validation_setup, tmp_path / "independent.json")
    original = changed_lane(spec, expected={"prior_lanes": 1})
    original = original.model_copy(
        update={
            "limitations": ("Original authored limitation.",),
            "environment": original.environment.model_copy(
                update={"coverage_gaps": ("Original authored gap.",)}
            ),
        }
    )
    package, digest = publish_spec(original, tmp_path / "unexpected-package.json")
    directory = tmp_path / "unexpected-original"
    report = task_package.validate_environment(package, digest, directory, runtime=runtime)
    assert report["status"] == "incomplete"
    receipt = Path(report["cases"][0]["lanes"][1]["source"]["receipt"])
    original_bytes = receipt.read_bytes()
    prior = directory / "task-validation.json"
    prior_digest = file_digest(prior)
    selected = spec.model_copy(update={"limitations": ("Selected limitation.",)})
    package, digest = publish_spec(selected, tmp_path / "selected-package.json")
    selected_spec, checked = task_package.checked_validation(package, digest, prior, prior_digest)
    assert selected_spec == selected
    assert checked == report
    summary = task_package.readiness_selection(selected, digest, checked)
    source = summary["sources"][1]
    assert source["original_status"] == "unexpected"
    assert source["original_coverage_gaps"] == ["Original authored gap."]
    assert source["original_limitations"] == ["Original authored limitation."]
    assert summary["selected_limitations"] == ["Selected limitation."]
    assert summary["selected_expectations_status"] == "matched"
    assert receipt.read_bytes() == original_bytes
    assert len(creations(runtime)) == 2


def test_cli_dry_run_returns_reuse_plan_without_output_or_runtime_calls(retained, tmp_path, capsys):
    _, package, digest, runtime, prior, prior_digest, report = retained
    output = tmp_path / "unused-dry-run-output"
    audit = runtime.parent.parent / "validation-calls.jsonl"
    original_calls = audit.read_bytes()
    assert (
        cli.main(
            [
                "task-package",
                "validate-environment",
                str(package),
                "--sha256",
                digest,
                "--runtime",
                str(runtime),
                "--reuse",
                str(prior),
                "--reuse-sha256",
                prior_digest,
                "--output",
                str(output),
                "--dry-run",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    planned = json.loads(captured.out)
    assert planned["kind"] == "skilleval-task-validation-plan"
    assert planned["package_sha256"] == digest
    assert planned["inference_requests"] == 0
    assert [row["action"] for row in planned["lanes"]] == ["reuse", "reuse"]
    assert [row["source"] for row in planned["lanes"]] == [
        lane["source"] for lane in report["cases"][0]["lanes"]
    ]
    assert not output.exists()
    assert audit.read_bytes() == original_calls
    assert len(creations(runtime)) == 2
