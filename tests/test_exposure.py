"""Public synthetic request controls; no live native discovery claim."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from skilleval import runner
from skilleval.treatments import capture_skill, request_observer

BODY = (
    "---\nname: public-control\ndescription: Inspect this public synthetic change.\n---\n"
    "Trace the supplied callers and report only defects supported by source evidence.\n"
)
SKILL_PATH = "/run/native/codex-home/skills/public-control/SKILL.md"


def detector(tmp_path: Path) -> Callable[[bytes], dict[str, Any]]:
    source = tmp_path / "skill"
    source.mkdir()
    (source / "SKILL.md").write_text(BODY)
    bundle = tmp_path / "bundle"
    receipt = capture_skill(
        source,
        None,
        bundle,
        source_url="https://example.invalid/public-control",
        source_revision="synthetic-v1",
    )
    return request_observer(bundle, receipt["manifest_sha256"], SKILL_PATH)


def test_structure_counts_all_input_items_without_retaining_unknown_values() -> None:
    request = {
        "instructions": "PRIVATE_INSTRUCTIONS",
        "PRIVATE_FIELD": "PRIVATE_VALUE",
        "input": [
            {"type": "message", "role": "developer", "content": "PRIVATE_TEXT"} for _ in range(9)
        ]
        + [
            {"type": "function_call_output", "call_id": "PRIVATE_ID", "output": "PRIVATE"},
            {"type": "PRIVATE_KIND", "role": "PRIVATE_ROLE"},
            {"content": "PRIVATE"},
            "PRIVATE_ITEM",
        ],
    }
    result = runner.protocol_shape(json.dumps(request).encode())
    structure = result["request_structure"]
    assert structure["fields"]["instructions"] == "str"
    assert structure["unknown_root_field_count"] == 1
    assert structure["input"] == {
        "type": "list",
        "item_count": 13,
        "non_object_count": 1,
        "kind_counts": {"message": 9, "function_call_output": 1, "unknown": 1, "missing": 1},
        "role_counts": {"developer": 9, "unknown": 1, "missing": 2},
    }
    assert result["shape"]["items_omitted"] == 5
    assert result["shape_limits"] == {"max_depth": 3, "list_items": 8}
    assert "PRIVATE" not in json.dumps(result)
    assert len(json.dumps(result)) < 16_384


@pytest.mark.parametrize("array_content", [False, True])
def test_actual_response_input_forms_preserve_exact_body_detection(
    tmp_path: Path, array_content: bool
) -> None:
    observe = detector(tmp_path)
    output = "Exit code: 0\nOutput:\n" + BODY
    request = {
        "instructions": "Ordinary instruction.",
        "input": [
            {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": f"public-control: {SKILL_PATH}"}],
            },
            {
                "type": "function_call_output",
                "call_id": "PRIVATE_ID",
                "output": [{"type": "input_text", "text": output}] if array_content else output,
            },
        ],
    }
    result = observe(json.dumps(request).encode())
    assert result["skill_body_observed"] is True
    # Name/path alone do not establish an exact catalog entry.
    assert result["skill_metadata_observed"] is False
    matches = result["match_diagnostics"]
    assert matches["input"]["name"] == 2
    assert matches["input"]["path"] == 1
    assert matches["input"]["body"] == 1
    assert matches["instructions"]["body"] == 0
    assert result["absence_established"] is False
    encoded = json.dumps(result)
    for value in ("PRIVATE", BODY, SKILL_PATH, "Inspect this public synthetic change."):
        assert value not in encoded


def test_diagnostics_distinguish_split_metadata_from_unobserved_content(tmp_path: Path) -> None:
    observe = detector(tmp_path)
    result = observe(
        json.dumps(
            {
                "instructions": "public-control",
                "input": [{"content": "Inspect this public synthetic change."}],
                "metadata": {"PRIVATE_KEY": SKILL_PATH},
            }
        ).encode()
    )
    assert result["skill_metadata_observed"] is False
    assert result["skill_body_observed"] is False
    assert result["match_diagnostics"]["instructions"]["name"] == 1
    assert result["match_diagnostics"]["input"]["description"] == 1
    assert result["match_diagnostics"]["other"]["path"] == 1
    assert observe(b"{invalid")["match_diagnostics"] is None
    assert result["absence_established"] is False


def test_partial_body_is_not_promoted_by_component_matches(tmp_path: Path) -> None:
    observe = detector(tmp_path)
    result = observe(json.dumps({"input": [{"output": BODY[:90]}, {"output": BODY[90:]}]}).encode())
    assert result["skill_body_observed"] is False
    assert result["match_diagnostics"]["input"]["body"] == 0
    assert result["absence_established"] is False


def test_structure_declares_missing_or_invalid_input_without_inventing_empty_history() -> None:
    assert runner.protocol_shape(b"{}")["request_structure"]["input"] == {"type": "missing"}
    assert runner.protocol_shape(b"null")["request_structure"] == {
        "schema_version": 1,
        "root_type": "NoneType",
    }
    assert runner.protocol_shape(b"{invalid") == {"shape": "invalid-json"}
