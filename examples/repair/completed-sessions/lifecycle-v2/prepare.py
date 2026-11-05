"""Prepare a fresh lifecycle-control identity without executing imported code."""

from __future__ import annotations

import argparse
import json
from contextlib import suppress
from pathlib import Path

from skilleval.identity import file_digest
from skilleval.repair import RepairProbe, RepairSpec, prepare_repair

HERE = Path(__file__).resolve().parent
BASELINE = "5f681e21c8af7766bb21efe60d15bae33c4ac68b"


def prepare(input_bundle: Path, input_sha256: str, output: Path) -> dict:
    requests = json.loads((HERE / "requests.json").read_text())
    expected = json.loads((HERE / "expected.json").read_text())
    answers = {row["probe_id"]: row["expected"] for row in expected}
    request_ids = [row["probe_id"] for row in requests]
    if (
        len(answers) != len(expected)
        or len(set(request_ids)) != len(request_ids)
        or set(answers) != set(request_ids)
    ):
        raise ValueError("request and expected observation IDs differ or repeat")
    output = output.absolute()
    worker = output.with_suffix(".worker.py")
    for destination in (output, worker):
        if destination.resolve().is_relative_to(input_bundle.resolve()):
            raise ValueError("task and frozen worker must remain outside solver input")
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(destination)
    if not output.parent.is_dir():
        raise FileNotFoundError(output.parent)
    created_worker = False
    try:
        with worker.open("xb") as target:
            created_worker = True
            target.write((HERE / "worker.py").read_bytes())
        worker.chmod(0o444)
        digest = file_digest(worker)
        result = prepare_repair(
            RepairSpec(
                task_id="completed-sessions-lifecycle-v2",
                input_bundle=str(input_bundle),
                input_sha256=input_sha256,
                prompt=(HERE.parent / "prompt.txt").read_text(),
                editable_paths=("README.md",),
                editable_directories=(
                    "src",
                    "tests",
                    "scripts",
                    "docs",
                    "dist",
                    "frontend/src",
                    "frontend/tests",
                    "frontend/dist",
                ),
                worker=str(worker),
                worker_sha256=digest,
                observer_resource_profile="node-v1",
                probes=tuple(
                    RepairProbe(**row, expected=answers[row["probe_id"]]) for row in requests
                ),
                timeout_seconds=180,
                source_url="https://github.com/davisbuilds/agentmonitor",
                source_revision=BASELINE,
            ),
            output,
        )
        return {**result, "worker": str(worker), "worker_sha256": digest}
    except BaseException:
        if created_worker:
            with suppress(OSError):
                worker.unlink()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Retained baseline capture")
    parser.add_argument("--sha256", required=True, help="Baseline capture manifest digest")
    parser.add_argument("--output", type=Path, required=True, help="Fresh task outside capture")
    args = parser.parse_args()
    print(json.dumps(prepare(args.input, args.sha256, args.output), sort_keys=True))


if __name__ == "__main__":
    main()
