"""Interpret captured ``codex exec --json`` events without invoking Codex.

Event reference: https://learn.chatgpt.com/docs/non-interactive-mode
Unknown fields and events remain in ``events``. These are reported observations,
not task-quality judgments or proof that a skill was consulted.
"""

from __future__ import annotations

import json
import re
from typing import Any

from skilleval.errors import SkillEvalError

CODEX_BASELINE_VERSION = "0.164.0"
CODEX_VERSION_POLICY = "stable-same-major-at-or-after-baseline"


def codex_version_identity(raw: str) -> dict[str, str | bool]:
    """Admit stable upgrades for qualification; native controls must still pass.

    The exact observed identity is retained. Acceptance does not establish event
    protocol compatibility or equivalence to earlier qualification evidence.
    """
    component = r"(?:0|[1-9][0-9]*)"
    match = re.fullmatch(rf"codex-cli ({component}\.{component}\.{component})", raw.strip())
    if len(raw) > 128 or match is None:
        raise SkillEvalError("unknown or unstable Codex version requires adapter review")
    observed = tuple(int(part) for part in match[1].split("."))
    baseline = tuple(int(part) for part in CODEX_BASELINE_VERSION.split("."))
    if observed[0] != baseline[0]:
        raise SkillEvalError("Codex major version requires adapter review")
    if observed < baseline:
        raise SkillEvalError("Codex version predates the adapter baseline")
    return {
        "observed_version": match[1],
        "baseline_version": CODEX_BASELINE_VERSION,
        "version_policy": CODEX_VERSION_POLICY,
        "baseline_match": observed == baseline,
    }


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
