"""Deterministic preparation and replay controls; never proof of VM isolation."""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from test_qualification import docker_archive
from test_task_package import solver_process

from skilleval import task_package
from skilleval.errors import SkillEvalError
from skilleval.identity import file_digest


@pytest.fixture
def validation_probe() -> Any:
    script = Path(__file__).parents[1] / "scripts/qualify-validation.py"
    spec = importlib.util.spec_from_file_location("qualify_validation", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def prepared(validation_probe: Any, tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    runtime = tmp_path / "runtime" / "smolvm"
    runtime.parent.mkdir()
    runtime.write_text("not executable; preparation must never invoke this runtime\n")
    image, digest = docker_archive(tmp_path / "image")
    output = tmp_path / "control"
    return output, validation_probe.prepare(output, runtime, image, digest)


def test_preparation_preserves_ordinary_context_history_and_explicit_pins(
    validation_probe: Any, prepared: tuple[Path, dict[str, Any]]
) -> None:
    output, inputs = prepared
    package = task_package.load_package(Path(inputs["package"]), inputs["package_sha256"])
    assert inputs["runtime_sha256"] == file_digest(Path(inputs["runtime"]))
    assert inputs["script_sha256"] == file_digest(Path(validation_probe.__file__))
    assert (output / "control-inputs.json").is_file()
    assert not (output / "validation").exists()
    assert package.environment.profile == "solver-isolated-v1"
    assert package.environment.validation_isolation == "case-v1"
    assert {"service.toml", "test_value.py", ".git/HEAD", ".git/objects"} <= set(
        package.environment.required_paths
    )
    assert [lane.lane_id for lane in package.validation[0].lanes] == [
        "inspection",
        "ordinary-tests",
    ]
    assert validation_probe.git(output / "source", "rev-list", "--count", "HEAD") == "2"
    assert (
        validation_probe.git(output / "source", "show", "HEAD^:README.md")
        == "# Example service\nBase context."
    )
    expected = package.validation[0].lanes[0].expected
    assert expected["public_sentinel"] == "public-readable-control\n"
    assert expected["positive_reads"] == dict.fromkeys(("direct", "symlink", "subprocess"), True)
    assert expected["process_scan_positive"] is True
    assert expected["controller_process_visible"] is False
    assert expected["edit_roundtrip"] is True
    assert inputs["inference_requests"] == 0 and inputs["scored_admitted"] is False
    compile(validation_probe.PROBE, "validation-control", "exec")
    compile(validation_probe.TESTS, "ordinary-tests", "exec")


def test_preparation_refuses_existing_output_without_changing_it(
    validation_probe: Any, prepared: tuple[Path, dict[str, Any]]
) -> None:
    output, inputs = prepared
    original = (output / "control-inputs.json").read_bytes()
    with pytest.raises(FileExistsError):
        validation_probe.prepare(
            output, Path(inputs["runtime"]), Path(inputs["image"]), inputs["image_sha256"]
        )
    assert (output / "control-inputs.json").read_bytes() == original


def test_mismatched_image_digest_cannot_become_prepared_inputs(
    validation_probe: Any, tmp_path: Path
) -> None:
    runtime = tmp_path / "smolvm"
    runtime.write_bytes(b"never invoked")
    image, _ = docker_archive(tmp_path / "image")
    output = tmp_path / "bad-control"
    with pytest.raises(SkillEvalError, match="digest"):
        validation_probe.prepare(output, runtime, image, "0" * 64)
    assert not (output / "control-inputs.json").exists()


@pytest.mark.parametrize(
    "mutation",
    ["path_leak", "missing_denial", "broken_symlink", "blind_process_scan", "visible_controller"],
)
def test_packaged_expectations_reject_leaks_and_nonworking_detectors(
    prepared: tuple[Path, dict[str, Any]], mutation: str
) -> None:
    _, inputs = prepared
    package = task_package.load_package(Path(inputs["package"]), inputs["package_sha256"])
    lane = package.validation[0].lanes[0]
    observation = copy.deepcopy(lane.expected)
    process = solver_process(argv=list(lane.argv), stdout=json.dumps(observation))
    assert (
        task_package.compare_lane(lane, process, package.environment.profile)["status"] == "matched"
    )
    if mutation == "path_leak":
        observation["controller_reads"]["/var/tmp/native/validation.py"]["direct"] = True
    elif mutation == "missing_denial":
        del observation["controller_reads"]["/var/tmp/native/validation.py"]["subprocess"]
    elif mutation == "broken_symlink":
        observation["positive_reads"]["symlink"] = False
    elif mutation == "blind_process_scan":
        observation["process_scan_positive"] = False
    else:
        observation["controller_process_visible"] = True
    process["stdout"] = json.dumps(observation)
    assert (
        task_package.compare_lane(lane, process, package.environment.profile)["status"]
        == "unexpected"
    )


def test_runtime_mutation_is_rejected_before_any_vm_work(
    validation_probe: Any, prepared: tuple[Path, dict[str, Any]]
) -> None:
    output, inputs = prepared
    Path(inputs["runtime"]).write_bytes(b"changed runtime")
    with pytest.raises(SkillEvalError, match="runtime changed"):
        validation_probe.run(output, inputs)
    assert not (output / "validation").exists()


def test_help_is_inert_and_preparation_is_default(validation_probe: Any) -> None:
    result = subprocess.run(
        [sys.executable, validation_probe.__file__, "--help"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "--run" in result.stdout and "--prepare-only" in result.stdout
    assert "without a VM (default)" in result.stdout


def test_default_cli_prepares_without_invoking_the_nonexecutable_runtime(
    validation_probe: Any, prepared: tuple[Path, dict[str, Any]]
) -> None:
    output, inputs = prepared
    fresh = output.parent / "default-cli-control"
    result = subprocess.run(
        [
            sys.executable,
            validation_probe.__file__,
            "--runtime",
            inputs["runtime"],
            "--image-archive",
            inputs["image"],
            "--image-sha256",
            inputs["image_sha256"],
            "--output",
            str(fresh),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert json.loads(result.stdout)["status"] == "prepared-no-vm"
    assert (fresh / "control-inputs.json").is_file()
    assert not (fresh / "validation").exists()
