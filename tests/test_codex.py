import json

from skilleval.harnesses.codex import parse_codex_jsonl


def events(*items: dict[str, object]) -> str:
    return "\n".join(json.dumps(item) for item in items)


def test_missing_usage_is_unknown_and_item_failure_is_separate() -> None:
    result = parse_codex_jsonl(
        events(
            {"type": "thread.started", "thread_id": "thread-1"},
            {
                "type": "item.completed",
                "item": {
                    "id": "c",
                    "type": "command_execution",
                    "status": "failed",
                    "exit_code": 1,
                },
            },
            {"type": "turn.completed"},
        )
    )
    assert result["execution_status"] == "completed"
    assert result["usage"] is None
    assert result["item_outcomes"][0]["status"] == "failed"
    assert result["task_quality"] == "unassessed"


def test_failed_turn_preserves_usage_and_unknown_events() -> None:
    result = parse_codex_jsonl(
        events(
            {"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 0}},
            {"type": "vendor.future", "value": 3},
            {"type": "turn.failed", "error": {"message": "interrupted"}},
        )
    )
    assert result["execution_status"] == "failed"
    assert result["usage"] is None  # second turn's usage is unknown
    assert result["usage_observations"] == [{"input_tokens": 12, "output_tokens": 0}, None]
    assert result["events"][1]["value"] == 3
    assert result["errors"] == [{"message": "interrupted"}]


def test_malformed_and_unterminated_stream_cannot_be_completed() -> None:
    result = parse_codex_jsonl("{broken\n" + events({"type": "turn.started"}))
    assert result["execution_status"] == "unknown"
    assert result["parse_errors"][0]["line"] == 1
    assert result["usage"] is None


def test_zero_usage_is_preserved_as_reported() -> None:
    result = parse_codex_jsonl(
        events(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                },
            }
        )
    )
    assert result["usage"]["input_tokens"] == 0
