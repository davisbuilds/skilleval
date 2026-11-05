#!/usr/bin/env python3
"""Opt-in context controls through production run_native, without real inference.

Runs OFF/ON/fresh OFF, a deliberately exposed marker, and a dirty-system refusal.
All operator state is synthetic and private. Never reads real credentials.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from skilleval import context_guard, evidence
from skilleval.gateway import GatewayLimits
from skilleval.identity import file_digest
from skilleval.runner import GUEST_ROOT, run_native
from skilleval.treatments import capture_skill


def _responder() -> Any:
    spec = importlib.util.spec_from_file_location(
        "native_fixture", Path(__file__).with_name("qualify-native.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def worker(spec_path: Path) -> int:
    spec = json.loads(spec_path.read_text())
    output = Path(spec["output"])
    captured: list[bytes] = []
    responder = _responder()
    selected = spec["arm"] == "available"
    command = "cat " + (
        GUEST_ROOT + "/codex-home/skills/isolation-control/SKILL.md"
        if selected
        else "/workspace/task/AGENTS.md"
    )

    def transport(raw: bytes, _headers: dict[str, str], _timeout: float) -> Any:
        captured.append(raw)
        body = json.loads(raw)
        if len(captured) == 1:
            item = responder.tool_item(body, command)
        elif len(captured) == 2:
            item = {
                "type": "message",
                "id": "msg_context",
                "role": "assistant",
                "phase": "final_answer",
                "status": "completed",
                "content": [{"type": "output_text", "text": "Synthetic context control."}],
            }
        else:
            raise ValueError("unexpected synthetic context request")
        return responder.SyntheticStream(
            responder.response_stream(item, f"resp_context_{len(captured)}")
        )

    report = run_native(
        Path(spec["input"]),
        spec["input_digest"],
        spec["prompt"],
        output,
        runtime=Path(spec["runtime"]),
        image=Path(spec["image"]),
        image_sha256=spec["image_sha256"],
        catalog=Path(spec["catalog"]),
        auth_file=None,
        synthetic_transport=transport,
        context_control="dirty-system" if spec["arm"] == "dirty-system" else None,
        native_timeout_seconds=60,
        limits=GatewayLimits(max_requests=3, timeout_seconds=20, lifetime_seconds=180),
        skill_bundle=Path(spec["skill"]) if selected else None,
        skill_digest=spec["skill_digest"] if selected else None,
    )
    expected = report["status"] == "completed" or spec["arm"] == "dirty-system"
    return 0 if expected and report["cleanup_complete"] and report["runtime_home_removed"] else 1


def _texts(body: dict[str, Any], *, instructions: bool = False) -> list[str]:
    texts = []
    for item in body.get("input", []):
        if item.get("type") != "message" or item.get("role") != "user":
            continue
        for part in item.get("content", []):
            text = part.get("text", "")
            if not instructions or text.startswith("# AGENTS.md instructions for "):
                texts.append(text)
    return texts


def assess(root: Path, task_marker: str, host_marker: str, skill_marker: str) -> dict[str, Any]:
    """Independently read retained original requests, native events and inventories."""
    controls: dict[str, bool] = {}
    audits = {}
    bodies = {}
    for arm in ("absent-1", "available", "absent-2", "exposed", "dirty-system"):
        directory = root / arm
        audit = json.loads((directory / "native-audit.json").read_text())
        audits[arm] = audit
        requests = [json.loads(p.read_bytes()) for p in sorted(directory.glob("request-*.json"))]
        bodies[arm] = requests
        controls[arm + ":cleanup"] = all(
            audit.get(k) is True
            for k in (
                "cleanup_complete",
                "runtime_home_removed",
                "observed_absent",
                "broker_clean",
            )
        )
        controls[arm + ":control_kind"] = audit["kind"] == "skilleval-native-context-control"
        if arm == "dirty-system":
            controls[arm + ":refused_before_request"] = (
                audit["status"] != "completed"
                and not requests
                and audit["request_count"] == 0
                and "unexpected ambient context path: /etc/codex"
                in audit.get("context", {}).get("error", "")
            )
            continue
        controls[arm + ":native_completed"] = audit["status"] == "completed" and len(requests) == 2
        first = requests[0] if requests else {}
        context = context_guard.request_context(json.dumps(first).encode())
        selected_entries = [
            item
            for catalog in context["skill_catalogs"]
            for item in catalog["skills"]
            if item["name"] == "isolation-control"
        ]
        controls[arm + ":selected_catalog"] = (
            selected_entries
            == [
                {
                    "name": "isolation-control",
                    "path": GUEST_ROOT + "/codex-home/skills/isolation-control/SKILL.md",
                }
            ]
            if arm == "available"
            else not selected_entries
        )
        instructions = _texts(first, instructions=True)
        controls[arm + ":task_instruction_positive"] = any(
            task_marker in text for text in instructions
        )
        controls[arm + ":host_marker"] = (
            any(host_marker in text for text in instructions)
            if arm == "exposed"
            else all(host_marker not in json.dumps(body) for body in requests)
        )
        controls[arm + ":environment"] = set(audit["process"]["environment"]) == {
            "PATH",
            "HOME",
            "CODEX_HOME",
            "LANG",
        }
        events = [
            json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()
        ]
        marker = skill_marker if arm == "available" else task_marker
        controls[arm + ":successful_native_read"] = any(
            event.get("type") == "item.completed"
            and event.get("item", {}).get("type") == "command_execution"
            and event["item"].get("status") == "completed"
            and event["item"].get("exit_code") == 0
            and marker in event["item"].get("aggregated_output", "")
            for event in events
        )
        controls[arm + ":selected_skill"] = (
            any(skill_marker in json.dumps(body) for body in requests)
            if arm == "available"
            else all(
                skill_marker not in json.dumps(body) and "isolation-control" not in json.dumps(body)
                for body in requests
            )
        )
        if arm == "available":
            controls[arm + ":complete_pinned_read"] = (
                audit["skill_consultation"].get("consultation") == "observed"
            )
            controls[arm + ":complete_body_in_request"] = any(
                row.get("skill_body_observed") is True for row in audit["request_observations"]
            )
    baseline = audits["absent-1"]
    for arm in ("available", "absent-2"):
        audit = audits[arm]
        controls[arm + ":same_task_and_harness"] = all(
            audit[key] == baseline[key]
            for key in (
                "identity",
                "input_digest",
                "image",
                "catalog_sha256",
                "native_config_sha256",
                "codex_identity",
            )
        )
        controls[arm + ":same_builtin_bytes"] = [
            row for row in audit["context"]["skills_after"] if row["path"].startswith(".system/")
        ] == [
            row for row in baseline["context"]["skills_after"] if row["path"].startswith(".system/")
        ]
        controls[arm + ":same_tools"] = (
            bool(context_guard.request_context(json.dumps(bodies[arm][0]).encode())["tools"])
            and context_guard.request_context(json.dumps(bodies[arm][0]).encode())["tools"]
            == context_guard.request_context(json.dumps(bodies["absent-1"][0]).encode())["tools"]
        )

        def shared_messages(body: dict[str, Any]) -> list[tuple[str, str]]:
            context = context_guard.request_context(json.dumps(body).encode())
            catalogs = {row["sha256"] for row in context["skill_catalogs"]}
            return [
                (row["role"], row["sha256"])
                for row in context["messages"]
                if row["role"] in {"system", "developer"} and row["sha256"] not in catalogs
            ]

        controls[arm + ":same_shared_instructions"] = shared_messages(
            bodies[arm][0]
        ) == shared_messages(bodies["absent-1"][0])
        base_context = context_guard.request_context(json.dumps(bodies["absent-1"][0]).encode())
        arm_context = context_guard.request_context(json.dumps(bodies[arm][0]).encode())
        controls[arm + ":same_top_level_instructions"] = (
            base_context["top_level_instructions"] == arm_context["top_level_instructions"]
        )

        def baseline_catalog(context: dict[str, Any]) -> list[dict[str, str]]:
            return [
                item
                for catalog in context["skill_catalogs"]
                for item in catalog["skills"]
                if item["name"] != "isolation-control"
            ]

        controls[arm + ":same_advertised_builtins"] = baseline_catalog(
            base_context
        ) == baseline_catalog(arm_context)
        prefix = "Use $isolation-control for this task.\n\n" if arm == "available" else ""
        controls[arm + ":declared_invocation_delta"] = (
            root / arm / "prompt.txt"
        ).read_text() == prefix + (root / "absent-1/prompt.txt").read_text()
    controls["fresh_session"] = audits["absent-1"]["execution"].get("thread_id") != audits[
        "absent-2"
    ]["execution"].get("thread_id")
    return {
        "schema_version": 1,
        "kind": "skilleval-context-qualification",
        "status": "passed" if all(controls.values()) else "failed",
        "controls": controls,
        "scope": "synthetic native context loading and exclusion; no model or task quality claim",
        "runs": {
            arm: {
                "audit_sha256": file_digest(root / arm / "native-audit.json"),
                "request_sha256": [
                    file_digest(p) for p in sorted((root / arm).glob("request-*.json"))
                ],
            }
            for arm in audits
        },
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    output = args.output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    operator = output / "synthetic-operator"
    source = operator / "Dev/task"
    source.mkdir(parents=True)
    task_marker, host_marker, skill_marker = [uuid.uuid4().hex for _ in range(3)]
    (source / "AGENTS.md").write_text("Shared task context: " + task_marker + "\n")
    (source.parent / "AGENTS.md").write_text("Synthetic operator context: " + host_marker)
    codex_home = operator / "home/.codex"
    codex_home.mkdir(parents=True)
    (codex_home / "AGENTS.md").write_text("Synthetic global context: " + host_marker)
    (codex_home / "config.toml").write_text('developer_instructions = "' + host_marker + '"\n')
    (codex_home / "sessions").mkdir()
    (codex_home / "sessions/prior.jsonl").write_text(host_marker)
    for relative in ("skills/ambient/SKILL.md", "memories/MEMORY.md", "plugins/ambient/README.md"):
        path = codex_home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\nname: ambient\ndescription: Synthetic ambient context.\n---\n" + host_marker
        )
    (codex_home / "hooks.json").write_text(
        json.dumps({"hooks": {}, "synthetic_marker": host_marker})
    )
    shell = operator / "shell-init.sh"
    shell.write_text("echo " + host_marker + "\n")
    task = output / "task"
    task_receipt = evidence.snapshot(source, task)
    selected = output / "selected"
    selected.mkdir()
    (selected / "SKILL.md").write_text(
        "---\nname: isolation-control\ndescription: Read the isolated control context.\n---\n"
        "Synthetic skill context: " + skill_marker + "\n"
    )
    skill = output / "skill"
    skill_receipt = capture_skill(
        selected, None, skill, source_url="synthetic:context-control", source_revision="1"
    )
    exposed = output / "exposed-source"
    exposed.mkdir()
    (exposed / "AGENTS.md").write_text((source / "AGENTS.md").read_text() + host_marker + "\n")
    exposed_bundle = output / "exposed-task"
    exposed_receipt = evidence.snapshot(exposed, exposed_bundle)
    environment = {
        **os.environ,
        "HOME": str(operator / "home"),
        "CODEX_HOME": str(codex_home),
        "BASH_ENV": str(shell),
        "SKILLEVAL_AMBIENT_CANARY": host_marker,
    }
    for arm in ("absent-1", "available", "absent-2", "exposed", "dirty-system"):
        spec = {
            "arm": arm,
            "output": str(output / arm),
            "runtime": str(args.runtime.resolve()),
            "image": str(args.image_archive.resolve()),
            "image_sha256": args.image_sha256,
            "catalog": str(args.catalog.resolve()),
            "skill": str(skill),
            "skill_digest": skill_receipt["manifest_sha256"],
            "input": str(exposed_bundle if arm == "exposed" else task),
            "input_digest": (exposed_receipt if arm == "exposed" else task_receipt)[
                "manifest_sha256"
            ],
            "prompt": ("Use $isolation-control for this task.\n\n" if arm == "available" else "")
            + "Read the available control context and report completion.",
        }
        spec_path = output / (arm + ".json")
        spec_path.write_text(json.dumps(spec, indent=2) + "\n")
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--worker", str(spec_path)],
            env=environment,
            check=False,
        )
        print(json.dumps({"arm": arm, "returncode": result.returncode}), flush=True)
        if result.returncode != 0:
            raise ValueError("context worker failed; preserve its recovery ticket")
    report = assess(output, task_marker, host_marker, skill_marker)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        return worker(Path(sys.argv[2]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--image-archive", type=Path, required=True)
    parser.add_argument("--image-sha256", required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    report = run(parser.parse_args())
    print(
        json.dumps(
            {
                "status": report["status"],
                "failed": [key for key, passed in report["controls"].items() if not passed],
            }
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
