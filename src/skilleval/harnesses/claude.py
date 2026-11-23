"""Interpret native Claude Code ``--output-format stream-json`` without execution.

Protocol reference: https://code.claude.com/docs/en/agent-sdk/typescript
``result.modelUsage`` is a cumulative, per-model pipeline snapshot; ``usage`` is
main-loop-only. Neither is independent provider reconciliation. Unknown native
fields/events remain in ``events``; projections do not replace that evidence.
"""

from __future__ import annotations

import json
import re
from typing import Any, TypeGuard

from skilleval.errors import SkillEvalError

CLAUDE_BASELINE_VERSION = "2.1.321"
CLAUDE_VERSION_POLICY = "stable-same-major-at-or-after-baseline"
CLAUDE_MODEL = "claude-opus-5-5"
CLAUDE_SURFACE = "claude-code-stream-json"
_COUNTS = {
    "inputTokens": None,
    "outputTokens": "output_tokens",
    "cacheReadInputTokens": "cached_input_tokens",
    "cacheCreationInputTokens": "cache_write_input_tokens",
}


def claude_version_identity(raw: str) -> dict[str, str | bool]:
    """Admit stable upgrades for qualification while preserving exact identity."""
    component = r"(?:0|[1-9][0-9]*)"
    match = re.fullmatch(rf"({component}\.{component}\.{component}) \(Claude Code\)", raw.strip())
    if len(raw) > 128 or match is None:
        raise SkillEvalError("unknown or unstable Claude Code version requires adapter review")
    observed = tuple(int(part) for part in match[1].split("."))
    baseline = tuple(int(part) for part in CLAUDE_BASELINE_VERSION.split("."))
    if observed[0] != baseline[0] or observed < baseline:
        raise SkillEvalError("Claude Code version is outside the admitted adapter policy")
    return {
        "observed_version": match[1],
        "baseline_version": CLAUDE_BASELINE_VERSION,
        "version_policy": CLAUDE_VERSION_POLICY,
        "baseline_match": observed == baseline,
    }


def _count(value: Any) -> bool:
    return type(value) is int and 0 <= value < 2**63


def _reject_constant(value: str) -> Any:
    raise ValueError("non-finite JSON constant")


def _nonempty(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip())


def _native_api_error(event: dict[str, Any], source: str) -> dict[str, Any]:
    """Project only reviewed diagnostic enums/status; raw text stays in events."""
    codes = {
        "authentication_failed",
        "oauth_org_not_allowed",
        "account_on_hold",
        "billing_error",
        "rate_limit",
        "overloaded",
        "invalid_request",
        "model_not_found",
        "server_error",
        "max_output_tokens",
        "cloud_credential_error",
        "unknown",
    }
    raw_code = event.get("error")
    status = event.get("api_error_status")
    details = {
        "message": "native Claude reported an API error",
        "source": source,
        "code": raw_code if isinstance(raw_code, str) and raw_code in codes else "unknown",
        "api_error_status": status if type(status) is int and 400 <= status <= 599 else None,
        "api_error": "usage_limit_reached"
        if event.get("api_error") == "usage_limit_reached"
        else None,
        "api_error_origin": "first_party"
        if event.get("api_error_origin") == "first_party"
        else None,
    }
    return {"type": "error", "message": details["message"], "error": details}


def _usage(model_usage: Any, model: str | None) -> tuple[dict[str, Any], list[str]]:
    normalized: dict[str, Any] = {
        "schema_version": 1,
        "status": "unknown",
        "model": model,
        **dict.fromkeys(
            (
                "input_tokens",
                "uncached_input_tokens",
                "cached_input_tokens",
                "cache_write_input_tokens",
                "output_tokens",
                "total_tokens",
                "reasoning_output_tokens",
            )
        ),
        "surface": CLAUDE_SURFACE,
        "basis": "single terminal result.modelUsage cumulative native pipeline snapshot",
        "provider_reconciliation": {
            "status": "unavailable",
            "source": None,
            "reasons": ["native subscription route has no independent provider ledger"],
        },
    }
    errors: list[str] = []
    if not isinstance(model_usage, dict) or set(model_usage) != {CLAUDE_MODEL}:
        return normalized, ["one exact pinned modelUsage identity required"]
    raw = model_usage[CLAUDE_MODEL]
    if not isinstance(raw, dict):
        return normalized, ["modelUsage entry must be an object"]
    if "canonicalModel" in raw and raw["canonicalModel"] != CLAUDE_MODEL:
        errors.append("modelUsage canonicalModel differs from pinned model")
    # WHY: Do not substitute result.usage, assistant usage, or guessed cache zeros.
    for native, target in _COUNTS.items():
        if native not in raw:
            errors.append(f"modelUsage.{native} missing")
        elif not _count(raw[native]):
            errors.append(f"modelUsage.{native} must be a nonnegative signed-64-bit integer")
        elif target is not None:
            normalized[target] = raw[native]
    if "thinkingTokens" in raw and not _count(raw["thinkingTokens"]):
        errors.append("modelUsage.thinkingTokens must be a nonnegative signed-64-bit integer")
    if errors:
        return normalized, errors
    if raw.get("thinkingTokens", 0) > raw["outputTokens"]:
        return normalized, ["modelUsage thinking tokens exceed output tokens"]
    # WHY: Anthropic's inputTokens excludes both cache read and cache creation counts.
    input_tokens = sum(
        raw[key] for key in ("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
    )
    total_tokens = input_tokens + raw["outputTokens"]
    if not _count(total_tokens):
        return normalized, ["modelUsage aggregate exceeds signed-64-bit token range"]
    normalized.update(
        input_tokens=input_tokens,
        uncached_input_tokens=raw["inputTokens"] + raw["cacheCreationInputTokens"],
        total_tokens=total_tokens,
    )
    # WHY: Native thinking fields can contain placeholder zeros; keep reasoning unknown.
    return normalized, errors


def parse_claude_jsonl(text: str) -> dict[str, Any]:
    """Validate one fresh stream, retain raw evidence, and project completed tools.

    Completion requires one init and one final result with consistent session and
    exact model identity. Structural uncertainty is ``unknown``; a native failed
    result is ``failed``. Tool errors are item outcomes, independent of turn status.
    Usage comes from one terminal snapshot and is never summed with messages.
    The caller still verifies process exit, tool allowlists, version and isolation.
    """
    result: dict[str, Any] = {
        "schema_version": 1,
        "harness": CLAUDE_SURFACE,
        "execution_status": "unknown",
        "task_quality": "unassessed",
        "skill_consultation": "unknown",
        "events": [],
        "canonical_events": [],
        "parse_errors": [],
        "errors": [],
        "item_outcomes": [],
        "usage": None,
        "model_usage": None,
        "client_estimate_usd": None,
        "usage_observations": [],
        "canonical_usage": None,
        "usage_errors": [],
        "final_text": "",
        "model": None,
        "observed_models": [],
        "thread_id": None,
    }
    inits: list[dict[str, Any]] = []
    terminals: list[dict[str, Any]] = []
    calls: dict[str, dict[str, Any]] = {}
    settled: set[str] = set()
    sessions: set[str] = set()
    terminal_seen = False

    def error(line: int, message: str) -> None:
        result["parse_errors"].append({"line": line, "error": message})

    def api_failure(event: dict[str, Any], source: str) -> None:
        canonical = _native_api_error(event, source)
        result["errors"].append(canonical["error"])
        result["canonical_events"].append(canonical)

    def observe_model(value: Any) -> None:
        if _nonempty(value) and value not in result["observed_models"]:
            result["observed_models"].append(value)

    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line, parse_constant=_reject_constant)
            if not isinstance(event, dict) or not _nonempty(event.get("type")):
                raise ValueError
        except (ValueError, RecursionError):
            error(line_number, "expected a JSON event object with a nonempty type")
            continue
        result["events"].append(event)
        kind = event["type"]
        if terminal_seen:
            error(line_number, "native event follows terminal result")
        session = event.get("session_id")
        if "session_id" in event:
            if not _nonempty(session):
                error(line_number, "invalid native session identity")
            else:
                sessions.add(session)
        if kind == "system" and event.get("subtype") == "init":
            inits.append(event)
            if len(result["events"]) != 1:
                error(line_number, "init must be the first native event")
            observe_model(event.get("model"))
            result["thread_id"] = event.get("session_id")
            result["canonical_events"].extend(
                [
                    {"type": "thread.started", "thread_id": event.get("session_id")},
                    {"type": "turn.started"},
                ]
            )
        elif kind in {"assistant", "user"}:
            if event.get("parent_tool_use_id") is not None or event.get("agent_id") is not None:
                error(line_number, "subagent messages are outside the admitted single-agent stream")
            if event.get("error"):
                if kind == "assistant":
                    api_failure(event, "assistant")
                else:
                    result["errors"].append(event)
            if event.get("aborted"):
                result["errors"].append(event)
            message = event.get("message")
            if not isinstance(message, dict):
                error(line_number, "native message must be an object")
                continue
            synthetic_api_error = False
            if kind == "assistant":
                # WHY: Native API failures can be assistant-shaped diagnostics rather
                # than inference responses. Only the exact observed marker and
                # explicit native flag bypass inference-model identity checking.
                synthetic_api_error = (
                    event.get("is_api_error_message") is True
                    and message.get("model") == "<synthetic>"
                    and _nonempty(event.get("error"))
                )
                if not synthetic_api_error:
                    observe_model(message.get("model"))
                if isinstance(message.get("usage"), dict):
                    result["usage_observations"].append(
                        {
                            "source": "assistant.message.usage",
                            "usage": message["usage"],
                        }
                    )
            content = message.get("content")
            if isinstance(content, str) and kind == "user":
                continue
            if not isinstance(content, list):
                error(line_number, "native message content must be an array")
                continue
            for block in content:
                if not isinstance(block, dict) or not _nonempty(block.get("type")):
                    error(line_number, "native content block must be a typed object")
                    continue
                block_type = block["type"]
                if synthetic_api_error and block_type != "text":
                    error(line_number, "native API error diagnostic contains unexpected content")
                    continue
                if kind == "assistant" and block_type == "text":
                    if not isinstance(block.get("text"), str):
                        error(line_number, "assistant text must be a string")
                    elif not synthetic_api_error:
                        result["canonical_events"].append(
                            {
                                "type": "item.completed",
                                "item": {
                                    "type": "agent_message",
                                    "text": block["text"],
                                },
                            }
                        )
                elif kind == "assistant" and block_type == "tool_use":
                    tool_id, name, arguments = (
                        block.get("id"),
                        block.get("name"),
                        block.get("input"),
                    )
                    if (
                        not _nonempty(tool_id)
                        or not _nonempty(name)
                        or not isinstance(arguments, dict)
                        or tool_id in calls
                    ):
                        error(line_number, "malformed or duplicate native tool call")
                        continue
                    item = {
                        "type": "mcp_tool_call",
                        "id": tool_id,
                        "name": name,
                        "arguments": arguments,
                        "status": "in_progress",
                    }
                    # WHY: EndConversation is a native session-control tool, never
                    # an executor. It may return a reflection tool_result before
                    # a later call ends the process; missing results stay pending.
                    # https://code.claude.com/docs/en/tools-reference#endconversation-tool-behavior
                    if name == "EndConversation":
                        item["type"] = "native_terminal_tool"
                    elif isinstance(name, str) and name.startswith("mcp__"):
                        parts = name.split("__", 2)
                        if len(parts) == 3:
                            item.update(server=parts[1], tool=parts[2])
                    else:
                        error(line_number, "built-in tool observed outside admitted MCP route")
                    calls[tool_id] = item
                    result["canonical_events"].append({"type": "item.started", "item": dict(item)})
                elif kind == "user" and block_type == "tool_result":
                    tool_id = block.get("tool_use_id")
                    if not _nonempty(tool_id) or tool_id not in calls or tool_id in settled:
                        error(line_number, "unmatched or duplicate native tool result")
                        continue
                    if "is_error" in block and type(block["is_error"]) is not bool:
                        error(line_number, "native tool result is_error must be boolean")
                        continue
                    output = block.get("content")
                    if not isinstance(output, (str, list)):
                        error(line_number, "native tool result content must be text or blocks")
                        continue
                    if isinstance(output, list) and any(
                        not isinstance(part, dict)
                        or not _nonempty(part.get("type"))
                        or (part.get("type") == "text" and not isinstance(part.get("text"), str))
                        for part in output
                    ):
                        error(line_number, "native tool result blocks must be typed content")
                        continue
                    settled.add(tool_id)
                    item = {
                        **calls[tool_id],
                        "result": output,
                        "status": "failed" if block.get("is_error") else "completed",
                    }
                    if "tool_use_result" in event:
                        item["native_tool_use_result"] = event["tool_use_result"]
                    result["canonical_events"].append({"type": "item.completed", "item": item})
                    result["item_outcomes"].append(
                        {
                            "id": tool_id,
                            "type": item["type"],
                            "status": item["status"],
                            "exit_code": None,
                        }
                    )
        elif kind == "result":
            terminal_seen = True
            terminals.append(event)
            subtype = event.get("subtype")
            if subtype not in (
                "success",
                "error_max_turns",
                "error_during_execution",
                "error_max_budget_usd",
                "error_max_structured_output_retries",
            ):
                error(line_number, "unknown or malformed terminal subtype")
            if type(event.get("is_error")) is not bool:
                error(line_number, "terminal is_error must be boolean")
            if subtype == "success" and not isinstance(event.get("result"), str):
                error(line_number, "successful terminal result must be text")
            if event.get("terminal_reason") is not None and not _nonempty(event["terminal_reason"]):
                error(line_number, "terminal_reason must be text or null")
            if "result_index" in event and (
                type(event["result_index"]) is not int or event["result_index"] != 0
            ):
                error(line_number, "fresh single-result stream must have result_index zero")
            result["usage"] = event.get("usage")
            result["model_usage"] = event.get("modelUsage")
            result["client_estimate_usd"] = event.get("total_cost_usd")
            result["usage_observations"].append(
                {
                    "source": "result",
                    "usage": event.get("usage"),
                    "modelUsage": event.get("modelUsage"),
                }
            )
            if isinstance(event.get("modelUsage"), dict):
                for model in event["modelUsage"]:
                    observe_model(model)
            if isinstance(event.get("result"), str):
                result["final_text"] = event["result"]
            if event.get("is_error") or event.get("errors"):
                status = event.get("api_error_status")
                if event.get("terminal_reason") == "api_error" or (
                    type(status) is int and 400 <= status <= 599
                ):
                    api_failure(event, "result")
                else:
                    result["errors"].append(event)
        elif kind == "error":
            result["errors"].append(event)

    structural = len(inits) == len(terminals) == 1
    if not structural:
        error(0, "exactly one init and one terminal result required")
    init_session = inits[0].get("session_id") if inits else None
    if len(sessions) != 1 or not _nonempty(init_session) or init_session not in sessions:
        error(0, "one consistent native session identity required")
    terminal_session = terminals[0].get("session_id") if terminals else None
    if terminals and (not _nonempty(terminal_session) or terminal_session not in sessions):
        error(0, "terminal native session identity missing")
    if inits and inits[0].get("model") == CLAUDE_MODEL:
        result["model"] = CLAUDE_MODEL
    if result["model"] is None or any(model != CLAUDE_MODEL for model in result["observed_models"]):
        error(0, "native observed model identity differs from exact pinned model")
    if set(calls) != settled:
        error(0, "native tool calls lack settled results")
    if structural:
        terminal = terminals[0]
        success = (
            terminal.get("subtype") == "success"
            and terminal.get("is_error") is False
            and terminal.get("terminal_reason") in (None, "completed")
            and isinstance(terminal.get("result"), str)
            and not result["errors"]
        )
        if not result["parse_errors"]:
            result["execution_status"] = "completed" if success else "failed"
        result["canonical_events"].append(
            {
                "type": "turn.completed"
                if result["execution_status"] == "completed"
                else "turn.failed",
            }
        )
    normalized, usage_errors = _usage(result["model_usage"], result["model"])
    result["normalized_usage"] = normalized
    result["usage_errors"] = usage_errors
    if result["execution_status"] == "completed" and not usage_errors:
        normalized["status"] = "complete"
        result["canonical_usage"] = dict(normalized)
    if result["parse_errors"]:
        result["canonical_events"].append({"type": "error", "message": "incomplete native stream"})
    return result
