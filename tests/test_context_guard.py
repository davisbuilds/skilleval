"""Disposable filesystem and request controls, not real VM evidence."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from skilleval import context_guard as guard
from skilleval import guest_runner, judge, study
from skilleval.errors import SkillEvalError


def roots(tmp_path: Path) -> tuple[Path, Path, list[dict[str, object]]]:
    root, work = tmp_path / "native", tmp_path / "task"
    (root / "home").mkdir(parents=True)
    (root / "codex-home").mkdir()
    (root / "codex-home/config.toml").write_text("generated")
    work.mkdir()
    (work / "AGENTS.md").write_text("shared task instructions")
    return root, work, guard.inventory(root / "codex-home")


def test_preflight_preserves_task_instructions_but_refuses_ambient_context(tmp_path: Path) -> None:
    root, work, expected = roots(tmp_path)
    observed = guard.preflight(root, work, expected, system=tmp_path / "image")
    assert observed["task_instructions"] == [
        {"path": "AGENTS.md", **guard.identity(b"shared task instructions")}
    ]
    ambient = tmp_path / "AGENTS.override.md"
    ambient.write_text("unintended parent")
    with pytest.raises(ValueError, match="ambient"):
        guard.preflight(root, work, expected, system=tmp_path / "image")
    assert ambient.read_text() == "unintended parent"


@pytest.mark.parametrize(
    "relative",
    ["etc/codex/hooks.json", "etc/codex/skills/x/SKILL.md", "root/.agents/skills/x/SKILL.md"],
)
def test_system_context_is_refused(tmp_path: Path, relative: str) -> None:
    root, work, expected = roots(tmp_path)
    system = tmp_path / "image"
    target = system / relative
    target.parent.mkdir(parents=True)
    target.write_text("synthetic ambient")
    with pytest.raises(ValueError, match="ambient"):
        guard.preflight(root, work, expected, system=system)


def test_previous_session_or_link_is_not_a_fresh_home(tmp_path: Path) -> None:
    root, work, expected = roots(tmp_path)
    old = root / "codex-home/sessions"
    old.mkdir()
    (old / "old.jsonl").write_text("old session")
    with pytest.raises(ValueError, match="home"):
        guard.preflight(root, work, expected, system=tmp_path / "image")
    link = root / "home/leak"
    link.symlink_to(work / "AGENTS.md")
    with pytest.raises(ValueError, match="link"):
        guard.inventory(root / "home")


def message(text: str, role: str = "user") -> dict[str, object]:
    return {"type": "message", "role": role, "content": [{"type": "input_text", "text": text}]}


def test_observation_attributes_instruction_sources_and_refuses_ambient_sources() -> None:
    shared = "# AGENTS.md instructions for /workspace/task\n<INSTRUCTIONS>\ncanary\n</INSTRUCTIONS>"
    raw = json.dumps({"input": [message(shared)]}).encode()
    observed = guard.request_context(raw)
    assert observed["messages"][0]["sha256"] == guard.identity(shared.encode())["sha256"]
    assert observed["messages"][0]["instruction_sources"] == ["/workspace/task"]
    assert "canary" not in json.dumps(observed)
    guard.check_request_context(observed, "/workspace/task", "/native/codex-home", None)
    contaminated = guard.request_context(raw.replace(b"/workspace/task", b"/host/home"))
    with pytest.raises(ValueError, match="instruction source"):
        guard.check_request_context(contaminated, "/workspace/task", "/native/codex-home", None)
    untyped = json.loads(raw)
    del untyped["input"][0]["type"]
    assert guard.request_context(json.dumps(untyped).encode()) == observed


def test_catalog_observation_preserves_builtins_and_rejects_other_skills() -> None:
    catalog = (
        "<skills_instructions>\n### Skill roots\n"
        "- `r0` = `/native/codex-home/skills/.system`\n"
        "- `r1` = `/native/codex-home/skills`\n### Available skills\n"
        "- builtin: Builtin. (file: r0/builtin/SKILL.md)\n"
        "- chosen: Chosen. (file: r1/chosen/SKILL.md)\n</skills_instructions>"
    )
    observed = guard.request_context(
        json.dumps({"input": [message(catalog, "developer")]}).encode()
    )
    guard.check_request_context(observed, "/workspace/task", "/native/codex-home", "chosen")
    with pytest.raises(ValueError, match="unexpected skill"):
        guard.check_request_context(observed, "/workspace/task", "/native/codex-home", None)
    observed["skill_catalogs"][0]["skills"][0]["name"] = "chosen"
    with pytest.raises(ValueError, match="collides"):
        guard.check_request_context(observed, "/workspace/task", "/native/codex-home", "chosen")


@pytest.mark.parametrize(
    "raw", [b"{}", b"{", b'{"input":[null]}', b'{"input":[{"type":"message"}]}']
)
def test_incomplete_observations_do_not_establish_clean_context(raw: bytes) -> None:
    with pytest.raises(ValueError):
        guard.request_context(raw)


def test_top_level_tools_are_observed_and_changes_are_visible() -> None:
    original = {"input": [], "tools": [{"type": "function", "name": "shell"}]}
    observed = guard.request_context(json.dumps(original).encode())
    assert observed["tools"]
    original["tools"][0]["name"] = "other"
    assert guard.request_context(json.dumps(original).encode())["tools"] != observed["tools"]


def test_message_image_identity_is_separate_from_instruction_prose() -> None:
    image = {"type": "input_image", "image_url": "data:image/png;base64,iVBORw0KGgo="}
    prose = "# AGENTS.md instructions for /workspace/task\nShared task context."
    content = [{"type": "input_text", "text": prose}, image]

    def observe() -> dict:
        return guard.request_context(
            json.dumps({"input": [{"role": "user", "content": content}]}).encode()
        )

    first = observe()
    assert first["messages"][0]["instruction_sources"] == ["/workspace/task"]
    assert first["messages"][0]["sha256"] == guard.identity(prose.encode())["sha256"]
    assert first["messages"][0]["images"][0]["index"] == 1
    image["detail"] = "high"
    assert observe()["messages"][0]["images"] != first["messages"][0]["images"]
    content.pop(0)
    only_image = observe()["messages"][0]
    assert only_image["bytes"] == 0
    assert "instruction_sources" not in only_image
    assert "data:image" not in json.dumps(only_image)


def test_plain_string_message_still_observes_instruction_sources() -> None:
    prose = "# AGENTS.md instructions for /host/home\nUnintended context."
    observed = guard.request_context(
        json.dumps({"input": [{"role": "user", "content": prose}]}).encode()
    )
    with pytest.raises(ValueError, match="instruction source"):
        guard.check_request_context(observed, "/workspace/task", "/native/codex-home", None)


def test_context_observation_covers_the_gateway_request_budget() -> None:
    from skilleval.gateway import GatewayLimits

    text = "x" * (4 * 1024 * 1024)
    raw = json.dumps({"input": [message(text)]}).encode()
    budget = GatewayLimits(max_body_bytes=8 * 1024 * 1024).max_body_bytes
    assert 4 * 1024 * 1024 < len(raw) < budget
    assert guard.request_context(raw)["messages"][0]["bytes"] == len(text)
    with pytest.raises(ValueError, match="context request exceeds limit"):
        guard.request_context(b" " * (budget + 1))


def test_tool_record_ids_do_not_change_capabilities_but_definitions_do() -> None:
    item = {
        "type": "additional_tools",
        "id": "at_first",
        "role": "developer",
        "tools": [{"type": "function", "name": "shell"}],
    }
    observed = guard.request_context(json.dumps({"input": [item]}).encode())["tools"]
    item["id"] = "at_second"
    assert guard.request_context(json.dumps({"input": [item]}).encode())["tools"] == observed
    item["tools"] = [{"type": "function", "name": "different"}]
    assert guard.request_context(json.dumps({"input": [item]}).encode())["tools"] != observed


def test_synthetic_controls_cannot_become_task_or_study_results(tmp_path: Path) -> None:
    report = {
        "schema_version": 1,
        "kind": "skilleval-native-context-control",
        "mode": "judge",
        "status": "completed",
    }
    (tmp_path / "native-run.json").write_text(json.dumps(report))
    with pytest.raises(SkillEvalError, match="invalid native"):
        judge._run_files(tmp_path)
    with pytest.raises(SkillEvalError, match="synthetic control"):
        study._check_native(tmp_path, report)


def test_standalone_guest_uses_copied_guard_not_installed_package(tmp_path: Path) -> None:
    copied = tmp_path / "native"
    copied.mkdir()
    (copied / "runner.py").write_bytes(Path(guest_runner.__file__).read_bytes())
    (copied / "context_guard.py").write_bytes(Path(guard.__file__).read_bytes())
    installed = tmp_path / "installed/skilleval"
    installed.mkdir(parents=True)
    (installed / "__init__.py").write_text("raise RuntimeError('wrong installed package loaded')")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy; runpy.run_path('runner.py',run_name='inspect_guest')",
        ],
        cwd=copied,
        env={**os.environ, "PYTHONPATH": str(installed.parent)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
