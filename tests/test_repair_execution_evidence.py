"""Bounded execution observations, not snapshot binding or complete edit detection."""

import hashlib
import json

import pytest

from skilleval import evidence
from skilleval.repair_assessment import _tool_evidence

POLICY = "execution-context-v3"


def event(kind="item.completed", *, item_type="command_execution", **fields):
    return {
        "type": kind,
        "item": {"type": item_type, **fields},
    }


def raw_events(*events):
    return b"".join(json.dumps(row).encode() + b"\n" for row in events)


def tools(*events):
    return _tool_evidence(raw_events(*events), POLICY)


@pytest.mark.parametrize(
    "policy,expected",
    [
        ("legacy-v1", "29c8e39f82bea9530557bb2a0720e2392ae2ee07afc44f1e1e6def71eb9bbf16"),
        (
            "application-context-v2",
            "fc29fde50fafefb937b4e8bbbe920e92233565fed34c97ebd669ea06ada66cb9",
        ),
    ],
)
def test_old_policy_bytes_remain_reconstructible(policy, expected):
    raw = raw_events(
        event("item.started", command="pnpm test"),
        event(command="pnpm test", aggregated_output=None, status="completed", exit_code=0),
        event(
            command="CODEX_HOME=/workspace/task/app pnpm test",
            aggregated_output="suite passed",
            status="completed",
            exit_code=0,
        ),
        event(
            command="git diff",
            aggregated_output="+const home = process.env.CODEX_HOME;",
            status="completed",
            exit_code=0,
        ),
        event(command="pnpm lint", aggregated_output="failed", status="failed", exit_code=1),
    )
    assert hashlib.sha256(evidence._json_bytes(_tool_evidence(raw, policy))).hexdigest() == expected


@pytest.mark.parametrize(
    "output_fields,expected,state",
    [
        ({"aggregated_output": None}, None, "null"),
        ({}, None, "missing"),
        ({"aggregated_output": ""}, "", "captured"),
        ({"aggregated_output": 17}, None, "invalid"),
    ],
)
def test_outputless_completions_retain_status_exit_and_output_state(output_fields, expected, state):
    result = tools(
        event(command="pnpm test > test.log 2>&1", status="failed", exit_code=1, **output_fields)
    )
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["status"] == "failed" and item["exit_code"] == 1
    assert item["output"] == expected and item["output_state"] == state
    assert item["event_index"] == 1


def test_source_diff_home_lookups_are_retained_as_application_evidence():
    output = (
        "diff --git a/src/home.ts b/src/home.ts\n"
        "+const home = process.env.CODEX_HOME;\n"
        "+// import history from $CODEX_HOME/sessions\n"
        "+const other = os.environ.get('CODEX_HOME')\n"
    )
    result = tools(
        event(command="git diff -- src", aggregated_output=output, status="completed", exit_code=0)
    )
    assert result["items"][0]["output"] == output
    assert result["items"][0]["output_redacted"] is False
    assert result["gaps"]["output_lines_redacted"] == 0


@pytest.mark.parametrize(
    "command",
    [
        "CODEX_HOME=/workspace/task/sample-history/codex-home pnpm test",
        "python3 - <<'PY'\nimport os, subprocess\n"
        "env = dict(os.environ, CODEX_HOME='/workspace/task/app-home')\n"
        "subprocess.run(['pnpm', 'test'], env=env)\nPY",
    ],
)
def test_application_home_settings_remain_observable_positive_controls(command):
    result = tools(
        event(command=command, status="completed", exit_code=0, aggregated_output="pass")
    )
    assert result["items"][0]["command"] == command
    assert result["items"][0]["output"] == "pass"
    assert result["chronology"][0]["command"] == command
    assert result["gaps"]["command_events_withheld"] == 0


@pytest.mark.parametrize(
    "lookup",
    [
        'cat "$CODEX_HOME/config.toml"',
        'cat "${CODEX_HOME}/config.toml"',
        "printenv CODEX_HOME",
        "node -p process.env.CODEX_HOME",
        "python3 -c 'import os; print(os.environ[\"CODEX_HOME\"])'",
    ],
)
def test_direct_harness_lookups_are_withheld_from_items_and_chronology(lookup):
    result = tools(event(command=lookup, aggregated_output="do not disclose", id="c1"))
    assert result["items"] == [] and result["chronology"] == []
    assert result["gaps"]["command_events_withheld"] == 1
    assert lookup not in json.dumps(result) and "do not disclose" not in json.dumps(result)


@pytest.mark.parametrize("token", ["/var/tmp/native", "/run/native", "/skills/", "SKILL.md"])
def test_harness_tokens_are_checked_before_truncation_and_output_is_explicitly_redacted(token):
    result = tools(
        event("item.started", command="x" * 9000 + token, id="secret"),
        event(
            command="pnpm test",
            aggregated_output="passed\n" + "x" * 70000 + token + "\n",
            id="safe",
        ),
    )
    assert result["gaps"]["command_events_withheld"] == 1
    assert result["gaps"]["output_lines_redacted"] == 1
    assert result["items"][0]["output"] == "passed\n[withheld harness-access output line]\n"
    assert result["items"][0]["output_redacted"] is True
    assert token not in json.dumps(result)


def test_chronology_preserves_intervening_edits_and_native_ids_without_inferred_binding():
    result = tools(
        event(
            "item.started",
            command="pnpm test > test.log",
            id="suite",
            cwd="/workspace/task",
            status="in_progress",
        ),
        event(
            item_type="file_change",
            id="patch",
            status="completed",
            changes=[
                {"path": "/workspace/task/src/a.ts", "kind": {"type": "update"}, "diff": "+fixed"},
            ],
        ),
        event(
            command="cp /tmp/fixed src/a.ts",
            id="restore",
            status="completed",
            exit_code=0,
            aggregated_output=None,
        ),
        event(
            command="pnpm test > test.log",
            id="suite",
            cwd="/workspace/task",
            status="failed",
            exit_code=1,
            aggregated_output=None,
        ),
    )
    rows = result["chronology"]
    assert [(r["event_index"], r["event_type"], r["item_type"], r["item_id"]) for r in rows] == [
        (1, "item.started", "command_execution", "suite"),
        (2, "item.completed", "file_change", "patch"),
        (3, "item.completed", "command_execution", "restore"),
        (4, "item.completed", "command_execution", "suite"),
    ]
    assert rows[0]["cwd"] == "/workspace/task"
    assert rows[1]["changes"] == [{"path": "/workspace/task/src/a.ts", "kind": "update"}]
    assert rows[3]["item_index"] == 2
    assert "+fixed" not in json.dumps(result)
    assert all(row.get("candidate_sha256") is None for row in rows)
    assert "not an immutable candidate check" in " ".join(result["limits"])
    assert "shell" in " ".join(result["limits"])


def test_absent_ids_cwd_and_missing_completion_remain_unknown():
    result = tools(event("item.started", command="pnpm test", status="in_progress"))
    assert result["items"] == []
    assert result["chronology"][0]["item_id"] is None
    assert result["chronology"][0]["cwd"] is None
    assert result["chronology"][0]["exit_code"] is None
    assert result["chronology"][0]["item_index"] is None


def test_scope_and_relative_source_event_indices_survive_non_tool_events():
    started = event("item.started", command="pnpm test", id="c1", cwd="/workspace/task")
    completed = event(command="pnpm test", id="c1", aggregated_output="", exit_code=0)
    for row in (started, completed):
        row.update(thread_id="thread-one", turn_id="turn-two")
    result = tools(
        {"type": "turn.started"},
        started,
        event("item.started", item_type="file_change", id="f1", changes=[]),
        event(item_type="agent_message", text="all pass"),
        completed,
    )
    assert [row["event_index"] for row in result["chronology"]] == [2, 3, 5]
    assert result["chronology"][0]["thread_id"] == "thread-one"
    assert result["chronology"][-1]["turn_id"] == "turn-two"
    assert result["chronology"][1]["event_type"] == "item.started"
    assert result["items"][0]["event_index"] == 5


def test_oversize_and_masked_metadata_become_unknown_instead_of_truncated_identities():
    result = tools(event(command="pnpm test", id="x" * 257, cwd="/run/native/private"))
    row = result["chronology"][0]
    assert row["item_id"] is None and row["cwd"] is None
    assert result["gaps"]["metadata_withheld"] == 2
    assert "/run/native/private" not in json.dumps(result)


def test_malformed_and_masked_file_paths_have_visible_gaps_and_bounds():
    result = tools(
        event(command=None),
        event(item_type="file_change", changes="not an array"),
        event(
            item_type="file_change",
            changes=[
                {"path": "/run/native/secret", "kind": {"type": "add"}, "diff": "secret"},
                {"path": "/workspace/task/a.ts", "kind": {"type": "update"}, "diff": "+fixed"},
            ],
        ),
    )
    assert result["gaps"]["malformed_command_events"] == 1
    assert result["gaps"]["malformed_file_change_events"] == 1
    assert result["gaps"]["file_changes_withheld"] == 1
    assert result["chronology"][0]["changes"] == [
        {"path": "/workspace/task/a.ts", "kind": "update"}
    ]
    assert "secret" not in json.dumps(result)


def test_v3_item_and_chronology_limits_are_independent_and_explicit():
    rows = [event("item.started", command="pnpm test", id=f"start-{i}") for i in range(513)]
    rows += [
        event(command="pnpm test", aggregated_output="pass", id=f"done-{i}") for i in range(257)
    ]
    result = tools(*rows)
    assert len(result["items"]) == 256 and result["items_truncated"] is True
    assert len(result["chronology"]) == 512 and result["chronology_truncated"] is True
    assert result["chronology"][0]["event_index"] == 1


def test_file_path_and_display_bounds_preserve_explicit_truncation_flags():
    changes = [{"path": f"/workspace/task/file-{i}", "kind": "add"} for i in range(129)]
    changes[0]["path"] = "x" * 4100
    result = tools(
        event(item_type="file_change", changes=changes),
        event(command="x" * 9000, aggregated_output="y" * 70000, exit_code=1),
    )
    assert len(result["chronology"][0]["changes"]) == 128
    assert len(result["chronology"][0]["changes"][0]["path"]) == 4096
    assert result["chronology"][0]["changes_truncated"] is True
    assert result["chronology"][1]["command_truncated"] is True
    assert result["items"][0]["truncated"] is True
    assert len(result["items"][0]["command"]) == 8192
    assert len(result["items"][0]["output"]) == 65536


@pytest.mark.parametrize(
    "destination,expected,omitted",
    [
        ("/workspace/task/checked.ts", "/workspace/task/checked.ts", False),
        (None, None, False),
        ("/run/native/checked.ts", None, True),
        ("x" * 4100, None, True),
        ("x" * 4100 + "/skills/checked.ts", None, True),
    ],
    ids=["rename", "null", "blocked", "overlong", "late-blocked"],
)
def test_native_rename_destination_is_retained_or_explicitly_omitted(
    destination, expected, omitted
):
    result = tools(
        event(
            item_type="file_change",
            id="rename-1",
            status="completed",
            changes=[
                {
                    "path": "/workspace/task/old.ts",
                    "kind": {"type": "update", "move_path": destination},
                    "diff": "+renamed content",
                }
            ],
        )
    )
    change = result["chronology"][0]["changes"][0]
    assert change["path"] == "/workspace/task/old.ts" and change["kind"] == "update"
    assert change["move_path"] == expected
    assert change.get("move_path_omitted", False) is omitted
    assert result["gaps"]["metadata_withheld"] == int(omitted)
    if omitted:
        assert destination not in json.dumps(result)
    assert "+renamed content" not in json.dumps(result)
