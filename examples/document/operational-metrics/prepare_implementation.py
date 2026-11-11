"""Freeze downstream acceptance and observation routes without executing application code.

A handoff supplies the implementer's exact input and prompt. Authored controls can
use prepare_contract directly with an explicitly captured baseline. The original
request and repository contracts, never the generated specification, govern grading.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from skilleval import acceptance, handoff, repair, runner, task_package
from skilleval.errors import SkillEvalError
from skilleval.identity import file_digest

EXAMPLE = Path(__file__).resolve().parent
WORKER = """import json,sys
from pathlib import Path
root=Path(sys.argv[1])
requests=json.load(sys.stdin)["requests"]
if any(item != {"check":"source-present"} for item in requests):
    raise ValueError("unsupported source observation")
print(json.dumps({"observations":[{"source_present":all((root/name).is_file() for name in
    ("frontend/package.json","src/api/v2/router.ts","src/db/v2-queries.ts"))} for _ in requests]}))
"""


def _helper():
    spec = importlib.util.spec_from_file_location(
        "downstream_preparation", EXAMPLE / "downstream/prepare.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare_contract(
    source_package: Path,
    source_package_sha256: str,
    input_bundle: Path,
    input_sha256: str,
    prompt: str,
    output: Path,
) -> dict:
    """Prepare a repair-family task + original-need map; no quality judgment."""
    original = task_package.load_package(source_package, source_package_sha256)
    source_task = repair.load_task(Path(original.task), original.task_sha256)
    _, files = runner._snapshot_files(input_bundle, input_sha256)
    output = output.absolute()
    if output.resolve().is_relative_to(input_bundle.resolve()):
        raise SkillEvalError("implementation preparation must stay outside solver input")
    output.mkdir(parents=True, exist_ok=False)
    worker = output / "source-worker.py"
    worker.write_text(WORKER)
    names = [name for name, _, _ in files if not repair.ignored(name)]
    task = repair.RepairSpec(
        task_id="operational-metrics-implementation-development",
        input_bundle=str(input_bundle.absolute()),
        input_sha256=input_sha256,
        prompt=prompt,
        editable_paths=tuple(name for name in names if "/" not in name),
        editable_directories=tuple(sorted({name.split("/")[0] for name in names if "/" in name})),
        worker=str(worker),
        worker_sha256=file_digest(worker),
        probes=(
            repair.RepairProbe(
                probe_id="source-present",
                request={"check": "source-present"},
                expected={"source_present": True},
            ),
        ),
        timeout_seconds=30,
        source_url=source_task.source_url,
        source_revision=source_task.source_revision,
    )
    saved = repair.prepare_repair(task, output / "task.json")
    baseline = next(case for case in original.validation if case.role == "baseline")
    package = original.model_copy(
        update={
            "package_id": "operational-metrics-implementation-development",
            "family": "repair",
            "task": saved["path"],
            "task_sha256": saved["sha256"],
            "validation": (
                baseline.model_copy(
                    update={
                        "input_bundle": str(input_bundle.absolute()),
                        "input_sha256": input_sha256,
                    }
                ),
            ),
            "limitations": (
                *original.limitations,
                "Prepared implementation environment declaration; no new validation is implied.",
                "The generated spec is an implementation input, not an acceptance oracle.",
            ),
        }
    )
    packaged = task_package.prepare_package(package, output / "package.json")
    contract = json.loads((EXAMPLE / "controls/downstream-contract.json").read_bytes())
    requirements = []
    for row in contract["required_observations"]:
        criterion = (
            "scope-and-regressions"
            if row["observation_id"] == "scope-and-console-regression"
            else "task-contract"
        )
        requirements.append(
            {
                "requirement_id": row["observation_id"],
                "statement": row["expected"],
                "role": "regression" if criterion == "scope-and-regressions" else "core",
                "criticality": "required",
                "source_rationale": row["source_rationale"],
                "mechanical": [],
                "semantic": [{"criterion": criterion, "question": row["check"]}],
                "gaps": [
                    "Browser observations cover only the selected driver and fixtures; "
                    "unavailable or incomplete paths remain unresolved."
                ],
            }
        )
    mapped = acceptance.AcceptanceMapSpec.model_validate_json(
        json.dumps(
            {
                "map_id": "operational-metrics-implementation-development",
                "task": saved["path"],
                "task_sha256": saved["sha256"],
                "package": packaged["path"],
                "package_sha256": packaged["sha256"],
                "purpose": (
                    "Assess a read-only operational-metrics console against the original "
                    "work request and repository contracts; "
                    "the generated specification cannot redefine success."
                ),
                "population": "Exposed development task, not an independent held-out population.",
                "selection_rationale": "Requested console feature with a shipped read API.",
                "exposure_history": "Authored checks and related controls; not independent.",
                "authored_after_outcomes": False,
                "requirements": requirements,
                "unmapped_probes": [
                    {
                        "probe_id": "source-present",
                        "reason": "Source availability alone is not feature acceptance.",
                    }
                ],
                "controls": [],
                "review_evidence": [],
                "open_questions": ["Independent domain and semantic-grader audit remains pending."],
                "limitations": [
                    contract["authority"],
                    *contract["valid_alternatives"],
                    "Map validation does not establish semantic validity or scored admission.",
                ],
            }
        )
    )
    mapped_receipt = acceptance.prepare_acceptance(mapped, output / "acceptance-map.json")
    receipt = {
        "task": saved,
        "package": packaged,
        "acceptance_map": mapped_receipt,
        "original_request": source_task.prompt,
        "semantic_acceptance": "unassessed",
        "scored_admitted": False,
    }
    repair._publish(output / "preparation.json", receipt)
    return receipt


def prepare_from_handoff(
    handoff_path, handoff_sha256, source_package, source_package_sha256, output
):
    value = handoff.load_handoff(handoff_path, handoff_sha256)
    package = task_package.load_package(source_package, source_package_sha256)
    if (
        package.task_sha256 != value["source_spec"]["task_sha256"]
        or Path(package.task).resolve() != Path(value["source_spec"]["task"]).resolve()
    ):
        raise SkillEvalError("implementation source package differs from original writer task")
    return prepare_contract(
        source_package,
        source_package_sha256,
        handoff_path / "input",
        value["input_sha256"],
        value["prompt"],
        output,
    )


def prepare_observation(
    task_path, task_sha256, result_path, result_sha256, package_path, package_sha256, driver, output
):
    """Bind a selected operator browser driver to the exact collected candidate."""
    task = repair.load_task(task_path, task_sha256)
    result, _ = repair.result_files(task, task_sha256, result_path, result_sha256)
    package = task_package.load_package(package_path, package_sha256)
    if Path(package.task).resolve() != task_path.resolve() or package.task_sha256 != task_sha256:
        raise SkillEvalError("observation package belongs to another task")
    argv = _helper().lane_argv(
        driver, Path(task.input_bundle), task.input_sha256, original_request=task.prompt
    )
    baseline = task_package.ValidationCase(
        case_id="baseline",
        role="baseline",
        input_bundle=task.input_bundle,
        input_sha256=task.input_sha256,
        lanes=(
            task_package.ValidationLane(
                lane_id="source-identity",
                purpose="Baseline availability only; not environment requalification",
                argv=(
                    "python3",
                    "-I",
                    "-c",
                    "import json,pathlib; print(json.dumps({'source_available':"
                    "pathlib.Path('frontend/package.json').is_file()}))",
                ),
                parser="json",
                expected_exit_code=0,
                expected={"source_available": True},
            ),
        ),
    )
    candidate = task_package.ValidationCase(
        case_id="candidate",
        role="reference",
        input_bundle=str(result_path.absolute() / "candidate"),
        input_sha256=result["candidate_sha256"],
        lanes=(
            task_package.ValidationLane(
                lane_id="browser-observation",
                purpose="Retain raw observations; equality is not a semantic verdict",
                argv=argv,
                timeout_seconds=600,
                parser="json",
                expected_exit_code=0,
                expected={},
            ),
        ),
    )
    environment = package.environment.model_copy(
        update={"required_paths": ("frontend/package.json",), "profile": "solver-isolated-v1"}
    )
    selected = package.model_copy(
        update={
            "package_id": "operational-metrics-candidate-observations",
            "environment": environment,
            "validation": (baseline, candidate),
            "limitations": (
                *package.limitations,
                "Dynamic envelopes have no equality answer key; "
                "matched/unexpected is not feature correctness.",
                "Judge consumes retained observations, source, original request "
                "and mapped requirements independently.",
            ),
        }
    )
    return task_package.prepare_package(selected, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--handoff", type=Path, required=True)
    prepare.add_argument("--handoff-sha256", required=True)
    prepare.add_argument("--source-package", type=Path, required=True)
    prepare.add_argument("--source-package-sha256", required=True)
    prepare.add_argument("--output", type=Path, required=True)
    observe = commands.add_parser("observe")
    for name in ("task", "result", "package"):
        observe.add_argument("--" + name, type=Path, required=True)
        observe.add_argument("--" + name + "-sha256", required=True)
    observe.add_argument("--driver", type=Path, required=True)
    observe.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare_from_handoff(
            args.handoff,
            args.handoff_sha256,
            args.source_package,
            args.source_package_sha256,
            args.output,
        )
    else:
        result = prepare_observation(
            args.task,
            args.task_sha256,
            args.result,
            args.result_sha256,
            args.package,
            args.package_sha256,
            args.driver,
            args.output,
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
