#!/usr/bin/env python3
"""Explicit public-route validation control; task bytes execute only in the VM.

Default/--prepare-only creates authored inputs without launching a VM. --run
prepares fresh inputs and executes the public task-package validation route.
Provide a pinned Docker-save image with Python >=3.11 and Git, an explicit smolvm
runtime, and a fresh output directory outside the runtime inventory. Keep source,
runtime and image stable during execution. No authentication or model requests.
Native Codex controls are maintained separately in qualify-solver.py.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from skilleval import evidence, repair, task_package
from skilleval.errors import SkillEvalError
from skilleval.identity import file_digest

BASE_README = "# Example service\nBase context.\n"
CURRENT_README = "# Example service\nOrdinary configuration and tests.\n"

PROBE = r"""
import json
import os
import subprocess
import tomllib
from pathlib import Path

def git(*args):
    result = subprocess.run(["git", *args], check=True, capture_output=True, text=True)
    return result.stdout

def readable(path):
    try:
        Path(path).read_bytes()
        return True
    except OSError:
        return False

status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines())
controller_paths = [
    "/var/tmp/native/validation.py",
    "/var/tmp/native/native_process.py",
    "/var/tmp/native/solver_boundary.py",
    "/var/tmp/validation.tar",
    "/opt/skilleval/openbench_gateway.py",
]
def indirect(path, method):
    if method == "direct":
        return readable(path)
    if method == "symlink":
        link = Path("read-link")
        link.symlink_to(path)
        try:
            return readable(link)
        finally:
            link.unlink()
    child = subprocess.run(
        ["python3", "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).read_bytes()", path],
        capture_output=True, timeout=5,
    )
    return child.returncode == 0
methods = ("direct", "symlink", "subprocess")
positive = str(Path("public-sentinel.txt").resolve())
edited = Path("probe-edit.txt")
edited.write_text("ordinary shell edit\n")
try:
    child = subprocess.run(
        ["/bin/sh", "-c", "cat probe-edit.txt"], check=True, capture_output=True, text=True
    )
    edit_roundtrip = child.stdout == "ordinary shell edit\n"
finally:
    edited.unlink()
for name in ("HOME", "CODEX_HOME"):
    (Path(os.environ[name]) / "first-lane-marker").write_text("first lane only\n")
cmdlines = []
for path in Path("/proc").glob("[0-9]*/cmdline"):
    try:
        cmdlines.append(path.read_bytes())
    except OSError:
        pass
print(json.dumps({
    "uid": os.getuid(),
    "gid": os.getgid(),
    "groups": os.getgroups(),
    "no_new_privs": int(status["NoNewPrivs"].strip()),
    "cap_effective": status["CapEff"].strip(),
    "config_mode": tomllib.loads(Path("service.toml").read_text())["mode"],
    "readme": Path("README.md").read_text(),
    "public_sentinel": Path("public-sentinel.txt").read_text(),
    "history_count": int(git("rev-list", "--count", "HEAD").strip()),
    "previous_readme": git("show", "HEAD^:README.md"),
    "head_readme": git("show", "HEAD:README.md"),
    "clean_task": git("status", "--porcelain") == "",
    "edit_roundtrip": edit_roundtrip,
    "positive_reads": {method: indirect(positive, method) for method in methods},
    "controller_reads": {
        path: {method: indirect(path, method) for method in methods}
        for path in controller_paths
    },
    "self_cmdline_readable": bool(Path("/proc/self/cmdline").read_bytes()),
    "process_scan_positive": any(b"visibility_probe.py" in line for line in cmdlines),
    "controller_process_visible": any(
        b"validation.py" in line or b"solver_boundary.py" in line for line in cmdlines
    ),
    "standard_fds_are_pipes": all(
        os.readlink(f"/proc/self/fd/{fd}").startswith("pipe:[") for fd in (0, 1, 2)
    ),
}, sort_keys=True))
"""

TESTS = r"""
import os
import tomllib
import unittest
from pathlib import Path

from value import add

class OrdinaryTests(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(3, 4), 7)

    def test_configuration(self):
        config = tomllib.loads(Path("service.toml").read_text())
        self.assertEqual(config["mode"], "offline")

    def test_fresh_homes(self):
        for name in ("HOME", "CODEX_HOME"):
            self.assertFalse((Path(os.environ[name]) / "first-lane-marker").exists())

if __name__ == "__main__":
    unittest.main()
"""


def write_json(path: Path, value: object) -> None:
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def git(root: Path, *arguments: str) -> str:
    """Only trusted Git runs on the host; no task module is imported/executed."""
    binary = shutil.which("git", path=os.defpath)
    if binary is None:
        raise SkillEvalError("Git unavailable")
    environment = {
        "PATH": os.defpath,
        "HOME": str(root / ".git"),
        "XDG_CONFIG_HOME": str(root / ".git"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_ALLOW_PROTOCOL": "",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_AUTHOR_NAME": "Task Author",
        "GIT_AUTHOR_EMAIL": "author@example.invalid",
        "GIT_COMMITTER_NAME": "Task Author",
        "GIT_COMMITTER_EMAIL": "author@example.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+0000",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+0000",
        "LC_ALL": "C",
    }
    result = subprocess.run(
        [binary, "-c", f"core.hooksPath={os.devnull}", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )
    return result.stdout.strip()


def prepare(output: Path, runtime: Path, image: Path, image_sha256: str) -> dict[str, object]:
    runtime_digest = file_digest(runtime)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    source = output / "source"
    source.mkdir(mode=0o700)
    (source / "README.md").write_text(BASE_README)
    (source / "value.py").write_text("def add(left, right):\n    return left + right\n")
    (source / "service.toml").write_text('mode = "offline"\n')
    (source / "test_value.py").write_text(textwrap.dedent(TESTS).lstrip())
    git(source, "init", "--quiet", "--template=")
    git(source, "add", "README.md", "value.py", "service.toml", "test_value.py")
    git(source, "commit", "--quiet", "-m", "Add arithmetic helper and ordinary tests")
    base_commit = git(source, "rev-parse", "HEAD")
    (source / "README.md").write_text(CURRENT_README)
    (source / "public-sentinel.txt").write_text("public-readable-control\n")
    (source / "visibility_probe.py").write_text(textwrap.dedent(PROBE).lstrip())
    git(source, "add", "README.md", "public-sentinel.txt", "visibility_probe.py")
    git(source, "commit", "--quiet", "-m", "Document configuration and add inspection command")
    current_commit = git(source, "rev-parse", "HEAD")
    snapshot = evidence.snapshot(source, output / "input")
    worker = output / "held-checker.py"
    worker.write_text("raise RuntimeError('held checker is not part of environment validation')\n")
    artifact = repair.RepairSpec(
        task_id="solver-validation-control",
        input_bundle=str(output / "input"),
        input_sha256=snapshot["manifest_sha256"],
        prompt="Keep the arithmetic helper and its ordinary tests working.",
        editable_paths=("value.py",),
        worker=str(worker),
        worker_sha256=file_digest(worker),
        probes=(repair.RepairProbe(probe_id="held", request={}, expected=True),),
        source_url="https://example.invalid/authored-control",
        source_revision=current_commit,
    )
    artifact_receipt = repair.prepare_repair(artifact, output / "task.json")
    expected = {
        "uid": 61000,
        "gid": 61000,
        "groups": [],
        "no_new_privs": 1,
        "cap_effective": "0000000000000000",
        "config_mode": "offline",
        "readme": CURRENT_README,
        "public_sentinel": "public-readable-control\n",
        "history_count": 2,
        "previous_readme": BASE_README,
        "head_readme": CURRENT_README,
        "clean_task": True,
        "edit_roundtrip": True,
        "positive_reads": dict.fromkeys(("direct", "symlink", "subprocess"), True),
        "controller_reads": {
            path: dict.fromkeys(("direct", "symlink", "subprocess"), False)
            for path in (
                "/var/tmp/native/validation.py",
                "/var/tmp/native/native_process.py",
                "/var/tmp/native/solver_boundary.py",
                "/var/tmp/validation.tar",
                "/opt/skilleval/openbench_gateway.py",
            )
        },
        "self_cmdline_readable": True,
        "process_scan_positive": True,
        "controller_process_visible": False,
        "standard_fds_are_pipes": True,
    }
    spec = task_package.TaskPackageSpec(
        package_id="solver-validation-control",
        family="repair",
        task=str(output / "task.json"),
        task_sha256=artifact_receipt["sha256"],
        environment=task_package.TaskEnvironment(
            image_archive=str(image),
            image_sha256=image_sha256,
            profile="solver-isolated-v1",
            setup=("Explicit pinned image supplies Python, unittest and Git.",),
            required_paths=(
                "README.md",
                "value.py",
                "service.toml",
                "test_value.py",
                "visibility_probe.py",
                "public-sentinel.txt",
                ".git/HEAD",
                ".git/objects",
            ),
            coverage_gaps=(
                "Synthetic control; native Codex and inference are exercised separately.",
            ),
        ),
        validation=(
            task_package.ValidationCase(
                case_id="baseline",
                role="baseline",
                input_bundle=str(output / "input"),
                input_sha256=snapshot["manifest_sha256"],
                lanes=(
                    task_package.ValidationLane(
                        lane_id="inspection",
                        purpose="Check source/history, subprocess edits and solver authority.",
                        argv=("python3", "visibility_probe.py"),
                        timeout_seconds=30,
                        parser="json",
                        expected_exit_code=0,
                        expected=expected,
                    ),
                    task_package.ValidationLane(
                        lane_id="ordinary-tests",
                        purpose="Run ordinary tests in a second boundary with fresh homes.",
                        argv=("python3", "-m", "unittest", "-v", "test_value"),
                        timeout_seconds=30,
                        parser="unittest",
                        expected_exit_code=0,
                        expected={"tests": 3, "failures": [], "errors": [], "skips": []},
                    ),
                ),
            ),
        ),
        limitations=(
            "Authored mechanism/packaging control, not task quality or scored admission.",
            "No Codex or inference socket; the native route is qualified separately.",
        ),
    )
    package = task_package.prepare_package(spec, output / "package.json")
    inputs = {
        "schema_version": 1,
        "kind": "skilleval-validation-qualification-inputs",
        "script_sha256": file_digest(Path(__file__)),
        "runtime": str(runtime),
        "runtime_sha256": runtime_digest,
        "image": str(image),
        "image_sha256": image_sha256,
        "package": str(output / "package.json"),
        "package_sha256": package["sha256"],
        "input_sha256": snapshot["manifest_sha256"],
        "base_commit": base_commit,
        "current_commit": current_commit,
        "scored_admitted": False,
        "inference_requests": 0,
    }
    write_json(output / "control-inputs.json", inputs)
    return inputs


def run(output: Path, inputs: dict[str, object]) -> dict[str, object]:
    runtime = Path(str(inputs["runtime"]))
    if file_digest(runtime) != inputs["runtime_sha256"]:
        raise SkillEvalError("runtime changed after control preparation")
    package = Path(str(inputs["package"]))
    package_digest = str(inputs["package_sha256"])
    report = task_package.validate_environment(
        package, package_digest, output / "validation", runtime=runtime
    )
    report_path = output / "validation/task-validation.json"
    report_digest = file_digest(report_path)
    consumer: dict[str, object] = {"accepted": False}
    try:
        task_package.checked_validation(package, package_digest, report_path, report_digest)
        consumer["accepted"] = True
    except (SkillEvalError, OSError, ValueError) as error:
        consumer["error"] = type(error).__name__ + ": " + str(error)
    passed = (
        report["status"] == "matched"
        and consumer["accepted"] is True
        and report["inference_requests"] == 0
        and report["scored_admitted"] is False
        and len(report["cases"]) == 1
        and all(case.get("cleanup_complete") is True for case in report["cases"])
        and file_digest(runtime) == inputs["runtime_sha256"]
    )
    summary = {
        "schema_version": 1,
        "kind": "skilleval-validation-qualification",
        "status": "passed" if passed else "failed",
        "validation_status": report["status"],
        "report": str(report_path),
        "report_sha256": report_digest,
        "checked_validation": consumer,
        "cases": [
            {
                "case_id": case["case_id"],
                "status": case["status"],
                "cleanup_complete": case.get("cleanup_complete"),
                "error": case.get("error"),
                "lanes": [
                    {"lane_id": lane["lane_id"], "status": lane["status"]} for lane in case["lanes"]
                ],
            }
            for case in report["cases"]
        ],
        "inference_requests": report["inference_requests"],
        "scored_admitted": report["scored_admitted"],
        "limitations": [
            "Authored validation-route control; no model, native Codex or task-quality claim.",
            "Controller path refusals have matching public direct/symlink/subprocess controls.",
            "Source inventory and boundary receipts are recorded by the trusted VM supervisor.",
        ],
    }
    write_json(output / "control-summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--image-archive", type=Path, required=True)
    parser.add_argument("--image-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--prepare-only", action="store_true", help="prepare inputs without a VM (default)"
    )
    modes.add_argument(
        "--run", action="store_true", help="prepare and execute fresh real-VM controls"
    )
    args = parser.parse_args()
    output = args.output.resolve()
    runtime = args.runtime.resolve()
    image = args.image_archive.resolve()
    if output.is_relative_to(runtime.parent):
        parser.error("output must be outside runtime inventory")
    try:
        inputs = prepare(output, runtime, image, args.image_sha256)
        summary = (
            run(output, inputs)
            if args.run
            else {"status": "prepared-no-vm", "inputs": str(output / "control-inputs.json")}
        )
    except (SkillEvalError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(type(error).__name__ + ": " + str(error), file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["status"] in {"prepared-no-vm", "passed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
