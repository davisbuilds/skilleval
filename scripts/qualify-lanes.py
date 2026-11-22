#!/usr/bin/env python3
"""Opt-in, credential-free real-VM validation lane and selective reuse controls.

Require explicit --runtime, --image-archive, --image-sha256 and a fresh --output directory.
The image must support solver-isolated-v1 and python3. Keep execution sources and
the runtime unchanged until every VM is recovered. After stopping an interrupted
controller, --recover TICKET_DIRECTORY recovers exactly one retained recovery.json.
No models, credentials, imported task code, or external network are used.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

from skilleval import evidence, repair, task_package
from skilleval.identity import file_digest, snapshot_identity
from skilleval.lifecycle import recover
from skilleval.study import _clean_native

REPO = Path(__file__).resolve().parents[1]
OBSERVE = (
    "{'writable_bits': bool(p.stat().st_mode & 0o222), "
    "'empty_dirs': int(d.is_dir() and not any(d.iterdir()))}"
)
PRELUDE = "import json; from pathlib import Path; p=Path('sentinel.txt'); d=Path('empty-state'); "
A = PRELUDE + "p.chmod(0o444); d.mkdir(); print(json.dumps(" + OBSERVE + "))"
B = PRELUDE + "print(json.dumps(" + OBSERVE + "))"
B2 = PRELUDE + "v=" + OBSERVE + "; v['revision']='b-v2'; print(json.dumps(v))"
DIRTY = {"writable_bits": False, "empty_dirs": 1}
CLEAN = {"writable_bits": True, "empty_dirs": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Fresh private evidence directory")
    parser.add_argument("--runtime", type=Path, help="smolvm binary from a qualified distribution")
    parser.add_argument(
        "--image-archive", type=Path, help="solver-isolated-v1 compatible image archive"
    )
    parser.add_argument("--image-sha256", help="Expected image archive SHA-256")
    parser.add_argument(
        "--recover", type=Path, help="Recover one ticket after its controller stops"
    )
    args = parser.parse_args()
    if args.recover:
        if any((args.runtime, args.image_archive, args.image_sha256, args.output)):
            parser.error("--recover cannot be combined with run arguments")
        ticket = recover(args.recover)
        print(json.dumps(ticket, indent=2, sort_keys=True))
        return 0 if ticket["cleanup_complete"] else 1
    if not all((args.runtime, args.image_archive, args.image_sha256, args.output)):
        parser.error(
            "provide --runtime, --image-archive, --image-sha256 and --output, or --recover"
        )
    if not re.fullmatch("[0-9a-f]{64}", args.image_sha256):
        parser.error("--image-sha256 must be 64 lowercase hexadecimal characters")
    root = args.output.resolve()
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError:
        parser.error("--output must be a fresh directory; existing evidence is never replaced")
    args.runtime = args.runtime.resolve()
    args.image_archive = args.image_archive.resolve()
    summary = {
        "status": "incomplete",
        "host": socket.gethostname(),
        "inference_requests": 0,
        "scope": "Authored execution/reuse controls; no task-quality or skill-effect claim.",
        "commands": [],
        "checks": {},
        "reports": {},
        "cleanup": [],
    }

    def save(path, value):
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

    def check(name, condition):
        summary["checks"][name] = bool(condition)
        if not condition:
            raise AssertionError(name)

    def cli(label, *argv):
        command = [sys.executable, "-m", "skilleval", *map(str, argv)]
        started = time.monotonic()
        result = subprocess.run(command, cwd=REPO, text=True, capture_output=True)
        (root / (label + ".stdout")).write_text(result.stdout)
        (root / (label + ".stderr")).write_text(result.stderr)
        summary["commands"].append(
            {
                "label": label,
                "argv": command,
                "returncode": result.returncode,
                "elapsed_seconds": time.monotonic() - started,
            }
        )
        save(root / "qualification.json", summary)
        if result.returncode:
            raise RuntimeError(f"{label} failed: see retained stdout/stderr")
        return json.loads(result.stdout)

    def package(label, isolation, second_argv, second_expected, timeout=60):
        lanes = tuple(
            task_package.ValidationLane(
                lane_id=lane_id,
                purpose="Authored state-independence control",
                argv=("python3", "-c", code),
                timeout_seconds=deadline,
                parser="json",
                expected_exit_code=0,
                expected=expected,
            )
            for lane_id, code, expected, deadline in (
                ("a", A, DIRTY, 60),
                ("b", second_argv, second_expected, timeout),
            )
        )
        spec = task_package.TaskPackageSpec(
            package_id=label,
            family="repair",
            task=task["path"],
            task_sha256=task["sha256"],
            environment=task_package.TaskEnvironment(
                image_archive=str(args.image_archive.absolute()),
                image_sha256=args.image_sha256,
                profile="solver-isolated-v1",
                validation_isolation=isolation,
                setup=("No setup; authored Python commands execute only in disposable VMs.",),
                required_paths=("sentinel.txt",),
            ),
            validation=(
                task_package.ValidationCase(
                    case_id="base",
                    role="baseline",
                    input_bundle=str(root / "input"),
                    input_sha256=captured["manifest_sha256"],
                    lanes=lanes,
                ),
            ),
            limitations=("Authored state control, not general workload coverage.",),
        )
        spec_path = root / (label + "-spec.json")
        save(spec_path, spec.model_dump(mode="json"))
        return cli(
            label + "-prepare",
            "task-package",
            "prepare",
            spec_path,
            "--output",
            root / (label + "-package.json"),
        )

    def validate(label, selected, prior=None):
        output = root / label
        argv = [
            "task-package",
            "validate-environment",
            selected["path"],
            "--sha256",
            selected["sha256"],
            "--runtime",
            args.runtime,
            "--output",
            output,
        ]
        if prior is not None:
            argv += ["--reuse", prior, "--reuse-sha256", file_digest(prior)]
        report = cli(label, *argv)
        path = output / "task-validation.json"
        summary["reports"][label] = {"path": str(path), "sha256": file_digest(path)}
        check(
            label + "-matched-zero-inference",
            report["status"] == "matched" and report["inference_requests"] == 0,
        )
        task_package.checked_validation(
            Path(selected["path"]), selected["sha256"], path, file_digest(path)
        )
        return report, path

    try:
        before = snapshot_identity(str(args.runtime))
        control_digest = file_digest(Path(__file__))
        summary["control_sha256"] = control_digest
        save(root / "source-identity-before.json", before)
        check("pinned-image", file_digest(args.image_archive) == args.image_sha256)
        source = root / "source"
        source.mkdir()
        sentinel = source / "sentinel.txt"
        sentinel.write_text("ordinary immutable sentinel bytes\n")
        sentinel.chmod(0o644)
        captured = evidence.snapshot(source, root / "input")
        worker = root / "unused-worker.py"
        worker.write_text("raise RuntimeError('qualification worker is never invoked')\n")
        spec = repair.RepairSpec(
            task_id="lane-state-control",
            input_bundle=str(root / "input"),
            input_sha256=captured["manifest_sha256"],
            prompt="Inspect the ordinary sentinel.",
            editable_paths=("sentinel.txt",),
            worker=str(worker),
            worker_sha256=file_digest(worker),
            probes=(repair.RepairProbe(probe_id="unused", request={}, expected={}),),
            source_url="https://example.invalid/authored-lane-state-control",
            source_revision="v1",
        )
        save(root / "task-spec.json", spec.model_dump(mode="json"))
        task = cli(
            "task-prepare",
            "artifact",
            "prepare",
            root / "task-spec.json",
            "--output",
            root / "task.json",
        )
        shared = package("shared", "case-v1", B, DIRTY)
        shared_report, _ = validate("shared-control", shared)
        shared_lanes = shared_report["cases"][0]["lanes"]
        check("shared-positive-dirty-state", shared_lanes[1]["observed"] == DIRTY)
        check(
            "shared-source-inventory-misses-state",
            all(x["process"]["source_changes"] == [] for x in shared_lanes),
        )
        independent = package("independent", "lane-v1", B, CLEAN)
        initial, prior = validate("independent-initial", independent)
        lanes = initial["cases"][0]["lanes"]
        check("independent-clean-state", lanes[1]["comparison"]["observed"] == CLEAN)
        check("initial-two-vms", len(list(prior.parent.rglob("recovery.json"))) == 2)
        unchanged, unchanged_path = validate("independent-unchanged", independent, prior)
        check(
            "unchanged-zero-vms",
            not list(unchanged_path.parent.rglob("recovery.json"))
            and all(x["action"] == "reuse" for x in unchanged["cases"][0]["lanes"]),
        )
        changed = package("changed-b", "lane-v1", B2, {**CLEAN, "revision": "b-v2"}, 61)
        changed_report, changed_path = validate("independent-changed-b", changed, unchanged_path)
        changed_lanes = changed_report["cases"][0]["lanes"]
        check(
            "changed-only-b-executes",
            [x["action"] for x in changed_lanes] == ["reuse", "execute"]
            and len(list(changed_path.parent.rglob("recovery.json"))) == 1,
        )
        check("a-original-source-preserved", changed_lanes[0]["source"] == lanes[0]["source"])
        check(
            "host-input-remains-ordinary",
            sentinel.stat().st_mode & 0o777 == 0o644 and not (source / "empty-state").exists(),
        )
        check(
            "execution-source-stable",
            snapshot_identity(str(args.runtime)) == before
            and file_digest(Path(__file__)) == control_digest,
        )
        check("image-remains-pinned", file_digest(args.image_archive) == args.image_sha256)
        summary["status"] = "passed"
    except BaseException as error:
        summary["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        for recovery in root.rglob("recovery.json"):
            directory = recovery.parent
            try:
                ticket = recover(directory)
            except Exception as error:
                summary["cleanup"].append(
                    {
                        "directory": str(directory),
                        "clean": False,
                        "error": f"{type(error).__name__}: {error}",
                    }
                )
                continue
            clean = (
                _clean_native(directory)
                and not os.path.lexists(ticket["runtime_home"])
                and not (directory / "runtime-state/image.tar").exists()
            )
            summary["cleanup"].append(
                {
                    "directory": str(directory),
                    "clean": clean,
                    "recovery_sha256": file_digest(recovery),
                }
            )
        if not all(x["clean"] for x in summary["cleanup"]):
            summary["status"] = "failed-cleanup"
        save(root / "qualification.json", summary)
    if summary["status"] != "passed":
        raise RuntimeError("qualification failed: inspect qualification.json")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
