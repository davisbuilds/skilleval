import json
import tempfile
import time
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
from test_gateway import BODY, EVENT, LIMITS, Stream, request, write_auth

from skilleval import gateway
from skilleval._vendor import openbench_gateway as vendor
from skilleval.errors import SkillEvalError
from skilleval.gateway import broker_scope, codex_provider_config
from skilleval.web_search import effective_catalog


@pytest.fixture
def short_root() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="seweb-", dir="/tmp") as name:
        yield Path(name)


CONFIG = vendor.GatewayConfig(gateway.MODEL, gateway.EFFORT, 4, 4194304, 1)


def search_body() -> dict:
    return {
        **BODY,
        "tools": [{"type": "web_search", "external_web_access": True}],
        "input": [
            *BODY["input"],
            {
                "type": "web_search_call",
                "id": "ws_search",
                "status": "completed",
                "action": {
                    "type": "search",
                    "query": "official reference",
                    "queries": ["official reference"],
                },
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Reference",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "start_index": 0,
                                "end_index": 9,
                                "url": "https://example.com/",
                                "title": "Reference",
                            }
                        ],
                        "logprobs": [],
                    }
                ],
            },
        ],
    }


def test_native_web_config_requires_opt_in() -> None:
    assert (
        tomllib.loads(codex_provider_config(8123, Path("/run/models.json")))["web_search"]
        == "disabled"
    )
    assert (
        tomllib.loads(codex_provider_config(8123, Path("/run/models.json"), web_search=True))[
            "web_search"
        ]
        == "live"
    )


def test_opt_in_broker_accepts_native_web_replay_without_rewriting(short_root: Path) -> None:
    observed: list[bytes] = []
    headers: list[dict] = []

    def transport(raw, forwarded_headers, timeout):
        observed.append(raw)
        headers.append(forwarded_headers)
        return Stream(EVENT)

    auth = gateway.load_chatgpt_auth(write_auth(short_root, time.time() + 600))
    body = search_body()
    with broker_scope(
        auth, short_root / "web", limits=LIMITS, test_transport=transport, web_search=True
    ) as broker:
        assert request(broker.socket_path, body)[0] == 200
    assert observed == [json.dumps(body).encode()]
    assert "x-openai-internal-codex-responses-lite" not in headers[0]
    with broker_scope(
        auth, short_root / "default", limits=LIMITS, test_transport=transport
    ) as broker:
        assert request(broker.socket_path, body)[0] == 400
        assert request(broker.socket_path)[0] == 200
    assert len(observed) == 2
    assert headers[-1]["x-openai-internal-codex-responses-lite"] == "true"


def test_catalog_adaptation_changes_only_selected_lite_flag() -> None:
    original = {
        "models": [
            {
                "slug": "gpt-6.1-sol",
                "use_responses_lite": True,
                "tool_mode": "code_mode_only",
                "other": "same",
            },
            {"slug": "other", "use_responses_lite": True},
        ]
    }
    raw = json.dumps(original, indent=2).encode()
    assert effective_catalog(raw) == raw
    adapted = json.loads(effective_catalog(raw, web_search=True))
    assert adapted["models"][0]["use_responses_lite"] is False
    adapted["models"][0]["use_responses_lite"] = True
    assert adapted == original


def test_concurrent_brokers_do_not_share_web_authority(short_root: Path) -> None:
    auth = gateway.load_chatgpt_auth(write_auth(short_root, time.time() + 600))
    received: list[bytes] = []

    def transport(raw, headers, timeout):
        received.append(raw)
        return Stream(EVENT)

    with (
        broker_scope(auth, short_root / "off", limits=LIMITS, test_transport=transport) as disabled,
        broker_scope(
            auth,
            short_root / "on",
            limits=LIMITS,
            test_transport=transport,
            web_search=True,
        ) as enabled,
    ):
        body = search_body()
        assert request(disabled.socket_path, body)[0] == 400
        assert request(enabled.socket_path, body)[0] == 200
        assert request(disabled.socket_path, body)[0] == 400
        assert request(disabled.socket_path)[0] == 200
    assert received == [json.dumps(body).encode(), json.dumps(BODY).encode()]


def test_native_text_and_image_search_declaration_is_accepted() -> None:
    body = search_body()
    body["tools"][0]["search_content_types"] = ["text", "image"]
    raw = json.dumps(body).encode()
    assert gateway.validate_body(raw, CONFIG, web_search=True) == body
    with pytest.raises(ValueError):
        gateway.validate_body(raw, CONFIG)


@pytest.mark.parametrize(
    "action",
    [
        {"type": "search", "query": "reference"},
        {"type": "search", "queries": ["reference", "other reference"]},
        {"type": "open_page", "url": "https://example.com/"},
        {"type": "find_in_page", "url": "https://example.com/", "pattern": "reference"},
    ],
)
def test_native_hosted_action_history_requires_opt_in(action: dict) -> None:
    body = search_body()
    body["input"][1]["action"] = action
    raw = json.dumps(body).encode()
    assert gateway.validate_body(raw, CONFIG, web_search=True) == body
    with pytest.raises(ValueError, match="inference policy"):
        gateway.validate_body(raw, CONFIG)


@pytest.mark.parametrize(
    "change",
    [
        {"tools": [{"type": "file_search", "vector_store_ids": ["vs_remote"]}]},
        {"tools": [{"type": "mcp", "server_url": "https://example.com"}]},
        {"tools": [{"type": "code_interpreter", "container": {"type": "auto"}}]},
        {"tools": [{"type": "web_search_preview"}]},
        {"tools": [{"type": "web_search", "external_web_access": False}]},
        {
            "tools": [
                {
                    "type": "web_search",
                    "external_web_access": True,
                    "server_url": "https://example.com/",
                }
            ]
        },
        {
            "tools": [
                {
                    "type": "web_search",
                    "external_web_access": True,
                    "search_content_types": ["image"],
                }
            ]
        },
        {"tools": [{"type": "web_search", "external_web_access": True}] * 2},
        {
            "tools": [
                {
                    "type": "namespace",
                    "name": "nested",
                    "tools": [{"type": "web_search", "external_web_access": True}],
                }
            ]
        },
        {"input": [{"type": "item_reference", "id": "remote"}]},
        {"include": ["web_search_call.action.sources"]},
        {"reasoning": {"effort": "low"}},
        {"model": "another-model"},
        {
            "tools": [
                {
                    "type": "function",
                    "name": "unsafe",
                    "parameters": {"$ref": "https://example.com/schema"},
                }
            ]
        },
    ],
)
def test_web_flag_preserves_other_capability_refusals(change: dict) -> None:
    raw = json.dumps({**search_body(), **change}).encode()
    with pytest.raises(ValueError, match="inference policy"):
        gateway.validate_body(raw, CONFIG, web_search=True)


@pytest.mark.parametrize(
    "item",
    [
        {"type": "web_search_call", "action": {"type": "unknown", "url": "https://example.com"}},
        {
            "type": "web_search_call",
            "action": {"type": "search", "query": "q", "secret": "unrecognized"},
        },
        {"type": "web_search_call", "action": {"type": "open_page"}},
        {"type": "web_search_call", "action": {"type": "search", "queries": "query"}},
        {
            "type": "web_search_call",
            "action": {"type": "search", "query": "q"},
            "status": "unknown",
        },
        {
            "type": "web_search_call",
            "action": {"type": "search", "query": "q"},
            "internal_chat_message_metadata_passthrough": {},
        },
    ],
)
def test_unknown_web_history_is_not_an_unrestricted_replay_channel(item: dict) -> None:
    body = search_body()
    body["input"][1] = item
    with pytest.raises(ValueError, match="inference policy"):
        gateway.validate_body(json.dumps(body).encode(), CONFIG, web_search=True)


@pytest.mark.parametrize(
    "change",
    [
        {"type": "file_citation"},
        {"start_index": True},
        {"end_index": 1000},
        {"url": None},
        {"unexpected": "field"},
        {"title": ["not-text"]},
    ],
)
def test_native_citations_are_strictly_typed(change: dict) -> None:
    body = search_body()
    annotation = body["input"][2]["content"][0]["annotations"][0]
    annotation.update(change)
    with pytest.raises(ValueError, match="inference policy"):
        gateway.validate_body(json.dumps(body).encode(), CONFIG, web_search=True)


def test_duplicate_json_and_nonempty_logprobs_remain_forbidden() -> None:
    body = search_body()
    body["input"][2]["content"][0]["logprobs"] = [{"token": "Reference"}]
    with pytest.raises(ValueError):
        gateway.validate_body(json.dumps(body).encode(), CONFIG, web_search=True)
    raw = (
        json.dumps(search_body())
        .encode()
        .replace(
            b'"external_web_access": true',
            b'"external_web_access": false, "external_web_access": true',
        )
    )
    with pytest.raises(ValueError):
        gateway.validate_body(raw, CONFIG, web_search=True)


@pytest.mark.parametrize("value", [None, "true", 1, [], {}])
def test_web_flag_is_an_explicit_boolean(value, short_root: Path) -> None:
    with pytest.raises(SkillEvalError, match="capability"):
        codex_provider_config(8123, Path("/run/models.json"), web_search=value)
    with pytest.raises(SkillEvalError, match="capability"):
        effective_catalog(b"{}", web_search=value)
    with pytest.raises(ValueError, match="inference policy"):
        gateway.validate_body(json.dumps(BODY).encode(), CONFIG, web_search=value)
    auth = gateway.load_chatgpt_auth(write_auth(short_root, time.time() + 600))
    with (
        pytest.raises(SkillEvalError, match="capability"),
        broker_scope(auth, short_root / "invalid", limits=LIMITS, web_search=value),
    ):
        pass
    assert not (short_root / "invalid").exists()


@pytest.mark.parametrize(
    "models",
    [
        [],
        [{"slug": "other", "use_responses_lite": True, "tool_mode": "code_mode_only"}],
        [{"slug": "gpt-6.1-sol", "use_responses_lite": False, "tool_mode": "code_mode_only"}],
        [{"slug": "gpt-6.1-sol", "use_responses_lite": True, "tool_mode": "code_mode"}],
        [{"slug": "gpt-6.1-sol", "use_responses_lite": True, "tool_mode": "code_mode_only"}] * 2,
    ],
)
def test_catalog_adaptation_requires_unambiguous_original_sol_mode(models: list) -> None:
    with pytest.raises(SkillEvalError, match="expected Sol catalog"):
        effective_catalog(json.dumps({"models": models}).encode(), web_search=True)
