"""Bind reviewed environment expectations to the specification task.

Preparation reads trusted operator wrappers as data; only the explicit
task-package validate-environment command executes them inside the guest.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from skilleval.repair import load_task
from skilleval.task_package import TaskPackageSpec, prepare_package

EXAMPLE = Path(__file__).resolve().parent
LANES = ("toolchain", "checks", "root-tests", "frontend-tests", "metrics-tests", "browser")


def prepare(task: Path, digest: str, image: Path, image_digest: str, expected: Path, output: Path):
    contract = load_task(task, digest)
    expectations = json.loads(expected.read_bytes())
    if set(expectations) != set(LANES):
        raise ValueError("expectations must explicitly name all six environment lanes")
    lanes = []
    for lane in LANES:
        helper = "toolchain.py" if lane == "toolchain" else "validate.py"
        argv = ["python3", "-I", "-c", (EXAMPLE / "environment" / helper).read_text()]
        if lane != "toolchain":
            argv.extend([lane, "--summary"])
        if lane == "browser":
            for flag, name in (
                ("--browser-base64", "browser.mjs"),
                ("--fixture-base64", "metrics-fixture.json"),
            ):
                argv.extend(
                    [
                        flag,
                        base64.b64encode((EXAMPLE / "environment" / name).read_bytes()).decode(
                            "ascii"
                        ),
                    ]
                )
        expectation = expectations[lane]
        if set(expectation) != {"exit_code", "observed"}:
            raise ValueError("each lane requires explicit exit_code and observed JSON")
        lanes.append(
            {
                "lane_id": lane,
                "purpose": f"Frozen source environment: {lane}; not feature correctness",
                "argv": argv,
                "timeout_seconds": 600 if lane != "toolchain" else 180,
                "parser": "json",
                "expected_exit_code": expectation["exit_code"],
                "expected": expectation["observed"],
            }
        )
    spec = TaskPackageSpec.model_validate_json(
        json.dumps(
            {
                "package_id": "operational-metrics-specification-development",
                "family": "document",
                "task": str(task),
                "task_sha256": digest,
                "environment": {
                    "image_archive": str(image),
                    "image_sha256": image_digest,
                    "profile": "solver-isolated-v1",
                    "setup": [
                        "Frozen Node/pnpm/lock dependencies and Chrome-for-Testing ARM64; "
                        "see image recipe.",
                        "Disposable synthetic application state; "
                        "original source runs only in the guest.",
                    ],
                    "required_paths": [
                        "AGENTS.md",
                        "frontend/AGENTS.md",
                        "docs/project/BACKLOG.md",
                        "docs/system/DESIGN.md",
                        "src/api/v2/router.ts",
                        "src/db/v2-queries.ts",
                        "pnpm-lock.yaml",
                        "frontend/package.json",
                        "scripts/browser-runtime.cjs",
                        ".git/HEAD",
                        ".git/refs/heads/main",
                    ],
                    "coverage_gaps": [
                        "Matching known baseline failures/skips is not a clean test population.",
                        "Browser smoke validates the existing console/API, not the future feature.",
                        "No provider polling, real sessions, cross-browser suite "
                        "or host state is exercised.",
                    ],
                },
                "validation": [
                    {
                        "case_id": "selected-baseline",
                        "role": "baseline",
                        "input_bundle": contract.input_bundle,
                        "input_sha256": contract.input_sha256,
                        "lanes": lanes,
                    }
                ],
                "limitations": [
                    "Source exclusions remove historical specs/plans equally; "
                    "this is a declared context intervention.",
                    "Environment qualification grants neither semantic validity "
                    "nor scored admission.",
                    "Review raw characterization evidence before freezing expectations; "
                    "do not auto-approve observed failures.",
                ],
            }
        )
    )
    return prepare_package(spec, output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--task-sha256", required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--image-sha256", required=True)
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(
                args.task,
                args.task_sha256,
                args.image,
                args.image_sha256,
                args.expected,
                args.output,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
