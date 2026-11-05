"""Controller-side protocol for one isolated Codex app-server turn.

Transport and raw evidence retention belong to the caller. This module imports
only the standard library and never starts processes or reads configuration.
"""

import copy
import re
from typing import Any

ADAPTER_NAME = "codex-app-server-jsonrpc"
ADAPTER_VERSION = 1
CODEX_PROTOCOL_VERSION = "0.164.0"
_SANDBOX = {"type": "externalSandbox", "networkAccess": "restricted"}
_COUNTERS = (
    "inputTokens",
    "cachedInputTokens",
    "cacheWriteInputTokens",
    "outputTokens",
    "reasoningOutputTokens",
    "totalTokens",
)
_PROGRESS = {
    "item/agentMessage/delta",
    "item/commandExecution/outputDelta",
    "item/commandExecution/terminalInteraction",
    "item/fileChange/outputDelta",
    "item/fileChange/patchUpdated",
    "item/reasoning/summaryTextDelta",
    "item/reasoning/textDelta",
    "turn/diff/updated",
    "turn/plan/updated",
}


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _snake(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


def _fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {_snake(key): _fields(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_fields(item) for item in value]
    return value


class Session:
    """Drive initialize, an ephemeral thread, and one externally sandboxed turn."""

    def __init__(self, prompt: str, work: str) -> None:
        self.prompt = prompt
        self.work = work
        self.events: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.terminal = False
        self.permissions_confirmed = False
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self._pending: int | None = None
        self._started = False
        self._turn_requested = False
        self._turn_reply = False
        self._turn_started = False
        self._successful = False
        self._final = ""
        self._usage: dict[str, int] | None = None
        self._items: dict[str, dict[str, Any]] = {}
        self._started_items: dict[str, dict[str, Any]] = {}
        self._announced_thread: str | None = None

    @property
    def settled(self) -> bool:
        """The terminal notification and matching turn/start reply both arrived."""
        return self.terminal and self._turn_reply

    @property
    def complete(self) -> bool:
        return self.settled and self._successful and self.permissions_confirmed and not self.errors

    @property
    def final_text(self) -> str:
        return self._final if self.complete else ""

    def start(self) -> list[dict[str, Any]]:
        if self._started:
            self._record_error("session already started")
            raise ValueError("session already started")
        self._started = True
        self._pending = 1
        return [
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "workspace-client", "version": "1"},
                    "capabilities": {"experimentalApi": True},
                },
            }
        ]

    def receive(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            return self._receive(_object(record, "RPC record"))
        except ValueError as error:
            self._record_error(str(error))
            raise

    def _record_error(self, message: str) -> None:
        self.errors.append(message)
        self.events.append({"type": "error", "message": message})

    def _receive(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        if not self._started:
            raise ValueError("session has not started")
        if "method" in record:
            method = _text(record["method"], "RPC method")
            if "id" in record:
                raise ValueError(f"unexpected app-server request: {method}")
            return self._notification(method, record.get("params"))
        if "id" not in record:
            raise ValueError("RPC response has no id")
        if type(record["id"]) is not int or record["id"] != self._pending:
            raise ValueError("unexpected or duplicate RPC response id")
        if "error" in record:
            error = _object(record["error"], "RPC error")
            message = _text(error.get("message"), "RPC error message")
            raise ValueError(f"app-server RPC error: {message}")
        result = _object(record.get("result"), "RPC result")
        request_id = self._pending
        self._pending = None
        if request_id == 1:
            for key in ("userAgent", "codexHome", "platformFamily", "platformOs"):
                _text(result.get(key), f"initialize.{key}")
            self._pending = 2
            return [
                {"method": "initialized"},
                {
                    "id": 2,
                    "method": "thread/start",
                    "params": {
                        "cwd": self.work,
                        "sandbox": "read-only",
                        "approvalPolicy": "never",
                        "ephemeral": True,
                    },
                },
            ]
        if request_id == 2:
            thread = _object(result.get("thread"), "thread/start.thread")
            self.thread_id = _text(thread.get("id"), "thread id")
            if self._announced_thread not in (None, self.thread_id):
                raise ValueError("thread/start response scope mismatch")
            if thread.get("ephemeral") is not True:
                raise ValueError("thread/start did not confirm an ephemeral thread")
            if result.get("approvalPolicy") != "never":
                raise ValueError("thread/start approval policy mismatch")
            if result.get("sandbox") != {"type": "readOnly", "networkAccess": False}:
                raise ValueError("thread/start sandbox mismatch")
            self.events.append({"type": "thread.started", "thread_id": self.thread_id})
            self._pending = 3
            self._turn_requested = True
            return [
                {
                    "id": 3,
                    "method": "turn/start",
                    "params": {
                        "threadId": self.thread_id,
                        "input": [{"type": "text", "text": self.prompt}],
                        "approvalPolicy": "never",
                        "sandboxPolicy": dict(_SANDBOX),
                    },
                }
            ]
        turn = self._turn(result.get("turn"))
        self._bind_turn(turn["id"])
        self._turn_reply = True
        return []

    def _bind_turn(self, turn_id: Any) -> None:
        if not self._turn_requested:
            raise ValueError("turn event arrived before turn/start request")
        turn_id = _text(turn_id, "turn id")
        if self.turn_id is not None and turn_id != self.turn_id:
            raise ValueError("turn scope mismatch")
        self.turn_id = turn_id

    def _scope(self, params: dict[str, Any], *, turn: bool = True) -> None:
        if self.thread_id is None or params.get("threadId") != self.thread_id:
            raise ValueError("thread scope mismatch")
        if turn:
            self._bind_turn(params.get("turnId"))

    def _turn(self, value: Any) -> dict[str, Any]:
        turn = _object(value, "turn")
        _text(turn.get("id"), "turn id")
        status = _text(turn.get("status"), "turn status")
        if status not in {"inProgress", "completed", "failed", "interrupted"}:
            raise ValueError("unknown turn status")
        if not isinstance(turn.get("items"), list):
            raise ValueError("turn.items must be an array")
        if turn.get("error") is not None:
            error = _object(turn["error"], "turn error")
            _text(error.get("message"), "turn error message")
        return turn

    def _notification(self, method: str, value: Any) -> list[dict[str, Any]]:
        known = {
            "thread/started",
            "thread/settings/updated",
            "turn/started",
            "turn/completed",
            "item/started",
            "item/completed",
            "thread/tokenUsage/updated",
            "error",
        }
        if method not in known and method not in _PROGRESS:
            return []
        params = _object(value, f"{method}.params")
        if method == "thread/started":
            thread = _object(params.get("thread"), "thread/started.thread")
            thread_id = _text(thread.get("id"), "thread id")
            if self.thread_id not in (None, thread_id):
                raise ValueError("thread notification scope mismatch")
            if self._announced_thread not in (None, thread_id):
                raise ValueError("thread notification scope mismatch")
            if self._pending not in (2, 3, None):
                raise ValueError("unexpected thread/started notification")
            self._announced_thread = thread_id
            return []
        self._scope(
            params,
            turn=method
            not in {
                "thread/settings/updated",
                "turn/started",
                "turn/completed",
            },
        )
        if self.terminal:
            raise ValueError("relevant notification arrived after terminal turn")
        if method == "thread/settings/updated":
            settings = _object(params.get("threadSettings"), "thread settings")
            if (
                settings.get("approvalPolicy") != "never"
                or settings.get("sandboxPolicy") != _SANDBOX
            ):
                raise ValueError("effective permission settings mismatch")
            self.permissions_confirmed = True
        elif method in {"turn/started", "turn/completed"}:
            turn = self._turn(params.get("turn"))
            self._bind_turn(turn["id"])
            if method == "turn/started":
                if self._turn_started or turn["status"] != "inProgress":
                    raise ValueError("duplicate or malformed turn/started notification")
                self._turn_started = True
                self.events.append({"type": "turn.started", "turn_id": self.turn_id})
            else:
                self._finish(turn)
        elif method in {"item/started", "item/completed"}:
            self._item(params.get("item"), completed=method == "item/completed")
        elif method == "thread/tokenUsage/updated":
            usage = _object(params.get("tokenUsage"), "token usage")
            self._usage = self._counters(usage.get("total"))
            self._counters(usage.get("last"))
        elif method == "error":
            error = _object(params.get("error"), "error notification")
            if type(params.get("willRetry")) is not bool:
                raise ValueError("error.willRetry must be boolean")
            self._record_error(_text(error.get("message"), "error message"))
        return []

    def _counters(self, value: Any) -> dict[str, int]:
        counters = _object(value, "usage counters")
        result = {}
        for key in _COUNTERS:
            counter = counters.get(key)
            if type(counter) is not int or counter < 0:
                raise ValueError(f"usage.{key} must be a nonnegative integer")
            result[_snake(key)] = counter
        return result

    def _item(self, value: Any, *, completed: bool) -> dict[str, Any]:
        item = _object(value, "thread item")
        item_id = _text(item.get("id"), "item id")
        kind = _text(item.get("type"), "item type")
        if kind == "agentMessage":
            if not isinstance(item.get("text"), str):
                raise ValueError("agent message text must be a string")
            if item.get("phase") is not None and not isinstance(item["phase"], str):
                raise ValueError("agent message phase must be a string or null")
        elif kind in {"commandExecution", "fileChange"}:
            status = _text(item.get("status"), "item status")
            allowed = {"completed", "failed", "declined"}
            if not completed:
                allowed.add("inProgress")
            if status not in allowed:
                raise ValueError("invalid executable item status")
            if kind == "commandExecution":
                _text(item.get("command"), "command")
                _text(item.get("cwd"), "command cwd")
                if item.get("exitCode") is not None and type(item["exitCode"]) is not int:
                    raise ValueError("command exitCode must be an integer or null")
                if item.get("aggregatedOutput") is not None and not isinstance(
                    item["aggregatedOutput"], str
                ):
                    raise ValueError("command output must be a string or null")
            else:
                if not isinstance(item.get("changes"), list):
                    raise ValueError("file changes must be an array")
                for change in item["changes"]:
                    change = _object(change, "file change")
                    _text(change.get("path"), "changed path")
                    patch = _object(change.get("kind"), "patch kind")
                    if _text(patch.get("type"), "patch type") not in {"add", "delete", "update"}:
                        raise ValueError("unknown patch kind")
                    if not isinstance(change.get("diff"), str):
                        raise ValueError("file change diff must be a string")
        canonical = _object(_fields(item), "canonical item")
        canonical["type"] = _snake(kind)
        if isinstance(item.get("status"), str):
            canonical["status"] = _snake(item["status"])
        previous = self._items.get(item_id)
        if previous is not None:
            if not completed or previous != canonical:
                raise ValueError("conflicting completed item")
            return previous
        previous_start = self._started_items.get(item_id)
        if previous_start is not None and previous_start["type"] != canonical["type"]:
            raise ValueError("item type changed during execution")
        target = self._items if completed else self._started_items
        if target.get(item_id) == canonical:
            return canonical
        target[item_id] = copy.deepcopy(canonical)
        self.events.append(
            {
                "type": "item.completed" if completed else "item.started",
                "thread_id": self.thread_id,
                "turn_id": self.turn_id,
                "item": copy.deepcopy(canonical),
            }
        )
        return canonical

    def _finish(self, turn: dict[str, Any]) -> None:
        if not self._turn_started:
            raise ValueError("terminal turn arrived without turn/started")
        if turn["status"] == "inProgress":
            raise ValueError("terminal turn has inProgress status")
        terminal_items = [
            self._item(item, completed=_object(item, "thread item").get("status") != "inProgress")
            for item in turn["items"]
        ]
        self.terminal = True
        self._successful = turn["status"] == "completed" and turn.get("error") is None
        if self._successful and not self.permissions_confirmed:
            self._record_error("terminal turn has no confirmed permission settings")
        if self._successful and not self.errors:
            candidates = terminal_items or list(self._items.values())
            final = self._final_message(candidates) or self._final_message(
                list(self._items.values())
            )
            self._final = final.get("text", "") if final is not None else ""
            event: dict[str, Any] = {"type": "turn.completed", "usage": self._usage}
        else:
            error = turn.get("error") or {"message": f"turn {turn['status']}"}
            if self.errors and turn.get("error") is None:
                error = {"message": self.errors[-1]}
            if turn.get("error") is not None or not self._successful:
                self.errors.append(error["message"])
            event = {"type": "turn.failed", "error": copy.deepcopy(error), "usage": self._usage}
        event.update({"thread_id": self.thread_id, "turn_id": self.turn_id})
        self.events.append(event)

    @staticmethod
    def _final_message(items: list[dict[str, Any]]) -> dict[str, Any] | None:
        messages = [item for item in items if item["type"] == "agent_message"]
        final = [item for item in messages if item.get("phase") == "final_answer"]
        fallback = [item for item in messages if item.get("phase") is None]
        candidates = final or fallback
        return candidates[-1] if candidates else None
