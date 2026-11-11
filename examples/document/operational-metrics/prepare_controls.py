"""Prepare authored controls, then finalize only from real completed VM checks.

Neither phase executes application code, workers, a VM, or a model. Expected
labels remain in the operator receipt and never enter the assessment packet.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from skilleval import acceptance, evidence, repair, runner
from skilleval.errors import SkillEvalError
from skilleval.identity import file_digest
from skilleval.repair_assessment import (
    DocumentAssessmentSpec,
    RepairCheckSource,
    checked_report,
    prepare_assessment,
)

EXAMPLE = Path(__file__).resolve().parent


def prepare(
    task: Path,
    task_sha256: str,
    acceptance_map: Path,
    acceptance_map_sha256: str,
    output: Path,
    *,
    runtime: Path,
    image: Path,
    image_sha256: str,
) -> dict:
    """Capture eight candidates and print commands for explicit guest checks."""
    task, acceptance_map, output = (p.absolute() for p in (task, acceptance_map, output))
    contract = repair.load_task(task, task_sha256)
    mapped = acceptance.load_acceptance(acceptance_map, acceptance_map_sha256)
    if Path(mapped.task).absolute() != task or mapped.task_sha256 != task_sha256:
        raise SkillEvalError("control acceptance map belongs to another task")
    if output.resolve().is_relative_to(Path(contract.input_bundle).resolve()):
        raise SkillEvalError("authored controls must stay outside solver input")
    _, files = runner._snapshot_files(Path(contract.input_bundle), contract.input_sha256)
    if any(name.startswith("docs/specs/") for name, _, _ in files):
        raise SkillEvalError("control baseline already contains specification outputs")
    manifest = json.loads((EXAMPLE / "controls/manifest.json").read_bytes())
    requirement_ids = {r.requirement_id for r in mapped.requirements}
    if any(set(c["expected_requirements"]) != requirement_ids for c in manifest["cases"]):
        raise SkillEvalError("authored labels must cover the selected requirements exactly")
    output.mkdir()
    rows = []
    for case in manifest["cases"]:
        directory = output / case["case_id"]
        directory.mkdir()
        with tempfile.TemporaryDirectory(prefix="skilleval-authored-") as temporary:
            source = Path(temporary).resolve()
            for name, raw, executable in files:
                target = source / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                target.chmod(0o755 if executable else 0o644)
            if case["candidate"] is not None:
                target = source / "docs/specs/authored-candidate.markdown"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((EXAMPLE / "controls" / case["candidate"]).read_bytes())
            captured = evidence.snapshot(source, directory / "candidate")
        collected = repair.collect_control(
            task,
            task_sha256,
            directory / "candidate",
            captured["manifest_sha256"],
            directory / "result",
        )
        if collected["status"] != "collected":
            raise SkillEvalError("authored control changed files outside the task scope")
        ref = {
            "task": str(task),
            "task_sha256": task_sha256,
            "result": collected["path"],
            "result_sha256": collected["sha256"],
        }
        rows.append(
            {
                "case_id": case["case_id"],
                "source": ref,
                "check_report": str(directory / "check/repair-check.json"),
                "check_argv": [
                    "uv",
                    "run",
                    "skilleval",
                    "repair",
                    "check",
                    str(task),
                    "--task-sha256",
                    task_sha256,
                    "--result",
                    collected["path"],
                    "--result-sha256",
                    collected["sha256"],
                    "--runtime",
                    str(runtime.absolute()),
                    "--image-archive",
                    str(image.absolute()),
                    "--image-sha256",
                    image_sha256,
                    "--output",
                    str(directory / "check"),
                ],
                "expected_observation": case["expected_observation"],
                "expected_categories": case["expected"],
                "expected_requirements": case["expected_requirements"],
                "rationale": case["rationale"],
            }
        )
    receipt = {
        "schema_version": 1,
        "kind": "authored-specification-controls",
        "acceptance_map": str(acceptance_map),
        "acceptance_map_sha256": acceptance_map_sha256,
        "control_manifest_sha256": file_digest(EXAMPLE / "controls/manifest.json"),
        "model_attempt": False,
        "independent_audit": "required-pending",
        "cases": rows,
    }
    receipt_path = output / "controls.json"
    runner._exclusive(receipt_path, evidence._json_bytes(receipt))
    return {"path": str(receipt_path), "sha256": file_digest(receipt_path), "cases": rows}


def finalize(prepared: Path, prepared_sha256: str, output: Path) -> dict:
    """Validate all check receipts before preparing fresh native judge packets."""
    if file_digest(prepared) != prepared_sha256:
        raise SkillEvalError("authored control preparation digest differs")
    data = json.loads(prepared.read_bytes())
    if data.get("kind") != "authored-specification-controls":
        raise SkillEvalError("not an authored control preparation")
    acceptance.load_acceptance(Path(data["acceptance_map"]), data["acceptance_map_sha256"])
    specs = []
    for case in data["cases"]:
        report = Path(case["check_report"])
        ref = RepairCheckSource(
            **case["source"], report=str(report), report_sha256=file_digest(report)
        )
        observed = checked_report(ref)
        if observed["status"] != "completed":
            raise SkillEvalError("authored control needs a completed real VM check")
        # A completed missing-output control legitimately fails the task probe.
        # Retain the actual observation; never synthesize its expected outcome.
        spec = DocumentAssessmentSpec.model_validate_json(
            json.dumps(
                {
                    **case["source"],
                    "checks": [ref.model_dump(mode="json")],
                    "assessment_kind": "document",
                    "rubric_profile": "specification-v1",
                    "requirement_policy": "acceptance-requirements-v1",
                    "acceptance_map": data["acceptance_map"],
                    "acceptance_map_sha256": data["acceptance_map_sha256"],
                    "investigation": True,
                    "tool_evidence_policy": "execution-context-v3",
                    "authored_statement": (
                        "Authored candidate; no model work or verification claims."
                    ),
                    "limitations": [
                        "Exposed authored development control, not a model attempt or holdout.",
                        "The guest worker observes artifact presence/readability only; semantic "
                        "completion and requirement satisfaction require separate judgment.",
                        "No independent human audit, scored admission or skill-effect estimate.",
                    ],
                }
            )
        )
        specs.append((case["case_id"], spec))
    output = output.absolute()
    output.mkdir()
    rows = []
    for case_id, spec in specs:
        directory = output / case_id
        directory.mkdir()
        spec_path = directory / "assessment-spec.json"
        runner._exclusive(spec_path, evidence._json_bytes(spec.model_dump(mode="json")))
        packet = prepare_assessment(spec, directory / "packet")
        runner._exclusive(directory / "prepared-packet.json", evidence._json_bytes(packet))
        rows.append(
            {
                "case_id": case_id,
                "spec": str(spec_path),
                "packet": packet["path"],
                "packet_sha256": packet["packet_sha256"],
            }
        )
    result = {"schema_version": 1, "prepared_sha256": prepared_sha256, "cases": rows}
    runner._exclusive(output / "assessments.json", evidence._json_bytes(result))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--task", type=Path, required=True)
    prep.add_argument("--task-sha256", required=True)
    prep.add_argument("--acceptance-map", type=Path, required=True)
    prep.add_argument("--acceptance-map-sha256", required=True)
    prep.add_argument("--runtime", type=Path, required=True)
    prep.add_argument("--image", type=Path, required=True)
    prep.add_argument("--image-sha256", required=True)
    done = commands.add_parser("finalize")
    done.add_argument("--prepared", type=Path, required=True)
    done.add_argument("--prepared-sha256", required=True)
    for command in (prep, done):
        command.add_argument("--output", type=Path, required=True)
    args = vars(parser.parse_args())
    operation = args.pop("operation")
    print(
        json.dumps(prepare(**args) if operation == "prepare" else finalize(**args), sort_keys=True)
    )


if __name__ == "__main__":
    main()
