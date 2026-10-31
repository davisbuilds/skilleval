import json
from pathlib import Path

import pytest
from test_repair import task as task
from test_runner import route as route

from skilleval import evidence, repair
from skilleval.errors import SkillEvalError
from skilleval.task_package import (
    TaskPackageSpec,
    ValidationLane,
    compare_lane,
    load_package,
    observations,
    prepare_package,
)


def lane(**updates):
    return ValidationLane(
        lane_id="unit",
        purpose="ordinary tests",
        argv=("python3", "-m", "unittest"),
        parser="unittest",
        expected_exit_code=0,
        expected={"tests": 2, "failures": [], "errors": [], "skips": []},
    ).model_copy(update=updates)


def process(**updates):
    return {
        "returncode": 0,
        "stdout": "",
        "stderr": "Ran 2 tests in 0.01s\n\nOK\n",
        "timed_out": False,
        "output_limit_reached": False,
        "argv": ["python3", "-m", "unittest"],
        "profile": {"uid": 0, "gid": 0, "groups": [], "cwd": "/workspace/task"},
        "source_changes": [],
    } | updates


def test_skipped_required_coverage_does_not_count_as_clean_success():
    result = compare_lane(
        lane(),
        process(
            stderr=(
                "test_a (test_suite.Tests.test_a) ... skipped 'missing dependency'\n"
                "Ran 2 tests in 0.01s\n\nOK (skipped=1)\n"
            )
        ),
    )
    assert result["status"] == "unexpected"
    assert result["observed"]["skips"] == ["test_a (test_suite.Tests.test_a)"]


@pytest.mark.parametrize("key", ["timed_out", "output_limit_reached"])
def test_partial_output_never_satisfies_an_expectation(key):
    assert compare_lane(lane(), process(**{key: True}))["status"] == "incomplete"


def test_known_failure_remains_visible_and_missing_failure_is_drift():
    expected = {"tests": 2, "failures": ["test_x (suite.Tests.test_x)"], "errors": [], "skips": []}
    item = lane(expected_exit_code=1, expected=expected)
    result = compare_lane(
        item,
        process(
            returncode=1,
            stderr=(
                "FAIL: test_x (suite.Tests.test_x)\nRan 2 tests in 0.01s\nFAILED (failures=1)\n"
            ),
        ),
    )
    assert result["status"] == "matched"
    assert result["observed"] == expected
    assert compare_lane(item, process())["status"] == "unexpected"


def test_empty_unittest_run_is_not_a_working_detector():
    with pytest.raises(SkillEvalError, match="nonempty"):
        observations(lane(), process(stderr="Ran 0 tests in 0.01s\nOK\n"))


def test_nonverbose_skips_and_missing_final_summary_are_incomplete():
    for text in ("s.\nRan 2 tests in 0.01s\nOK (skipped=1)\n", "Ran 2 tests in 0.01s\n"):
        assert compare_lane(lane(), process(stderr=text))["status"] == "incomplete"


def test_collected_json_can_hold_escaped_logs_within_the_guest_log_allowance(tmp_path):
    from skilleval.task_package import read_lane
    from skilleval.task_validation_guest import LOG_LIMIT

    payload = process(stderr="\x00" * LOG_LIMIT, stdout='{"ready":true}')
    file = tmp_path / "lane.json"
    file.write_text(json.dumps(payload))
    assert file.stat().st_size > 4 * 1024 * 1024
    spec = lane(parser="json", expected={"ready": True})
    assert compare_lane(spec, read_lane(file))["status"] == "matched"


@pytest.mark.parametrize("changed", ["argv", "profile", "source_changes"])
def test_wrong_execution_route_or_mutated_source_cannot_match(changed):
    mutations = {"argv": ["true"], "profile": {"uid": 65534}, "source_changes": ["code.py"]}
    assert compare_lane(lane(), process(**{changed: mutations[changed]}))["status"] != "matched"


def solver_process(**updates):
    return (
        process(
            profile={"uid": 61000, "gid": 61000, "groups": [], "cwd": "/workspace/task"},
            pipe_drain_incomplete=False,
            boundary={
                "schema_version": 1,
                "profile": "solver-isolated-v1",
                "status": "ready",
                "uid": 61000,
                "gid": 61000,
                "groups": [],
                "no_new_privs": True,
                "capabilities_cleared": True,
                "transport_filter": "vsock-deny-v1",
                "seccomp": True,
                "namespace_reaped": True,
                "native_returncode": 0,
                "native_elapsed_seconds": 0.01,
                "launcher_elapsed_seconds": 0.02,
            },
        )
        | updates
    )


def test_solver_validation_requires_its_effective_boundary_and_retains_legacy_root():
    assert compare_lane(lane(), process())["status"] == "matched"
    assert compare_lane(lane(), solver_process(), "solver-isolated-v1")["status"] == "matched"
    assert compare_lane(lane(), process(), "solver-isolated-v1")["status"] == "incomplete"
    assert compare_lane(lane(), solver_process())["status"] == "incomplete"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("schema_version", 2),
        ("profile", "native-root-v1"),
        ("status", "failed"),
        ("uid", 0),
        ("gid", 0),
        ("groups", [0]),
        ("no_new_privs", False),
        ("capabilities_cleared", False),
        ("transport_filter", "unknown"),
        ("seccomp", False),
        ("namespace_reaped", False),
        ("native_returncode", True),
        ("native_returncode", 1),
    ],
)
def test_incomplete_or_different_solver_boundary_cannot_validate_a_lane(key, value):
    observed = solver_process()
    observed["boundary"][key] = value
    assert compare_lane(lane(), observed, "solver-isolated-v1")["status"] == "incomplete"


def test_solver_validation_requires_complete_pipe_collection():
    for value in (True, None):
        assert (
            compare_lane(lane(), solver_process(pipe_drain_incomplete=value), "solver-isolated-v1")[
                "status"
            ]
            == "incomplete"
        )


def package_spec(task, route):
    return TaskPackageSpec.model_validate_json(
        json.dumps(
            {
                "package_id": "authored",
                "family": "repair",
                "task": str(task["task"]),
                "task_sha256": task["digest"],
                "environment": {
                    "image_archive": str(route["image"]),
                    "image_sha256": route["image_sha256"],
                    "setup": ["Synthetic image for deterministic tests."],
                    "required_paths": ["README.md", "code.py"],
                },
                "validation": [
                    {
                        "case_id": "base",
                        "role": "baseline",
                        "input_bundle": str(task["base"]),
                        "input_sha256": task["spec"].input_sha256,
                        "lanes": [lane().model_dump(mode="json")],
                    }
                ],
                "limitations": ["Authored packaging control, not live runtime evidence."],
            }
        )
    )


def test_package_preparation_preserves_code_as_data_and_rejects_input_drift(task, route, tmp_path):
    spec = package_spec(task, route)
    saved = prepare_package(spec, tmp_path / "package.json")
    assert load_package(Path(saved["path"]), saved["sha256"]) == spec
    assert saved["scored_admitted"] is False
    target = task["base"] / "README.md"
    target.chmod(0o600)
    target.write_text("changed ordinary context")
    with pytest.raises(SkillEvalError):
        load_package(Path(saved["path"]), saved["sha256"])


def test_explicit_solver_profile_and_missing_legacy_profile_load_without_rewriting(
    task, route, tmp_path
):
    spec = package_spec(task, route)
    isolated = spec.model_copy(
        update={
            "environment": spec.environment.model_copy(update={"profile": "solver-isolated-v1"})
        }
    )
    saved = prepare_package(isolated, tmp_path / "solver-package.json")
    assert load_package(Path(saved["path"]), saved["sha256"]).environment.profile == (
        "solver-isolated-v1"
    )
    legacy = {
        "schema_version": 1,
        "kind": "skilleval-task-package",
        "spec": spec.model_dump(mode="json"),
    }
    del legacy["spec"]["environment"]["profile"]
    destination = tmp_path / "legacy-package.json"
    digest = repair._publish(destination, legacy)
    original = destination.read_bytes()
    assert load_package(destination, digest).environment.profile == "native-root-v1"
    assert destination.read_bytes() == original


def test_missing_dependency_and_wrong_baseline_are_rejected_before_execution(task, route, tmp_path):
    spec = package_spec(task, route)
    missing = spec.model_copy(
        update={
            "environment": spec.environment.model_copy(
                update={"required_paths": ("missing-fixture.json",)}
            )
        }
    )
    with pytest.raises(SkillEvalError, match="required task context"):
        prepare_package(missing, tmp_path / "missing.json")
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "README.md").write_text("unrelated")
    frozen = evidence.snapshot(extra, tmp_path / "other")
    changed = spec.model_copy(
        update={
            "validation": (
                spec.validation[0].model_copy(
                    update={
                        "input_bundle": str(tmp_path / "other"),
                        "input_sha256": frozen["manifest_sha256"],
                    }
                ),
            )
        }
    )
    with pytest.raises(SkillEvalError, match="baseline differs"):
        prepare_package(changed, tmp_path / "foreign.json")
