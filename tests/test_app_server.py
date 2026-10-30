"""Protocol fixtures exercise the controller without processes or inference.

Shapes follow Codex 0.164.0's generated v2 schemas; extra response fields are
irrelevant to this single-turn controller. These tests establish protocol
interpretation, not effective sandbox enforcement or model behavior.
"""

import copy
from typing import Any

import pytest

from skilleval.app_server import Session

INITIALIZED = {
    "id": 1,
    "result": {
        "userAgent": "codex/0.164.0",
        "codexHome": "/tmp/native/codex-home",
        "platformFamily": "unix",
        "platformOs": "linux",
    },
}
THREAD = {
    "id": 2,
    "result": {
        "thread": {"id": "thread-1", "ephemeral": True, "turns": []},
        "model": "configured-model",
        "modelProvider": "local-gateway",
        "approvalPolicy": "never",
        "sandbox": {"type": "readOnly", "networkAccess": False},
    },
}
PERMISSIONS = {
    "method": "thread/settings/updated",
    "params": {
        "threadId": "thread-1",
        "threadSettings": {
            "approvalPolicy": "never",
            "sandboxPolicy": {"type": "externalSandbox", "networkAccess": "restricted"},
        },
    },
}
COMMAND = {
    "id": "command-1",
    "type": "commandExecution",
    "command": "python -m pytest",
    "cwd": "/workspace/task",
    "status": "completed",
    "aggregatedOutput": "2 passed\n",
    "exitCode": 0,
    "durationMs": 15,
    "commandActions": [],
    "processId": None,
}
MESSAGE = {
    "id": "message-1",
    "type": "agentMessage",
    "text": "The tests pass.",
    "phase": "final_answer",
}
COUNTERS = {
    "inputTokens": 81,
    "cachedInputTokens": 20,
    "cacheWriteInputTokens": 3,
    "outputTokens": 15,
    "reasoningOutputTokens": 4,
    "totalTokens": 96,
}


def turn(status: str = "inProgress", **fields: Any) -> dict[str, Any]:
    return {"id": "turn-1", "status": status, "items": [], "error": None, **fields}


def note(method: str, **fields: Any) -> dict[str, Any]:
    return {"method": method, "params": {"threadId": "thread-1", **fields}}


def ready(*, reply: bool = True, permissions: bool = True) -> Session:
    session = Session("Run tests", "/workspace/task")
    session.start()
    session.receive(copy.deepcopy(INITIALIZED))
    session.receive(copy.deepcopy(THREAD))
    if permissions:
        session.receive(copy.deepcopy(PERMISSIONS))
    session.receive(note("turn/started", turn=turn()))
    if reply:
        session.receive({"id": 3, "result": {"turn": turn()}})
    return session


def item(session: Session, data: dict[str, Any], *, completed: bool = True) -> None:
    session.receive(
        note("item/completed" if completed else "item/started", turnId="turn-1", item=data)
    )


def finish(session: Session, **fields: Any) -> None:
    session.receive(note("turn/completed", turn=turn("completed", **fields)))


def test_initialization_requests_only_the_isolated_protocol_capabilities() -> None:
    session = Session("Run tests", "/workspace/task")
    assert session.start() == [
        {
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "workspace-client", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
        }
    ]


def test_handshake_inherits_frozen_config_and_sets_only_native_permissions() -> None:
    session = Session("Run tests", "/workspace/task")
    session.start()
    assert session.receive(INITIALIZED) == [
        {"method": "initialized"},
        {
            "id": 2,
            "method": "thread/start",
            "params": {
                "cwd": "/workspace/task",
                "sandbox": "read-only",
                "approvalPolicy": "never",
                "ephemeral": True,
            },
        },
    ]
    assert session.receive(THREAD) == [
        {
            "id": 3,
            "method": "turn/start",
            "params": {
                "threadId": "thread-1",
                "input": [{"type": "text", "text": "Run tests"}],
                "approvalPolicy": "never",
                "sandboxPolicy": {"type": "externalSandbox", "networkAccess": "restricted"},
            },
        },
    ]
    assert session.thread_id == "thread-1"
    assert not session.permissions_confirmed


def test_typed_stream_preserves_items_and_latest_total_usage_without_duplicates() -> None:
    session = ready()
    item(session, {**COMMAND, "status": "inProgress", "exitCode": None}, completed=False)
    item(session, COMMAND)
    change = {
        "id": "patch-1",
        "type": "fileChange",
        "status": "completed",
        "changes": [
            {
                "path": "/workspace/task/a.py",
                "kind": {"type": "update", "move_path": None},
                "diff": "+fixed\n",
            }
        ],
    }
    item(session, change)
    item(session, MESSAGE)
    item(session, MESSAGE)
    for input_tokens in (50, 81):
        session.receive(
            note(
                "thread/tokenUsage/updated",
                turnId="turn-1",
                tokenUsage={
                    "total": {**COUNTERS, "inputTokens": input_tokens},
                    "last": dict.fromkeys(COUNTERS, 0),
                    "modelContextWindow": 32000,
                },
            )
        )
    finish(session, items=[COMMAND, change, MESSAGE])
    assert session.complete and session.settled and session.terminal
    assert session.permissions_confirmed
    assert session.final_text == "The tests pass."
    completed = [event["item"] for event in session.events if event["type"] == "item.completed"]
    assert [entry["id"] for entry in completed] == ["command-1", "patch-1", "message-1"]
    assert completed[0]["exit_code"] == 0
    assert completed[0]["aggregated_output"] == "2 passed\n"
    assert completed[0]["duration_ms"] == 15
    assert completed[1]["changes"][0]["diff"] == "+fixed\n"
    assert session.events[-1]["usage"] == {
        "input_tokens": 81,
        "cached_input_tokens": 20,
        "cache_write_input_tokens": 3,
        "output_tokens": 15,
        "reasoning_output_tokens": 4,
        "total_tokens": 96,
    }
    assert not session.errors


def test_turn_start_reply_may_follow_all_notifications() -> None:
    session = ready(reply=False)
    item(session, MESSAGE)
    finish(session, items=[MESSAGE])
    assert session.terminal and not session.settled and not session.complete
    assert session.final_text == ""
    session.receive({"id": 3, "result": {"turn": turn()}})
    assert session.complete and session.settled
    assert session.turn_id == "turn-1"


def test_terminal_summary_message_takes_precedence_over_streamed_messages() -> None:
    session = ready()
    item(session, MESSAGE)
    item(session, {**MESSAGE, "id": "later", "text": "streamed later"})
    finish(session, items=[MESSAGE])
    assert session.final_text == "The tests pass."


def test_missing_usage_remains_unknown_and_reported_zero_is_retained() -> None:
    session = ready()
    finish(session)
    assert session.complete
    assert session.events[-1]["usage"] is None
    session = ready()
    zero = dict.fromkeys(COUNTERS, 0)
    session.receive(
        note(
            "thread/tokenUsage/updated",
            turnId="turn-1",
            tokenUsage={
                "total": zero,
                "last": zero,
            },
        )
    )
    finish(session)
    assert session.events[-1]["usage"]["cache_write_input_tokens"] == 0


@pytest.mark.parametrize("status", ["failed", "interrupted"])
def test_failed_turn_has_no_final_even_after_a_final_message(status: str) -> None:
    session = ready()
    item(session, MESSAGE)
    session.receive(
        note(
            "turn/completed",
            turn=turn(status, items=[MESSAGE], error={"message": "provider unavailable"}),
        )
    )
    assert session.settled and session.terminal and not session.complete
    assert session.final_text == ""
    assert session.errors == ["provider unavailable"]
    assert session.events[-1]["type"] == "turn.failed"


def test_failed_command_does_not_alone_fail_a_completed_turn() -> None:
    session = ready()
    item(session, {**COMMAND, "status": "failed", "exitCode": 1})
    finish(session)
    assert session.complete
    assert session.events[-2]["item"]["status"] == "failed"


def test_interrupted_summary_does_not_invent_command_completion() -> None:
    session = ready()
    command = {**COMMAND, "status": "inProgress", "exitCode": None}
    item(session, command, completed=False)
    session.receive(note("turn/completed", turn=turn("interrupted", items=[command])))
    assert session.settled and not session.complete
    assert not [event for event in session.events if event["type"] == "item.completed"]


def test_transient_error_is_retained_conservatively() -> None:
    session = ready()
    session.receive(note("error", turnId="turn-1", error={"message": "retrying"}, willRetry=True))
    finish(session, items=[MESSAGE])
    assert session.settled and not session.complete
    assert session.errors == ["retrying"]
    assert session.final_text == ""
    assert session.events[-1]["type"] == "turn.failed"


@pytest.mark.parametrize(
    "method",
    [
        "item/tool/call",
        "item/permissions/requestApproval",
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/tool/requestUserInput",
        "account/chatgptAuthTokens/refresh",
    ],
)
def test_server_requests_fail_closed(method: str) -> None:
    session = ready()
    with pytest.raises(ValueError, match="unexpected app-server request"):
        session.receive({"id": "server-1", "method": method, "params": {}})
    assert session.errors and not session.complete
    assert session.events[-1]["type"] == "error"


@pytest.mark.parametrize(
    "record",
    [
        note("item/completed", threadId="wrong", turnId="turn-1", item=MESSAGE),
        note("item/completed", turnId="wrong", item=MESSAGE),
        note("thread/tokenUsage/updated", turnId="wrong", tokenUsage={}),
        note("thread/settings/updated", threadId="wrong", threadSettings={}),
        note("turn/completed", turn=turn("completed", id="wrong")),
        {"id": 3, "result": {"turn": turn(id="wrong")}},
    ],
)
def test_scopes_must_match_the_requested_thread_and_turn(record: dict[str, Any]) -> None:
    session = ready(reply=False)
    with pytest.raises(ValueError, match="scope mismatch"):
        session.receive(record)
    assert session.errors and not session.complete


def test_duplicate_terminal_invalidates_success() -> None:
    session = ready()
    finish(session, items=[MESSAGE])
    assert session.complete
    with pytest.raises(ValueError, match="after terminal"):
        finish(session, items=[MESSAGE])
    assert not session.complete and session.final_text == ""
    assert session.events[-1]["type"] == "error"


def test_reply_scope_mismatch_after_terminal_cannot_complete() -> None:
    session = ready(reply=False)
    finish(session, items=[MESSAGE])
    with pytest.raises(ValueError, match="turn scope mismatch"):
        session.receive({"id": 3, "result": {"turn": turn(id="different")}})
    assert not session.settled and not session.complete and session.final_text == ""


@pytest.mark.parametrize("counter", [None, True, -1, "1"])
def test_missing_or_malformed_usage_is_not_replaced_by_zero(counter: Any) -> None:
    session = ready()
    counters = {**COUNTERS, "inputTokens": counter}
    if counter is None:
        del counters["inputTokens"]
    with pytest.raises(ValueError, match=r"usage\.inputTokens"):
        session.receive(
            note(
                "thread/tokenUsage/updated",
                turnId="turn-1",
                tokenUsage={
                    "total": counters,
                    "last": COUNTERS,
                },
            )
        )
    assert session.errors


def test_missing_effective_permission_confirmation_cannot_complete() -> None:
    session = ready(permissions=False)
    finish(session, items=[MESSAGE])
    assert session.terminal and session.settled and not session.complete
    assert session.final_text == ""
    assert "no confirmed permission settings" in session.errors[-1]
    assert session.events[-1]["type"] == "turn.failed"


@pytest.mark.parametrize(
    "settings",
    [
        {
            "approvalPolicy": "onRequest",
            "sandboxPolicy": {
                "type": "externalSandbox",
                "networkAccess": "restricted",
            },
        },
        {"approvalPolicy": "never", "sandboxPolicy": {"type": "dangerFullAccess"}},
        {
            "approvalPolicy": "never",
            "sandboxPolicy": {
                "type": "externalSandbox",
                "networkAccess": "enabled",
            },
        },
    ],
)
def test_effective_permission_drift_is_rejected(settings: dict[str, Any]) -> None:
    session = ready(permissions=False)
    with pytest.raises(ValueError, match="permission settings mismatch"):
        session.receive(note("thread/settings/updated", threadSettings=settings))
    assert not session.permissions_confirmed


def test_conflicting_item_cannot_replace_retained_completion() -> None:
    session = ready()
    item(session, MESSAGE)
    with pytest.raises(ValueError, match="conflicting completed item"):
        finish(session, items=[{**MESSAGE, "text": "different"}])
    assert session.final_text == ""
    assert session.events[-2]["item"]["text"] == "The tests pass."


@pytest.mark.parametrize(
    "record",
    [
        {"id": 3, "result": {"turn": {}}},
        {"id": True, "result": {"turn": turn()}},
        {"id": 99, "result": {}},
        note("item/completed", turnId="turn-1", item={**COMMAND, "status": []}),
        note("item/completed", turnId="turn-1", item={**MESSAGE, "text": 3}),
        note("turn/completed", turn=turn("completed", items={})),
        note("turn/completed", turn=turn(status=[])),
    ],
)
def test_malformed_relevant_records_raise_value_error(record: dict[str, Any]) -> None:
    session = ready(reply=False)
    with pytest.raises(ValueError):
        session.receive(record)
    assert session.errors and not session.complete


def test_unknown_unrelated_notifications_are_ignored() -> None:
    session = ready()
    assert session.receive({"method": "future/notification", "params": None}) == []
    assert session.receive({"method": "account/updated", "params": {"authMode": None}}) == []
    finish(session)
    assert session.complete


@pytest.mark.parametrize(
    "changed",
    [
        {"thread": {"id": "thread-1", "ephemeral": False}},
        {"approvalPolicy": "onRequest"},
        {"sandbox": {"type": "dangerFullAccess"}},
    ],
)
def test_initial_thread_must_echo_the_requested_isolated_settings(changed: dict[str, Any]) -> None:
    session = Session("Run tests", "/workspace/task")
    session.start()
    session.receive(INITIALIZED)
    with pytest.raises(ValueError):
        session.receive({"id": 2, "result": {**THREAD["result"], **changed}})
    assert not session.complete and session.errors


def test_rpc_error_is_retained_and_never_starts_a_turn() -> None:
    session = Session("Run tests", "/workspace/task")
    session.start()
    with pytest.raises(ValueError, match="unsupported client"):
        session.receive({"id": 1, "error": {"code": -32600, "message": "unsupported client"}})
    assert not session.terminal and not session.complete
    assert session.events[-1]["type"] == "error"


def test_nonfinal_commentary_does_not_become_the_final_answer() -> None:
    session = ready()
    item(session, {**MESSAGE, "phase": "commentary"})
    finish(session)
    assert session.complete and session.final_text == ""


def test_input_evidence_is_not_mutated_or_aliased_by_canonicalization() -> None:
    session = ready()
    command = copy.deepcopy(COMMAND)
    item(session, command)
    command["command"] = "modified after receive"
    finish(session, items=[COMMAND])
    assert session.complete
    assert session.events[-2]["item"]["command"] == "python -m pytest"
