"""Interpret captured ``codex exec --json`` events without invoking Codex.

Event reference: https://learn.chatgpt.com/docs/non-interactive-mode
Unknown fields and events remain in ``events``. These are reported observations,
not task-quality judgments or proof that a skill was consulted.
"""

from __future__ import annotations

import json
from typing import Any


def parse_codex_jsonl(text: str) -> dict[str, Any]:
    """Parse telemetry without turning absent usage or failed items into success."""
    result: dict[str, Any] = {
        "schema_version": 1,
        "harness": "codex-exec-json",
        "execution_status": "unknown",
        "task_quality": "unassessed",
        "skill_consultation": "unknown",
        "usage": None,
        "usage_observations": [],
        "events": [],
        "item_outcomes": [],
        "errors": [],
        "parse_errors": [],
    }
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise ValueError("expected an event object with a type")
        except (json.JSONDecodeError, ValueError) as error:
            result["parse_errors"].append({"line": line_number, "error": str(error)})
            continue
        result["events"].append(event)
        event_type = event["type"]
        if event_type == "thread.started":
            result["thread_id"] = event.get("thread_id")
        elif event_type == "turn.started":
            result["execution_status"] = "unknown"
            result["usage"] = None
        elif event_type in {"turn.completed", "turn.failed"}:
            result["execution_status"] = "completed" if event_type == "turn.completed" else "failed"
            usage = event.get("usage")
            result["usage"] = usage if isinstance(usage, dict) else None
            result["usage_observations"].append(result["usage"])
            if "error" in event:
                result["errors"].append(event["error"])
        elif event_type == "error":
            result["errors"].append(event)
            result["execution_status"] = "failed"
        elif event_type == "item.completed" and isinstance(event.get("item"), dict):
            item = event["item"]
            result["item_outcomes"].append(
                {
                    "id": item.get("id"),
                    "type": item.get("type"),
                    "status": item.get("status"),
                    "exit_code": item.get("exit_code"),
                }
            )
    # A malformed stream has incomplete evidence even if a terminal record survived.
    if result["parse_errors"]:
        result["execution_status"] = "unknown"
    return result
