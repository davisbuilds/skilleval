"""Freeze the specification task; never execute captured application code."""

from __future__ import annotations

import argparse
import json
from contextlib import suppress
from pathlib import Path

from skilleval.identity import file_digest
from skilleval.repair import RepairProbe, RepairSpec, prepare_repair

EXAMPLE = Path(__file__).resolve().parent
REVISION = "0dd253129d143b5811224a0f80554a64eb3f4da0"


def prepare(input_bundle: Path, input_sha256: str, output: Path) -> dict:
    output = output.absolute()
    worker = output.with_suffix(".worker.py")
    for destination in (output, worker):
        if destination.resolve().is_relative_to(input_bundle.resolve()):
            raise ValueError("task and worker must stay outside solver input")
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(destination)
    created = False
    try:
        with worker.open("xb") as stream:
            created = True
            stream.write((EXAMPLE / "worker.py").read_bytes())
        worker.chmod(0o444)
        digest = file_digest(worker)
        receipt = prepare_repair(
            RepairSpec(
                task_id="operational-metrics-specification-development",
                input_bundle=str(input_bundle),
                input_sha256=input_sha256,
                prompt=(EXAMPLE / "prompt.txt").read_text(),
                # The directory grant permits any chosen document filename.
                editable_paths=("docs/specs/operational-metrics.md",),
                editable_directories=("docs/specs",),
                worker=str(worker),
                worker_sha256=digest,
                probes=(
                    RepairProbe(
                        probe_id="spec-artifact",
                        request={"check": "spec-artifact"},
                        expected={"candidate_found": True, "artifacts_readable": True},
                    ),
                ),
                timeout_seconds=30,
                source_url="https://github.com/davisbuilds/agentmonitor",
                source_revision=REVISION,
            ),
            output,
        )
        return {**receipt, "worker": str(worker), "worker_sha256": digest}
    except BaseException:
        if created:
            with suppress(OSError):
                worker.unlink()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Selected baseline capture")
    parser.add_argument("--sha256", required=True, help="Retained baseline manifest digest")
    parser.add_argument("--output", type=Path, required=True, help="Fresh external task JSON")
    args = parser.parse_args()
    print(json.dumps(prepare(args.input, args.sha256, args.output), sort_keys=True))


if __name__ == "__main__":
    main()
