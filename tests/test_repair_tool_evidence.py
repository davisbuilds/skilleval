"""Command evidence filtering, not proof of independent treatment blinding."""

import json

import pytest

from skilleval.repair_assessment import _tool_evidence


def command_event(command, output="checks passed", *, exit_code=0):
    return (
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": command,
                    "aggregated_output": output,
                    "status": "completed",
                    "exit_code": exit_code,
                },
            }
        ).encode()
        + b"\n"
    )


@pytest.mark.parametrize(
    "command,output",
    [
        (
            "python3 - <<'PY'\nimport os, subprocess\n"
            "env = dict(os.environ, CODEX_HOME='/workspace/task/sample-history/codex-home')\n"
            "result = subprocess.run(['pnpm', 'test'], env=env)\n"
            "raise SystemExit(result.returncode)\nPY",
            "1681 passed; 5 skipped",
        ),
        ("CODEX_HOME=/workspace/task/sample-history/codex-home pnpm test", "suite passed"),
        ("pnpm test", "Application fixture uses CODEX_HOME; suite passed"),
    ],
)
def test_application_context_evidence_is_retained_with_original_status(command, output):
    raw = command_event(command, output)
    assert _tool_evidence(raw)["items"] == []
    corrected = _tool_evidence(raw, "application-context-v2")
    assert corrected["items"] == [
        {
            "command": command,
            "status": "completed",
            "exit_code": 0,
            "output": output,
            "truncated": False,
        }
    ]


@pytest.mark.parametrize("token", ["/var/tmp/native", "/run/native", "/skills/", "SKILL.md"])
@pytest.mark.parametrize("where", ["command", "output", "late-command", "late-output"])
def test_harness_and_skill_access_remains_withheld_before_display_truncation(token, where):
    command = "CODEX_HOME=/workspace/task/application pnpm test"
    output = "checks passed"
    if where.endswith("command"):
        command += (" " * 8192 if where.startswith("late") else " ") + token
    else:
        output += (" " * 65536 if where.startswith("late") else " ") + token
    raw = command_event(command, output)
    assert _tool_evidence(raw, "application-context-v2")["items"] == []


@pytest.mark.parametrize(
    "command",
    [
        'cat "$CODEX_HOME/config.toml"',
        'cat "${CODEX_HOME}/config.toml"',
        "printenv CODEX_HOME",
        "python3 -c 'import os; print(os.environ[\"CODEX_HOME\"])'",
        "python3 -c 'import os; print(os.environ.get(\"CODEX_HOME\"))'",
        "python3 -c 'import os; print(os.getenv(\"CODEX_HOME\"))'",
        "node -p process.env.CODEX_HOME",
    ],
)
def test_direct_harness_home_lookups_remain_withheld(command):
    assert _tool_evidence(command_event(command), "application-context-v2")["items"] == []


def test_truncated_observations_preserve_failure_and_item_limits():
    command = "x" * 9000
    output = "y" * 70000
    raw = command_event(command, output, exit_code=1) * 257
    tools = _tool_evidence(raw, "application-context-v2")
    assert len(tools["items"]) == 256
    assert tools["items_truncated"] is True
    assert tools["items"][0] == {
        "command": "x" * 8192,
        "output": "y" * 65536,
        "status": "completed",
        "exit_code": 1,
        "truncated": True,
    }


def test_non_command_and_incomplete_events_do_not_become_verification():
    raw = b'{"type":"item.started","item":{"type":"command_execution"}}\n'
    raw += b'{"type":"item.completed","item":{"type":"agent_message","text":"all pass"}}\n'
    raw += command_event("pnpm test", "failure", exit_code=1)
    tools = _tool_evidence(raw, "application-context-v2")
    assert len(tools["items"]) == 1
    assert tools["items"][0]["exit_code"] == 1
