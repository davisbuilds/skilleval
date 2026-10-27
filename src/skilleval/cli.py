"""Noninteractive, explicit-file CLI. Help and inspection have no runtime effects."""

import argparse
import json
import math
import os
import stat
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from skilleval import __version__
from skilleval.contracts import Assessment, Attempt, Experiment, Record, Task
from skilleval.errors import SkillEvalError

RECORDS: dict[str, type[Record]] = {
    "task": Task,
    "experiment": Experiment,
    "attempt": Attempt,
    "assessment": Assessment,
}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="skilleval", description=__doc__)
    root.add_argument("--version", action="version", version=f"skilleval {__version__}")
    commands = root.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture", help="Copy and inventory an explicit evidence tree")
    capture.add_argument("source", type=Path)
    capture.add_argument("--output", type=Path, required=True)
    importer = commands.add_parser("import-review", help="Import one historical review case")
    importer.add_argument("source", type=Path)
    importer.add_argument("--case", required=True)
    importer.add_argument("--output", type=Path, required=True)
    for name in ("verify", "inspect"):
        command = commands.add_parser(name, help=f"{name.title()} captured evidence")
        command.add_argument("bundle", type=Path)
        command.add_argument("--sha256", help="Expected manifest identity retained separately")
    schema = commands.add_parser("schema", help="Print a versioned record's JSON Schema")
    schema.add_argument("kind", choices=RECORDS)
    validate = commands.add_parser("validate", help="Validate record structure, not its truth")
    validate.add_argument("kind", choices=RECORDS)
    validate.add_argument("file", type=Path)
    review = commands.add_parser("review", help="Prepare evidence or record an assessment")
    review_commands = review.add_subparsers(dest="operation", required=True)
    prepare = review_commands.add_parser("prepare")
    prepare.add_argument("bundle", type=Path)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--include-reference", action="store_true")
    record = review_commands.add_parser("record")
    record.add_argument("bundle", type=Path)
    record.add_argument("assessment", type=Path)
    record.add_argument("--output", type=Path, required=True)
    qualification = commands.add_parser("qualification", help="Plan or run offline VM controls")
    qualification_commands = qualification.add_subparsers(dest="operation", required=True)
    plan = qualification_commands.add_parser("plan")
    plan.add_argument("bundle", type=Path)
    plan.add_argument("--image", required=True, help="OCI reference pinned with @sha256:")
    plan.add_argument("--commands", type=Path, required=True, help="JSON array of argv arrays")
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--cpus", type=int, default=2)
    plan.add_argument("--memory-mib", type=int, default=2048)
    run = qualification_commands.add_parser("run")
    run.add_argument("plan", type=Path)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--runtime", default="smolvm", help="Explicit trusted smolvm executable")
    return root


def read_json(path: Path) -> Any:
    """Read bounded control data; malformed documents must not trigger execution."""
    limit = 4 * 1024 * 1024
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise SkillEvalError("control input must be a regular file at most 4 MiB")
        contents = stream.read(limit + 1)
    if len(contents) > limit:
        raise SkillEvalError("control input exceeds 4 MiB")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise SkillEvalError("duplicate JSON object key")
            result[key] = value
        return result

    def invalid_constant(_: str) -> None:
        raise SkillEvalError("non-finite JSON number")

    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            invalid_constant(value)
        return parsed

    return json.loads(
        contents,
        object_pairs_hook=pairs,
        parse_constant=invalid_constant,
        parse_float=finite_float,
    )


def write_new_json(path: Path, value: object) -> None:
    """Explicit new control file; an existing file is never silently replaced."""
    data = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(data)


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "schema":
        return RECORDS[args.kind].model_json_schema()
    if args.command == "validate":
        document = read_json(args.file)
        record = RECORDS[args.kind].model_validate_json(json.dumps(document))
        return {"status": "valid-structure", "record": record.model_dump(mode="json")}
    if args.command in ("capture", "import-review", "verify", "inspect"):
        from skilleval.evidence import import_review, snapshot, verify

        if args.command == "capture":
            return snapshot(args.source, args.output)
        if args.command == "import-review":
            return import_review(args.source, args.case, args.output)
        manifest = verify(args.bundle, expected_digest=args.sha256)
        summary = {
            "status": "verified",
            "scope": "captured-byte-integrity",
            "kind": manifest["kind"],
            "file_count": len(manifest["files"]),
            "total_bytes": sum(entry["size"] for entry in manifest["files"]),
            "expected_digest_checked": args.sha256 is not None,
        }
        if args.command == "verify":
            summary["manifest"] = manifest
        return summary
    if args.command == "review":
        from skilleval.grading import prepare_review, record_assessment

        if args.operation == "prepare":
            return prepare_review(
                args.bundle, args.output, include_reference=args.include_reference
            )
        return record_assessment(args.bundle, args.assessment, args.output)
    if args.command == "qualification":
        from skilleval.qualification import plan_qualification, run_offline_qualification

        if args.operation == "plan":
            controls = read_json(args.commands)
            if not isinstance(controls, list) or not all(
                isinstance(row, list) and row and all(isinstance(arg, str) for arg in row)
                for row in controls
            ):
                raise SkillEvalError("commands must be a nonempty JSON array of argv arrays")
            plan = plan_qualification(
                args.bundle, args.image, controls, cpus=args.cpus, memory_mib=args.memory_mib
            )
            write_new_json(args.output, plan)
            return {"status": "planned", "path": str(args.output), "plan": plan}
        value = read_json(args.plan)
        if not isinstance(value, dict):
            raise SkillEvalError("qualification plan must be a JSON object")
        return run_offline_qualification(value, args.output, runtime_binary=args.runtime)
    raise SkillEvalError("unknown command")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = dispatch(args)
    except KeyboardInterrupt:
        print(json.dumps({"status": "interrupted"}), file=sys.stderr)
        return 130
    except ValidationError as exc:
        fields = [".".join(str(part) for part in item["loc"]) for item in exc.errors()]
        error = {"status": "error", "error": "invalid record", "fields": fields}
        print(json.dumps(error), file=sys.stderr)
        return 2
    except (SkillEvalError, OSError, ValueError, RecursionError) as exc:
        # Avoid echoing untrusted file content or exception representations.
        message = str(exc) if isinstance(exc, SkillEvalError) else type(exc).__name__
        print(json.dumps({"status": "error", "error": message}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    return 1 if result.get("status") in ("failed", "incomplete", "error", "partial") else 0


if __name__ == "__main__":
    raise SystemExit(main())
