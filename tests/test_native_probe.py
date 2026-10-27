"""Pure synthetic-responder controls; these tests never launch VMs or inference."""

import ast
import json
import runpy
from pathlib import Path
from typing import Any

import pytest

from skilleval._vendor import openbench_gateway as vendor


@pytest.fixture
def probe() -> dict[str, Any]:
    return runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "qualify-native.py"))


def test_native_responder_uses_a_declared_namespaced_command_tool(probe: dict[str, Any]) -> None:
    body = {
        "input": [
            {
                "type": "additional_tools",
                "tools": [
                    {
                        "type": "namespace",
                        "name": "functions",
                        "tools": [
                            {
                                "type": "function",
                                "name": "exec_command",
                                "parameters": {},
                            }
                        ],
                    }
                ],
            }
        ],
    }
    item = probe["tool_item"](body)
    assert item["name"] == "exec_command"
    assert item["namespace"] == "functions"
    assert "NATIVE_TOOL_OK" in json.loads(item["arguments"])["cmd"]
    frames = probe["response_stream"](item, "synthetic").split(b"\n\n")
    events = [json.loads(frame.removeprefix(b"data: ")) for frame in frames if frame]
    assert events[-1]["type"] == "response.completed"
    assert events[-1]["response"]["output"] == [item]


def test_responder_refuses_to_invent_tools_and_guest_program_compiles(
    probe: dict[str, Any],
) -> None:
    with pytest.raises(probe["ProbeError"], match="did not declare"):
        probe["tool_item"]({"tools": [{"type": "web_search"}]})
    tree = ast.parse(probe["GUEST_RUNNER"])
    assert isinstance(tree, ast.Module)


def test_native_code_mode_custom_exec_remains_the_local_tool_loop(probe: dict[str, Any]) -> None:
    body = {
        "input": [
            {
                "type": "additional_tools",
                "tools": [
                    {
                        "type": "namespace",
                        "name": "functions",
                        "tools": [
                            {
                                "type": "custom",
                                "name": "exec",
                                "format": {"type": "text"},
                            }
                        ],
                    }
                ],
            }
        ]
    }
    item = probe["tool_item"](body)
    assert item["type"] == "custom_tool_call"
    assert item["namespace"] == "functions"
    assert "await tools.exec_command" in item["input"]
    assert "NATIVE_TOOL_OK" in item["input"]


def test_second_request_requires_the_actual_successful_result(probe: dict[str, Any]) -> None:
    # The earlier assistant call contains the marker as source text. It is not
    # evidence that native execution ran or returned a successful result.
    call = {
        "type": "custom_tool_call",
        "call_id": "call_synthetic",
        "name": "exec",
        "input": probe["TOOL_COMMAND"],
    }
    check = probe["has_successful_tool_result"]
    assert not check({"input": [call]})
    output = {
        "type": "custom_tool_call_output",
        "call_id": "call_synthetic",
        "output": [{"type": "input_text", "text": "NATIVE_TOOL_OK"}],
    }
    assert check({"input": [call, output]})
    assert not check({"input": [call, {**output, "status": "failed"}]})
    assert not check({"input": [call, {**output, "call_id": "another_call"}]})
    assert not check({"input": [call, {**output, "output": "tool execution failed"}]})


def test_machine_observer_requires_working_json_and_identifies_known_positive(
    probe: dict[str, Any],
) -> None:
    names = probe["machine_names"]
    assert names('[{"name":"our-machine"}]') == {"our-machine"}
    assert names("[]") == set()
    for invalid in ("", "{}", '[{"other":"our-machine"}]'):
        with pytest.raises(probe["ProbeError"], match="invalid JSON inventory"):
            names(invalid)


def test_explicit_real_mode_changes_only_declared_limits_and_prompt(probe: dict[str, Any]) -> None:
    synthetic = probe["mode_settings"](False)
    authenticated = probe["mode_settings"](True)
    assert synthetic["mode"] == "synthetic" and synthetic["max_requests"] == 3
    assert synthetic["request_timeout_seconds"] == 30
    assert synthetic["native_timeout_seconds"] == 100
    assert synthetic["lifetime_seconds"] == 240
    assert authenticated["mode"] == "authenticated" and authenticated["max_requests"] == 4
    assert authenticated["request_timeout_seconds"] == 90
    assert authenticated["native_timeout_seconds"] == 240
    assert authenticated["lifetime_seconds"] == 300
    assert "/workspace/native-tool-ran" in authenticated["prompt"]
    assert "native-ok" in authenticated["prompt"] and "NATIVE_TOOL_OK" in authenticated["prompt"]
    assert "auth_file" not in authenticated and "access_token" not in authenticated


def test_native_success_requires_completed_turn_and_successful_command_output(
    probe: dict[str, Any],
) -> None:
    command = {
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "status": "completed",
            "exit_code": 0,
            "aggregated_output": "NATIVE_TOOL_OK\n",
        },
    }
    complete = {"type": "turn.completed"}
    summary = probe["native_control_summary"]
    passed = summary(json.dumps(command) + "\n" + json.dumps(complete))
    assert passed == {
        "execution_status": "completed",
        "successful_command_marker": True,
        "parse_error_count": 0,
        "error_count": 0,
    }
    failed = {**command, "item": {**command["item"], "exit_code": 1}}
    assert not summary(json.dumps(failed) + "\n" + json.dumps(complete))[
        "successful_command_marker"
    ]
    assert summary(json.dumps(command))["execution_status"] != "completed"
    message = {
        "type": "item.completed",
        "item": {"type": "agent_message", "text": "NATIVE_TOOL_OK"},
    }
    assert not summary(json.dumps(message) + "\n" + json.dumps(complete))[
        "successful_command_marker"
    ]
    assert (
        summary(json.dumps(command) + "\n" + json.dumps(complete) + "\nmalformed")[
            "parse_error_count"
        ]
        == 1
    )


def test_rejected_request_diagnostic_records_shapes_without_values(probe: dict[str, Any]) -> None:
    secret = "PRIVATE_VALUE_MUST_NEVER_APPEAR"
    body = {
        "model": "gpt-6.1-sol",
        "reasoning": {"effort": "high"},
        "stream": True,
        "store": False,
        "input": [
            {
                "type": "reasoning",
                "summary": [],
                "content": [
                    {
                        "type": "reasoning_text",
                        "text": secret,
                    }
                ],
                "encrypted_content": secret,
            }
        ],
    }
    raw = json.dumps(body).encode()
    with pytest.raises(ValueError) as error:
        vendor.validate_body(raw, vendor.GatewayConfig("gpt-6.1-sol", "high", 4, 4194304, 90))
    diagnostic = probe["rejection_diagnostic"](raw, error.value)
    serialized = json.dumps(diagnostic)
    assert secret not in serialized and "gpt-6.1-sol" not in serialized
    assert len(serialized.encode()) <= 16384
    sites = [site for site in diagnostic["frames"] if site["function"] == "_keys"]
    assert any(site.get("extra_keys") == ["content"] for site in sites)
    assert diagnostic["input_items"][0]["type"] == "reasoning"
    assert diagnostic["input_items"][0]["content_items"][0]["type"] == "reasoning_text"


def test_diagnostic_is_bounded_and_redacts_non_protocol_key_names(probe: dict[str, Any]) -> None:
    body = {"input": [{"type": "unknown-private-value", "PRIVATE_FIELD": "PRIVATE_VALUE"}] * 1000}
    diagnostic = probe["rejection_diagnostic"](
        json.dumps(body).encode(), ValueError("PRIVATE_ERROR")
    )
    serialized = json.dumps(diagnostic)
    assert "PRIVATE" not in serialized and "unknown-private-value" not in serialized
    assert len(diagnostic.get("input_items", [])) <= 16
    assert len(serialized.encode()) <= 16384


def test_diagnostic_metadata_only_records_collection_types_and_lengths(
    probe: dict[str, Any],
) -> None:
    body = {
        "model": "gpt-6.1-sol",
        "reasoning": {"effort": "high"},
        "store": False,
        "stream": True,
        "input": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "PRIVATE_TEXT",
                        "annotations": ["PRIVATE_ANNOTATION"],
                        "logprobs": [],
                    }
                ],
            }
        ],
    }
    raw = json.dumps(body).encode()
    with pytest.raises(ValueError) as error:
        vendor.validate_body(raw, vendor.GatewayConfig("gpt-6.1-sol", "high", 4, 4194304, 90))
    diagnostic = probe["rejection_diagnostic"](raw, error.value)
    metadata = diagnostic["input_items"][0]["content_items"][0]["metadata"]
    assert metadata == {
        "annotations": {"kind": "array", "length": 1},
        "logprobs": {"kind": "array", "length": 0},
    }
    assert "PRIVATE" not in json.dumps(diagnostic)
