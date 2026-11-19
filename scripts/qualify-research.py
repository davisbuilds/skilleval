#!/usr/bin/env python3
"""Opt-in native hosted-web research and document-assessment route control.

Supply an explicitly selected runtime, pinned image, model catalog, host auth file
and fresh external output directory. This runs paid/subscription inference and
hosted web tools only when explicitly invoked. No skills, paired experiment,
real research topic selection or semantic reliability claim is involved. Full
hosted page text is opaque; model-written notes are not authenticated captures.
Keep execution code unchanged while any VM is active. Interrupted evidence is
retained; --recover recovers one ticket after its controller has stopped.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
from pathlib import Path
from typing import Any

from skilleval import evidence, repair, repair_assessment, repair_check
from skilleval.gateway import native_limits
from skilleval.identity import file_digest, snapshot_identity
from skilleval.lifecycle import recover
from skilleval.runner import run_native
from skilleval.study import _clean_native

REPO = Path(__file__).resolve().parents[1]
MARKER = "RESEARCH_LOCAL_REPLAY_OK"
SOURCE_URL = "https://docs.python.org/3/library/urllib.parse.html#url-parsing-security"
PROMPT = f"""Write report.md for a Python developer deciding whether urlsplit alone can
validate an untrusted redirect destination. Use the native web tool to search for
the official Python urllib.parse security documentation, then explicitly open
that official page and inspect the relevant passages. Explain what parsing does
and does not establish, an appropriate validation approach and material limits.
Cite the consulted official URL and identify the Python documentation version.
Keep the report concise. Preserve failures or inaccessible source passages as
uncertainty. Include relevant short excerpts with context, clearly labeled as
your model-written source notes rather than authenticated page captures.
After searching and opening the page, run a local shell command that prints
exactly {MARKER}, inspect its successful output, then write report.md. Use no
external shell/network client, package installation or persistent service.
Deliver only report.md; leave the ordinary input context unchanged.
"""
WORKER = '''"""Guest-only mechanical control; does not grade meaning or source support."""
import json
import sys
from pathlib import Path
root = Path(sys.argv[1])
path = root / "report.md"
valid = path.is_file() and 0 < path.stat().st_size <= 65536
text = path.read_text() if valid else ""
values = {"report_delivered": valid, "official_reference": "https://docs.python.org/" in text}
requests = json.load(sys.stdin)["requests"]
print(json.dumps({"observations": [values[item["check"]] for item in requests]}))
'''


def event_observations(raw: bytes) -> dict[str, Any]:
    """Require completed actions; config, starts and URL-looking output are insufficient."""
    search: list[int] = []
    opened: list[int] = []
    local: list[int] = []
    for index, line in enumerate(raw.splitlines(), 1):
        event = json.loads(line)
        if not isinstance(event, dict) or event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("status") not in (None, "completed"):
            continue
        if item.get("type") in ("web_search", "web_search_call"):
            action = item.get("action")
            if isinstance(action, dict):
                if action.get("type") == "search":
                    search.append(index)
                elif action.get("type") in ("open_page", "openPage", "open"):
                    opened.append(index)
        if (
            item.get("type") == "command_execution"
            and item.get("exit_code") == 0
            and isinstance(item.get("aggregated_output"), str)
            and MARKER in item["aggregated_output"].splitlines()
        ):
            local.append(index)
    return {
        "search_completed": bool(search),
        "open_completed": bool(opened),
        "local_marker_completed": bool(local),
        "hosted_then_local_replay": any(
            s < o < command for s in search for o in opened for command in local
        ),
        "search_event_lines": search,
        "open_event_lines": opened,
        "local_marker_event_lines": local,
    }


def save(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def prepare_control(root: Path) -> tuple[repair.RepairSpec, dict[str, Any]]:
    """Prepare a genuine ordinary file task; the worker/expected values stay host-side."""
    source = root / "source"
    source.mkdir()
    (source / "context.md").write_text(
        "Write a developer-facing answer in report.md.\n"
        "The cited official documentation is an investigation lead, not a retained source.\n"
        f"Official documentation lead: {SOURCE_URL}\n"
    )
    captured = evidence.snapshot(source, root / "input")
    worker = root / "mechanical-worker.py"
    worker.write_text(WORKER)
    spec = repair.RepairSpec(
        task_id="native-research-route-control",
        input_bundle=str(root / "input"),
        input_sha256=captured["manifest_sha256"],
        prompt=PROMPT,
        editable_paths=("report.md",),
        worker=str(worker),
        worker_sha256=file_digest(worker),
        probes=tuple(
            repair.RepairProbe(probe_id=name, request={"check": name}, expected=True)
            for name in ("report_delivered", "official_reference")
        ),
        source_url=SOURCE_URL,
        source_revision="authored-runtime-control-v1",
    )
    save(root / "task-spec.json", spec.model_dump(mode="json"))
    return spec, repair.prepare_repair(spec, root / "task.json")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--runtime", type=Path)
    result.add_argument("--image-archive", type=Path)
    result.add_argument("--image-sha256")
    result.add_argument("--catalog", type=Path, help="Qualified Codex catalog for this web route")
    result.add_argument("--auth-file", type=Path, help="Explicit host auth; never copied into VM")
    result.add_argument("--output", type=Path, help="Fresh private directory outside this checkout")
    result.add_argument(
        "--timeout-seconds",
        type=int,
        default=300,
        help="Per native run deadline (default: 300; maximum: 900)",
    )
    result.add_argument(
        "--max-requests",
        type=int,
        default=64,
        help="Mechanical inference backstop per native run (default: 64)",
    )
    result.add_argument("--recover", type=Path)
    return result


def main() -> int:
    cli = parser()
    args = cli.parse_args()
    run_values = (
        args.runtime,
        args.image_archive,
        args.image_sha256,
        args.catalog,
        args.auth_file,
        args.output,
    )
    if args.recover:
        if any(run_values):
            cli.error("--recover cannot be combined with run arguments")
        ticket = recover(args.recover)
        print(json.dumps(ticket, indent=2, sort_keys=True))
        return 0 if ticket["cleanup_complete"] else 1
    if not all(run_values):
        cli.error(
            "provide --runtime, --image-archive, --image-sha256, --catalog, "
            "--auth-file and --output, or --recover"
        )
    if not re.fullmatch(r"[a-f0-9]{64}", args.image_sha256):
        cli.error("--image-sha256 must be 64 lowercase hexadecimal characters")
    if not 1 <= args.timeout_seconds <= 900 or not 1 <= args.max_requests <= 1024:
        cli.error("invalid native deadline or request backstop")
    root = args.output.resolve()
    if root.is_relative_to(REPO) or REPO.is_relative_to(root):
        cli.error("--output must be outside this checkout")
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError:
        cli.error("--output must be a fresh directory; existing evidence is never replaced")
    summary: dict[str, Any] = {
        "kind": "skilleval-research-qualification",
        "schema_version": 1,
        "status": "incomplete",
        "host": socket.gethostname(),
        "checks": {},
        "cleanup": [],
        "scope": "Authored native hosted-web/local replay and research-assessment route control.",
        "limitations": [
            "Full hosted page text is opaque; retained notes are model-authored, unauthenticated.",
            "Route success is separate from content verdicts; "
            "unresolved and violated stay visible.",
            "No independent grader audit, general reliability, admission or skill-effect claim.",
        ],
    }

    def check(name: str, passed: bool) -> None:
        summary["checks"][name] = bool(passed)
        save(root / "qualification.json", summary)
        if not passed:
            raise AssertionError(name)

    def native(
        bundle: Path,
        digest: str,
        prompt: str,
        output: Path,
        *,
        mode: str,
        investigation: bool | None = None,
    ) -> dict[str, Any]:
        return run_native(
            bundle,
            digest,
            prompt,
            output,
            runtime=args.runtime.resolve(),
            image=args.image_archive.resolve(),
            image_sha256=args.image_sha256,
            catalog=args.catalog.resolve(),
            auth_file=args.auth_file.resolve(),
            mode=mode,
            investigation=investigation,
            web_search=True,
            native_timeout_seconds=args.timeout_seconds,
            limits=native_limits(args.timeout_seconds, max_requests=args.max_requests),
        )

    try:
        before = snapshot_identity(str(args.runtime.resolve()))
        save(root / "source-identity-before.json", before)
        summary["control_sha256"] = file_digest(Path(__file__))
        check("image-pin", file_digest(args.image_archive) == args.image_sha256)
        spec, task = prepare_control(root)
        solver = native(
            root / "input", spec.input_sha256, spec.prompt, root / "solver", mode="repair"
        )
        save(root / "solver-report.json", solver)
        check("solver-completed", solver.get("status") == "completed")
        observed = event_observations((root / "solver/events.jsonl").read_bytes())
        summary["solver_actions"] = observed
        for name in ("search_completed", "open_completed", "hosted_then_local_replay"):
            check("solver-" + name, observed[name])
        collected = repair.collect_repair(
            Path(task["path"]), task["sha256"], root / "solver", root / "candidate"
        )
        save(root / "candidate-selection.json", collected)
        check("candidate-in-scope", collected["status"] == "collected")
        checked = repair_check.check_repair(
            Path(task["path"]),
            task["sha256"],
            root / "candidate",
            collected["sha256"],
            root / "mechanical-check",
            runtime=args.runtime.resolve(),
            image=args.image_archive.resolve(),
            image_sha256=args.image_sha256,
        )
        check("mechanical-report-only", checked.get("accepted_by_checks") is True)
        reference = {
            "task": task["path"],
            "task_sha256": task["sha256"],
            "result": collected["path"],
            "result_sha256": collected["sha256"],
        }
        assessment_spec = repair_assessment.DocumentAssessmentSpec(
            **reference,
            checks=(
                repair_assessment.RepairCheckSource(
                    **reference,
                    report=str(root / "mechanical-check/repair-check.json"),
                    report_sha256=file_digest(root / "mechanical-check/repair-check.json"),
                ),
            ),
            rubric_profile="research-v1",
            web_search=True,
            investigation=True,
            tool_evidence_policy="execution-context-v3",
            limitations=(
                "Authored route control, no independent semantic audit.",
                "No authenticated full hosted source capture; original source passages "
                "are absent. Model-written report excerpts and judge notes cannot "
                "authenticate themselves; preserve resulting unresolved support.",
            ),
        )
        save(root / "assessment-spec.json", assessment_spec.model_dump(mode="json"))
        prepared = repair_assessment.prepare_assessment(assessment_spec, root / "packet")
        save(root / "prepared-assessment.json", prepared)
        judge_run = native(
            root / "packet",
            prepared["input_digest"],
            prepared["prompt"],
            root / "judge",
            mode="judge",
            investigation=True,
        )
        save(root / "judge-report.json", judge_run)
        check("judge-completed", judge_run.get("status") == "completed")
        judge_actions = event_observations((root / "judge/events.jsonl").read_bytes())
        summary["judge_actions"] = judge_actions
        for name in ("search_completed", "open_completed"):
            check("judge-" + name, judge_actions[name])
        recorded = repair_assessment.record_assessment(
            assessment_spec, root / "packet", root / "judge", root / "assessments"
        )
        save(root / "recorded-assessment.json", recorded)
        readback, restored = repair_assessment.read_assessment(
            Path(recorded["path"]), recorded["sha256"]
        )
        result = json.loads((Path(recorded["path"]) / "result.json").read_bytes())
        judgments = result["judgments"]
        check(
            "five-cited-judgments",
            len(judgments) == 5
            and {item["criterion"] for item in judgments}
            == set(repair_assessment.RESEARCH_CRITERIA)
            and all(item["citations"] or item["artifact_citations"] for item in judgments),
        )
        check("assessment-readback", restored == assessment_spec and bool(readback))
        summary["content_verdicts"] = {item["criterion"]: item["verdict"] for item in judgments}
        summary["content_audit"] = recorded["assessment"]
        summary["content_followup_required"] = any(
            item["verdict"] != "satisfied" for item in judgments
        )
        check(
            "execution-source-stable",
            snapshot_identity(str(args.runtime.resolve())) == before
            and file_digest(Path(__file__)) == summary["control_sha256"],
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
            except Exception as error:
                summary["cleanup"].append(
                    {
                        "directory": str(directory),
                        "clean": False,
                        "error": f"{type(error).__name__}: {error}",
                    }
                )
        if not all(item["clean"] for item in summary["cleanup"]):
            summary["status"] = "failed-cleanup"
        save(root / "qualification.json", summary)
    if summary["status"] != "passed":
        raise RuntimeError("qualification incomplete: inspect retained qualification.json")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
