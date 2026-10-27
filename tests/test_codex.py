import json

import pytest

from skilleval.errors import SkillEvalError
from skilleval.harnesses.codex import codex_version_identity, parse_codex_jsonl


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


@pytest.mark.parametrize("observed", ["0.164.0", "0.164.1", "0.165.0", "0.200.0"])
def test_stable_codex_upgrades_allow_qualification_with_exact_identity(observed: str) -> None:
    identity = codex_version_identity(f"codex-cli {observed}\n")
    assert identity == {
        "observed_version": observed,
        "baseline_version": "0.164.0",
        "version_policy": "stable-same-major-at-or-after-baseline",
        "baseline_match": observed == "0.164.0",
    }


@pytest.mark.parametrize(
    "output",
    [
        "codex-cli 0.163.9",
        "codex-cli 0.1.999",
        "codex-cli 1.0.0",
        "codex-cli 2.164.0",
        "codex-cli 0.165.0-rc.1",
        "codex-cli 0.164.1+build.1",
        "codex-cli unknown",
        "",
        "0.165.0",
        "codex 0.165.0",
        "codex-cli v0.165.0",
        "codex-cli 0.165",
        "codex-cli 0.165.0.1",
        "codex-cli 00.165.0",
        "codex-cli 0.0165.0",
        "codex-cli 0.165.00",
        "warning: changed interface\ncodex-cli 0.165.0",
        "codex-cli 0.165.0\nother output",
    ],
)
def test_older_unknown_prerelease_and_new_major_require_review(output: str) -> None:
    with pytest.raises(SkillEvalError):
        codex_version_identity(output)
