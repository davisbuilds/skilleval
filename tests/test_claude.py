"""Synthetic stream fixtures; no native agent process or credential access."""

import json
from typing import Any

import pytest

from skilleval.errors import SkillEvalError
from skilleval.harnesses.claude import claude_version_identity, parse_claude_jsonl

MODEL = "claude-opus-5-5"


def stream(*records: object) -> str:
    return "\n".join(json.dumps(record) for record in records)


def init(**overrides: object) -> dict[str, Any]:
    return {
        "type": "system",
        "subtype": "init",
        "session_id": "session-1",
        "model": MODEL,
        **overrides,
    }


def terminal(**overrides: object) -> dict[str, Any]:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "session_id": "session-1",
        "result": '{"verdict":"pass"}',
        "total_cost_usd": 0.123,
        "usage": {"input_tokens": 1, "output_tokens": 2},
        "modelUsage": {
            MODEL: {
                "inputTokens": 10,
                "outputTokens": 7,
                "cacheReadInputTokens": 20,
                "cacheCreationInputTokens": 3,
                "costUSD": 0.123,
            }
        },
        **overrides,
    }


def test_terminal_model_usage_includes_cache_reads_and_writes_without_double_counting() -> None:
    result = parse_claude_jsonl(stream(init(), terminal()))
    assert result["execution_status"] == "completed"
    assert result["final_text"] == '{"verdict":"pass"}'
    assert result["model"] == MODEL
    expected = {
        "input_tokens": 33,
        "uncached_input_tokens": 13,
        "cached_input_tokens": 20,
        "cache_write_input_tokens": 3,
        "output_tokens": 7,
        "total_tokens": 40,
        "reasoning_output_tokens": None,
    }
    assert {key: result["canonical_usage"][key] for key in expected} == expected
    assert result["canonical_usage"]["status"] == "complete"
    assert result["canonical_usage"]["provider_reconciliation"]["status"] == "unavailable"
    assert result["usage"] == {"input_tokens": 1, "output_tokens": 2}
    assert result["model_usage"] == terminal()["modelUsage"]
    assert result["client_estimate_usd"] == 0.123


def test_cumulative_assistant_usage_is_diagnostic_and_not_added_to_terminal() -> None:
    assistant = {
        "type": "assistant",
        "session_id": "session-1",
        "message": {
            "id": "message-1",
            "model": MODEL,
            "content": [{"type": "text", "text": "answer"}],
            "usage": {"input_tokens": 100, "output_tokens": 100},
        },
    }
    result = parse_claude_jsonl(stream(init(), assistant, assistant, terminal()))
    assert result["execution_status"] == "completed"
    assert result["canonical_usage"]["total_tokens"] == 40
    assert len(result["events"]) == 4


@pytest.mark.parametrize("bad", [[], None, 1, "event", {}, {"type": []}])
def test_nonobject_or_untyped_records_cannot_hide_behind_terminal(bad: object) -> None:
    result = parse_claude_jsonl(stream(init(), bad, terminal()))
    assert result["execution_status"] == "unknown"
    assert result["canonical_usage"] is None
    assert result["parse_errors"][0]["line"] == 2


def test_malformed_json_retains_original_valid_events_and_unknown_events() -> None:
    result = parse_claude_jsonl(
        stream(init(), {"type": "future", "payload": [1]}) + "\n{broken\n" + stream(terminal())
    )
    assert result["events"][1] == {"type": "future", "payload": [1]}
    assert result["execution_status"] == "unknown"
    assert result["parse_errors"][0]["line"] == 3


@pytest.mark.parametrize(
    "records",
    [
        [],
        [init()],
        [terminal()],
        [init(), terminal(), terminal()],
        [
            init(),
            terminal(),
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "late", "content": "late"}]
                },
            },
        ],
    ],
)
def test_absent_multiple_or_late_terminal_evidence_is_unknown(records: list[object]) -> None:
    result = parse_claude_jsonl(stream(*records))
    assert result["execution_status"] == "unknown"
    assert result["canonical_usage"] is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"subtype": "error_max_turns", "is_error": True, "errors": ["turn cap"]},
        {"is_error": True},
        {"terminal_reason": "hook_stopped"},
    ],
)
def test_native_error_result_cannot_be_success(overrides: dict[str, Any]) -> None:
    result = parse_claude_jsonl(stream(init(), terminal(**overrides)))
    assert result["execution_status"] == "failed"
    assert result["canonical_usage"] is None
    assert result["usage"] == {"input_tokens": 1, "output_tokens": 2}


@pytest.mark.parametrize(
    "field",
    [
        "inputTokens",
        "outputTokens",
        "cacheReadInputTokens",
        "cacheCreationInputTokens",
        "thinkingTokens",
    ],
)
@pytest.mark.parametrize("value", [True, -1, 0.1, "1", None, 2**63])
def test_usage_counts_require_nonnegative_integers(field: str, value: object) -> None:
    native = dict(terminal()["modelUsage"][MODEL])
    native[field] = value
    result = parse_claude_jsonl(stream(init(), terminal(modelUsage={MODEL: native})))
    assert result["canonical_usage"] is None
    assert result["usage_errors"]


def test_missing_cache_detail_is_unknown_while_explicit_zero_is_preserved() -> None:
    native = {
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheReadInputTokens": 0,
        "cacheCreationInputTokens": 0,
    }
    complete = parse_claude_jsonl(stream(init(), terminal(modelUsage={MODEL: native})))
    assert complete["canonical_usage"]["total_tokens"] == 0
    del native["cacheCreationInputTokens"]
    incomplete = parse_claude_jsonl(stream(init(), terminal(modelUsage={MODEL: native})))
    assert incomplete["canonical_usage"] is None
    assert incomplete["normalized_usage"]["cache_write_input_tokens"] is None


@pytest.mark.parametrize("observed", ["opus", "claude-opus-5-5-latest", "claude-sonnet-5-5"])
def test_observed_model_identity_is_exact_not_an_alias_guess(observed: str) -> None:
    result = parse_claude_jsonl(stream(init(model=observed), terminal()))
    assert result["execution_status"] == "unknown"
    assert result["canonical_usage"] is None
    assert observed in result["observed_models"]


def test_mcp_tool_calls_pair_results_and_retain_failures_separately() -> None:
    call = {
        "type": "assistant",
        "message": {
            "model": MODEL,
            "content": [
                {
                    "type": "tool_use",
                    "id": "tool-1",
                    "name": "mcp__executor__exec",
                    "input": {"command": "false"},
                }
            ],
        },
    }
    output = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-1",
                    "content": "failed",
                    "is_error": True,
                }
            ]
        },
        "tool_use_result": {"structuredContent": {"exit_code": 1}},
    }
    result = parse_claude_jsonl(stream(init(), call, output, terminal()))
    assert result["execution_status"] == "completed"
    items = [
        event["item"]
        for event in result["canonical_events"]
        if event["type"] == "item.completed" and event["item"]["type"] == "mcp_tool_call"
    ]
    assert len(items) == 1
    assert items[0]["id"] == "tool-1"
    assert items[0]["status"] == "failed"
    assert items[0]["arguments"] == {"command": "false"}
    assert items[0]["result"] == "failed"
    assert items[0]["native_tool_use_result"] == output["tool_use_result"]


def test_unmatched_tool_result_and_unsettled_call_prevent_completion() -> None:
    for content in (
        {"type": "tool_result", "tool_use_id": "absent", "content": "output"},
        {"type": "tool_use", "id": "unsettled", "name": "mcp__executor__exec", "input": {}},
    ):
        kind = "user" if content["type"] == "tool_result" else "assistant"
        result = parse_claude_jsonl(
            stream(
                init(),
                {"type": kind, "message": {"content": [content], "model": MODEL}},
                terminal(),
            )
        )
        assert result["execution_status"] == "unknown"
        assert result["canonical_usage"] is None


@pytest.mark.parametrize("observed", ["2.1.321", "2.1.322", "2.2.0"])
def test_stable_claude_upgrades_preserve_exact_identity(observed: str) -> None:
    identity = claude_version_identity(f"{observed} (Claude Code)\n")
    assert identity["observed_version"] == observed
    assert identity["baseline_version"] == "2.1.321"
    assert identity["baseline_match"] == (observed == "2.1.321")


@pytest.mark.parametrize(
    "output",
    [
        "2.1.320 (Claude Code)",
        "3.1.321 (Claude Code)",
        "1.2.321 (Claude Code)",
        "2.1.321-rc.1 (Claude Code)",
        "2.1.321+build (Claude Code)",
        "02.1.321 (Claude Code)",
        "2.01.321 (Claude Code)",
        "2.1.0321 (Claude Code)",
        "2.1.321",
        "claude 2.1.321",
        "2.1.321 (Claude Code)\nwarning",
        "",
    ],
)
def test_unknown_older_or_unstable_claude_versions_require_review(output: str) -> None:
    with pytest.raises(SkillEvalError):
        claude_version_identity(output)


@pytest.mark.parametrize("value", [[], {}, 1, True, "", None])
def test_invalid_session_identity_is_unknown_without_throwing(value: object) -> None:
    result = parse_claude_jsonl(stream(init(session_id=value), terminal(session_id=value)))
    assert result["execution_status"] == "unknown"
    assert result["canonical_usage"] is None


def test_multiple_models_and_missing_model_usage_do_not_guess_at_total_usage() -> None:
    for usage in (None, {}, {MODEL: {}, "claude-haiku-4-5": {"inputTokens": 12}}):
        result = parse_claude_jsonl(stream(init(), terminal(modelUsage=usage)))
        assert result["canonical_usage"] is None
        assert result["normalized_usage"]["status"] == "unknown"
        assert result["model_usage"] == usage


def test_reasoning_diagnostics_do_not_turn_a_placeholder_zero_into_known_reasoning() -> None:
    usage = dict(terminal()["modelUsage"][MODEL], thinkingTokens=0)
    result = parse_claude_jsonl(stream(init(), terminal(modelUsage={MODEL: usage})))
    assert result["canonical_usage"]["reasoning_output_tokens"] is None
    assert result["model_usage"][MODEL]["thinkingTokens"] == 0


def test_overflow_and_reasoning_exceeding_output_are_invalid_usage() -> None:
    for updates in ({"inputTokens": 2**63 - 1}, {"thinkingTokens": 8}):
        usage = dict(terminal()["modelUsage"][MODEL], **updates)
        result = parse_claude_jsonl(stream(init(), terminal(modelUsage={MODEL: usage})))
        assert result["canonical_usage"] is None
        assert result["usage_errors"]


def test_assistant_error_cannot_be_overwritten_by_success_result() -> None:
    result = parse_claude_jsonl(
        stream(
            init(),
            {
                "type": "assistant",
                "error": "authentication_failed",
                "message": {"model": MODEL, "content": []},
            },
            terminal(),
        )
    )
    assert result["execution_status"] == "failed"
    assert result["errors"]
    assert result["canonical_usage"] is None


def test_nonfinite_json_constants_are_malformed_evidence() -> None:
    result = parse_claude_jsonl(
        stream(init()) + '\n{"type":"future","value":NaN}\n' + stream(terminal())
    )
    assert result["execution_status"] == "unknown"
    assert result["parse_errors"][0]["line"] == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"subtype": None},
        {"subtype": []},
        {"is_error": 0},
        {"result": []},
        {"terminal_reason": {}},
        {"result_index": 1},
    ],
)
def test_malformed_terminal_fields_remain_unknown(overrides: dict[str, Any]) -> None:
    result = parse_claude_jsonl(stream(init(), terminal(**overrides)))
    assert result["execution_status"] == "unknown"
    assert result["canonical_usage"] is None
    assert result["parse_errors"]


def test_tool_result_blocks_require_typed_content_and_duplicate_results_are_invalid() -> None:
    call = {
        "type": "assistant",
        "message": {
            "model": MODEL,
            "content": [
                {"type": "tool_use", "id": "tool-1", "name": "mcp__executor__exec", "input": {}}
            ],
        },
    }
    for outputs in (
        [
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tool-1", "content": ["bad"]}
                    ]
                },
            }
        ],
        [
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "ok"}]
                },
            }
        ]
        * 2,
    ):
        result = parse_claude_jsonl(stream(init(), call, *outputs, terminal()))
        assert result["execution_status"] == "unknown"
        assert result["canonical_usage"] is None
        assert result["parse_errors"]


def test_end_conversation_reflection_is_a_native_terminal_tool_with_a_result() -> None:
    call = {
        "type": "assistant",
        "message": {
            "model": MODEL,
            "content": [
                {
                    "type": "tool_use",
                    "id": "end-1",
                    "name": "EndConversation",
                    "input": {},
                }
            ],
        },
    }
    response = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "end-1",
                    "content": "Synthetic reflection response",
                }
            ]
        },
        "tool_use_result": {"ended": False, "message": "Synthetic reflection response"},
    }
    result = parse_claude_jsonl(stream(init(), call, response, terminal()))
    assert result["execution_status"] == "completed"
    assert result["parse_errors"] == []
    tool = next(
        event["item"]
        for event in result["canonical_events"]
        if event["type"] == "item.completed" and event["item"].get("name") == "EndConversation"
    )
    assert tool["type"] == "native_terminal_tool"
    assert tool["status"] == "completed"
    assert tool["native_tool_use_result"]["ended"] is False
    assert "server" not in tool
    assert "tool" not in tool
    assert result["item_outcomes"] == [
        {
            "id": "end-1",
            "type": "native_terminal_tool",
            "status": "completed",
            "exit_code": None,
        }
    ]


@pytest.mark.parametrize("has_terminal", [False, True])
def test_pending_end_conversation_never_fabricates_a_tool_result_or_completion(
    has_terminal: bool,
) -> None:
    call = {
        "type": "assistant",
        "message": {
            "model": MODEL,
            "content": [
                {
                    "type": "tool_use",
                    "id": "end-1",
                    "name": "EndConversation",
                    "input": {},
                }
            ],
        },
    }
    tail = [terminal()] if has_terminal else []
    result = parse_claude_jsonl(stream(init(), call, *tail))
    assert result["execution_status"] == "unknown"
    assert result["canonical_usage"] is None
    started = next(
        event["item"] for event in result["canonical_events"] if event["type"] == "item.started"
    )
    assert started["type"] == "native_terminal_tool"
    assert started["status"] == "in_progress"
    assert result["item_outcomes"] == []
    assert not any("built-in tool observed" in error["error"] for error in result["parse_errors"])


def test_end_conversation_exception_does_not_admit_other_native_builtins() -> None:
    call = {
        "type": "assistant",
        "message": {
            "model": MODEL,
            "content": [
                {
                    "type": "tool_use",
                    "id": "host-1",
                    "name": "Bash",
                    "input": {"command": "true"},
                }
            ],
        },
    }
    response = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "host-1",
                    "content": "",
                    "is_error": False,
                }
            ]
        },
    }
    result = parse_claude_jsonl(stream(init(), call, response, terminal()))
    assert result["execution_status"] == "unknown"
    assert result["canonical_usage"] is None
    assert any("built-in tool observed" in error["error"] for error in result["parse_errors"])
