"""Explicit native hosted-search compatibility; no alternate network transport."""

from __future__ import annotations

import json
from typing import Any, cast

from skilleval._vendor import openbench_gateway as _vendor
from skilleval.errors import SkillEvalError

MODEL = "gpt-6.1-sol"


def _keys(value: object, allowed: set[str], required: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - allowed or not required <= set(value):
        raise ValueError("unsupported native web shape")
    return value


def _text(value: object, *, maximum: int = 16384) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError("invalid native web text")


def _web_tool(value: dict[str, Any]) -> None:
    _keys(
        value,
        {"type", "external_web_access", "search_content_types"},
        {"type", "external_web_access"},
    )
    if value["type"] != "web_search" or value["external_web_access"] is not True:
        raise ValueError("invalid native web tool")
    if "search_content_types" in value and value["search_content_types"] != ["text", "image"]:
        raise ValueError("unsupported native web content types")


def _web_history(value: dict[str, Any]) -> None:
    _keys(value, {"type", "id", "status", "action", "encrypted_content"}, {"type", "action"})
    for key in ("id", "encrypted_content"):
        if key in value:
            _text(value[key], maximum=8 * 1024 * 1024 if key == "encrypted_content" else 256)
    if "status" in value and value["status"] not in {
        "in_progress",
        "searching",
        "completed",
        "failed",
    }:
        raise ValueError("invalid native web status")
    action = value["action"]
    if not isinstance(action, dict):
        raise ValueError("invalid native web action")
    kind = action.get("type")
    if kind == "search":
        _keys(action, {"type", "query", "queries"}, {"type"})
        if "query" not in action and "queries" not in action:
            raise ValueError("missing native web query")
        if "query" in action:
            _text(action["query"])
        if "queries" in action:
            queries = action["queries"]
            if not isinstance(queries, list) or not 1 <= len(queries) <= 32:
                raise ValueError("invalid native web queries")
            for query in queries:
                _text(query)
    elif kind in {"open_page", "find_in_page"}:
        required = {"type", "url", "pattern"} if kind == "find_in_page" else {"type", "url"}
        _keys(action, required, required)
        _text(action["url"])
        if kind == "find_in_page":
            _text(action["pattern"])
    else:
        raise ValueError("unsupported native web action")


def _annotations(value: object, text: object) -> None:
    if not isinstance(value, list) or len(value) > 512 or not isinstance(text, str):
        raise ValueError("invalid native citations")
    for annotation in value:
        item = _keys(
            annotation,
            {"type", "start_index", "end_index", "title", "url"},
            {"type", "start_index", "end_index", "title", "url"},
        )
        if item["type"] != "url_citation":
            raise ValueError("unsupported native citation")
        _text(item["title"])
        _text(item["url"])
        start, end = item["start_index"], item["end_index"]
        # The wire offsets can count encoded characters; bound them by UTF-8
        # bytes rather than assuming Python codepoint offsets are equivalent.
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start <= end <= len(text.encode("utf-8"))
        ):
            raise ValueError("invalid native citation offsets")


def validate_web_search_body(raw: bytes, config: object) -> dict[str, Any]:
    """Admit only the native hosted web additions, retaining all base policy checks.

    A validation-only copy removes independently checked web metadata before
    applying the original local-tool validator. The broker forwards ``raw``
    unchanged. No schema, destination, remote reference or tool is expanded by
    the copy operation. The default broker never calls this validator.
    """
    try:
        body = cast(dict[str, Any], _vendor._json(raw))
        shadow = json.loads(json.dumps(body))
        tools = shadow.get("tools", [])
        if not isinstance(tools, list) or len(tools) > 128:
            raise ValueError("invalid tools")
        web_tools = [
            tool for tool in tools if isinstance(tool, dict) and tool.get("type") == "web_search"
        ]
        if len(web_tools) > 1:
            raise ValueError("duplicate native web tool")
        for tool in web_tools:
            _web_tool(tool)
        shadow["tools"] = [tool for tool in tools if tool not in web_tools]
        items = shadow.get("input")
        if not isinstance(items, list):
            raise ValueError("invalid input")
        local_items = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("invalid input item")
            if item.get("type") == "web_search_call":
                _web_history(item)
                continue
            if item.get("type", "message") == "message" and isinstance(item.get("content"), list):
                for part in item["content"]:
                    if (
                        isinstance(part, dict)
                        and part.get("type") == "output_text"
                        and "annotations" in part
                    ):
                        if item.get("role") != "assistant":
                            raise ValueError("native citations require assistant output")
                        _annotations(part["annotations"], part.get("text"))
                        part["annotations"] = []
            local_items.append(item)
        shadow["input"] = local_items
        _vendor.validate_body(json.dumps(shadow).encode(), config)
        return body
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, AttributeError):
        raise ValueError("request violates inference policy") from None


def effective_catalog(raw: bytes, *, web_search: bool = False) -> bytes:
    """Derive an opt-in native profile without changing its source catalog."""
    if type(web_search) is not bool:
        raise SkillEvalError("invalid native web search capability")
    if not web_search:
        return raw
    try:
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError
        catalog = _vendor._json(raw)
        if not isinstance(catalog, dict) or not isinstance(catalog.get("models"), list):
            raise ValueError
        entries = [
            entry
            for entry in catalog["models"]
            if isinstance(entry, dict) and entry.get("slug") == MODEL
        ]
        if (
            len(entries) != 1
            or entries[0].get("use_responses_lite") is not True
            or entries[0].get("tool_mode") != "code_mode_only"
        ):
            raise ValueError
        entries[0]["use_responses_lite"] = False
        return (json.dumps(catalog, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise SkillEvalError("native web search requires the expected Sol catalog entry") from None
