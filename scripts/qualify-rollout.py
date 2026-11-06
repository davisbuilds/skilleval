#!/usr/bin/env python3
"""Opt-in native rollout/summary control through production run_native.

Default: synthetic host inference, real pinned Codex inside a fresh VM. Supplying
--auth-file selects the existing host credential route for bounded real inference.
Execution source must remain frozen while the VM is active.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

from skilleval import evidence
from skilleval.gateway import GatewayLimits
from skilleval.identity import file_digest
from skilleval.runner import run_native

SUMMARY = "Authored readable summary confirms the retention control stream."
MARKER = "ROLLOUT_NATIVE_CONTROL_OK"
PROMPT = (
    "Run a local shell command that prints exactly ROLLOUT_NATIVE_CONTROL_OK, "
    "check its successful output, and report completion."
)


def _responder() -> Any:
    spec = importlib.util.spec_from_file_location(
        "native_fixture", Path(__file__).with_name("qualify-native.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def response(items: list[dict[str, Any]], identifier: str) -> bytes:
    """Author readable summaries as an explicit positive detector control."""
    events: list[dict[str, Any]] = [
        {"type": "response.created", "response": {"id": identifier, "object": "response"}}
    ]
    for index, item in enumerate(items):
        events.append(
            {
                "type": "response.output_item.added",
                "output_index": index,
                "item": {**item, "summary": []} if item["type"] == "reasoning" else item,
            }
        )
        if item["type"] == "reasoning":
            scope = {"item_id": item["id"], "output_index": index, "summary_index": 0}
            events.extend(
                [
                    {
                        "type": "response.reasoning_summary_part.added",
                        **scope,
                        "part": {"type": "summary_text", "text": ""},
                    },
                    {"type": "response.reasoning_summary_text.delta", **scope, "delta": SUMMARY},
                    {
                        "type": "response.reasoning_summary_part.done",
                        **scope,
                        "part": {"type": "summary_text", "text": SUMMARY},
                    },
                ]
            )
        if item["type"] == "message":
            events.append(
                {
                    "type": "response.output_text.delta",
                    "output_index": index,
                    "content_index": 0,
                    "item_id": item["id"],
                    "delta": item["content"][0]["text"],
                }
            )
        events.append({"type": "response.output_item.done", "output_index": index, "item": item})
    events.append(
        {
            "type": "response.completed",
            "response": {
                "id": identifier,
                "object": "response",
                "status": "completed",
                "output": items,
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            },
        }
    )
    return b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--image-sha256", required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--auth-file", type=Path)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    source = root / "source"
    source.mkdir()
    (source / "README.md").write_text("A small local command control.\n")
    snapshot = root / "input"
    capture = evidence.snapshot(source, snapshot)
    helper = _responder()
    calls = 0

    def transport(raw: bytes, _headers: dict[str, str], _timeout: float) -> Any:
        nonlocal calls
        calls += 1
        body = json.loads(raw)
        if body.get("reasoning", {}).get("summary") != "auto":
            raise ValueError("control did not send the configured reasoning summary parameter")
        if calls == 1:
            items = [
                {
                    "type": "reasoning",
                    "id": "rs_rollout_control",
                    "summary": [{"type": "summary_text", "text": SUMMARY}],
                },
                helper.tool_item(body, "printf '" + MARKER + "\\n'"),
            ]
        elif calls == 2:
            if MARKER not in raw.decode():
                raise ValueError("successful command output was not returned")
            items = [
                {
                    "type": "message",
                    "id": "msg_rollout_control",
                    "role": "assistant",
                    "phase": "final_answer",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": "Local command control completed."}
                    ],
                }
            ]
        else:
            raise ValueError("unexpected control request")
        return helper.SyntheticStream(response(items, f"resp_rollout_{calls}"))

    report = run_native(
        snapshot,
        capture["manifest_sha256"],
        PROMPT,
        root / "run",
        runtime=args.runtime.resolve(),
        image=args.image.resolve(),
        image_sha256=args.image_sha256,
        catalog=args.catalog.resolve(),
        auth_file=args.auth_file,
        synthetic_transport=transport if args.auth_file is None else None,
        native_timeout_seconds=180,
        limits=GatewayLimits(max_requests=4, timeout_seconds=60, lifetime_seconds=300),
    )
    rollouts = report.get("native_rollouts", {})
    coverage = [entry["coverage"] for entry in rollouts.get("files", [])]
    events_path = root / "run/events.jsonl"
    events = (
        [json.loads(line) for line in events_path.read_bytes().splitlines()]
        if events_path.exists()
        else []
    )
    command_marker = any(
        event.get("type") == "item.completed"
        and isinstance(item := event.get("item"), dict)
        and item.get("type") == "command_execution"
        and item.get("status") == "completed"
        and item.get("exit_code") == 0
        and MARKER in (item.get("aggregated_output") or "")
        for event in events
    )
    checks = {
        "successful_command_marker": command_marker,
        "native_completed": report.get("status") == "completed",
        "rollout_retained": rollouts.get("status") == "retained",
        "summary_parameter_sent": bool(report.get("request_observations"))
        and all(row.get("reasoning_summary") == "auto" for row in report["request_observations"]),
        "cleanup": all(
            report.get(key) is True
            for key in (
                "cleanup_complete",
                "runtime_home_removed",
                "observed_absent",
                "broker_clean",
            )
        ),
    }
    readable = any(row["readable_summary_observed"] for row in coverage)
    if args.auth_file is None:
        checks["authored_summary_positive_control"] = readable
    summary = {
        "schema_version": 1,
        "kind": "skilleval-rollout-qualification",
        "mode": report.get("inference_mode"),
        "checks": checks,
        "readable_summary_observed": readable,
        "real_summary_availability": "observed"
        if readable and args.auth_file
        else "not-established",
        "native_receipt": "run/native-run.json",
        "native_receipt_sha256": file_digest(root / "run/native-run.json"),
        "passed": all(checks.values()),
    }
    (root / "qualification.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
