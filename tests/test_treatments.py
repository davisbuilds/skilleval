"""Offline input fidelity and telemetry controls; no native discovery claim."""

import json
from pathlib import Path
from typing import Any

import pytest

from skilleval import evidence
from skilleval.errors import SkillEvalError
from skilleval.treatments import (
    capture_skill,
    check_task_baseline,
    install_skill,
    observe_consultation,
    prepare_pair,
)

BODY = (
    b"---\nname: careful-review\ndescription: Inspect a change with repository evidence.\n---\n"
    b"Read the affected callers and trace their data flow before reporting a defect.\n"
)
NATIVE_PATH = "/run/native/codex-home/skills/careful-review/SKILL.md"


def captured(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "selected"
    source.mkdir()
    (source / "SKILL.md").write_bytes(BODY)
    (source / "references").mkdir()
    (source / "references" / "flow.md").write_bytes(b"Trace every consumer.\r\n")
    (source / "scripts").mkdir()
    script = source / "scripts" / "inspect.sh"
    script.write_bytes(b"#!/bin/sh\nprintf 'fixture only\\n'\n")
    script.chmod(0o755)
    notice = tmp_path / "LICENSE"
    notice.write_bytes(b"Synthetic fixture notice.\n")
    bundle = tmp_path / "captured"
    receipt = capture_skill(
        source,
        notice,
        bundle,
        source_url="https://example.invalid/skills",
        source_revision="fixture-revision",
    )
    return bundle, receipt["manifest_sha256"]


def test_capture_and_install_preserve_full_tree_bytes_modes_and_notice(tmp_path: Path) -> None:
    bundle, digest = captured(tmp_path)
    home = tmp_path / "isolated-home"
    receipt = install_skill(bundle, digest, home)
    skill = home / "skills" / "careful-review"
    assert (skill / "SKILL.md").read_bytes() == BODY
    assert (skill / "references" / "flow.md").read_bytes() == b"Trace every consumer.\r\n"
    assert (skill / "scripts" / "inspect.sh").stat().st_mode & 0o111
    assert not (skill / "SKILL.md").stat().st_mode & 0o111
    assert (skill / ".source-notices" / "ROOT_LICENSE").read_bytes() == (
        tmp_path / "LICENSE"
    ).read_bytes()
    assert receipt["manifest_sha256"] == digest
    assert {entry["path"] for entry in receipt["files"]} == {
        "skills/careful-review/SKILL.md",
        "skills/careful-review/references/flow.md",
        "skills/careful-review/scripts/inspect.sh",
        "skills/careful-review/.source-notices/ROOT_LICENSE",
    }
    assert list(home.iterdir()) == [home / "skills"]
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    assert str(tmp_path) not in json.dumps(manifest)
    assert manifest["meta"]["source_revision"] == "fixture-revision"
    assert manifest["meta"]["license_status"] == "provided"
    assert manifest["meta"]["notice_path"] == ".source-notices/ROOT_LICENSE"


def test_legacy_notice_is_preserved_without_rewriting_the_pinned_capture(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    bundle = tmp_path / "legacy"
    notice = "skills/careful-review/.skilleval-notices/ROOT_LICENSE"

    def populate(_descriptor: int, writer: evidence._Writer) -> None:
        writer.add("skills/careful-review/SKILL.md", BODY)
        writer.add(notice, b"Original upstream notice.\n")

    captured = evidence._capture(
        source,
        bundle,
        "snapshot",
        {
            "purpose": "skill-treatment",
            "skill_name": "careful-review",
            "license_status": "provided",
        },
        populate,
    )
    original = (bundle / "manifest.json").read_bytes()
    home = tmp_path / "home"
    installed = install_skill(bundle, captured["manifest_sha256"], home)
    assert (home / notice).read_bytes() == b"Original upstream notice.\n"
    assert not (home / "skills/careful-review/.source-notices").exists()
    assert (bundle / "manifest.json").read_bytes() == original
    assert installed["manifest_sha256"] == captured["manifest_sha256"]


@pytest.mark.parametrize("directory", [".source-notices", ".skilleval-notices"])
def test_reserved_notice_never_overwrites_upstream_material(tmp_path: Path, directory: str) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_bytes(BODY)
    (source / directory).mkdir()
    (source / directory / "ROOT_LICENSE").write_bytes(b"Existing upstream file.")
    with pytest.raises(SkillEvalError, match="reserved notice directory"):
        capture_skill(source, None, tmp_path / "bundle", source_url="x", source_revision="y")
    assert (source / directory / "ROOT_LICENSE").read_bytes() == b"Existing upstream file."
    assert not (tmp_path / "bundle").exists()


def test_matched_pair_preserves_instructions_git_inputs_and_exact_prompt(tmp_path: Path) -> None:
    bundle, digest = captured(tmp_path)
    task = tmp_path / "prepared"
    task.mkdir()
    (task / ".git").mkdir()
    (task / ".git" / "HEAD").write_bytes(b"ref: refs/heads/candidate\n")
    (task / "AGENTS.md").write_bytes(b"Use the project checks.\r\n")
    (task / "app.py").write_bytes(b"print('captured bytes')\n")
    prompt = b"Review the candidate changes against the base.\n"
    identity = check_task_baseline(task)
    destination = tmp_path / "pair"
    receipt = prepare_pair(
        task, identity["task_sha256"], bundle, digest, destination, prompt=prompt
    )
    assert receipt["invocation"] == "natural-discovery"
    assert check_task_baseline(destination / "absent" / "task") == identity
    assert check_task_baseline(destination / "available" / "task") == identity
    for arm in ("absent", "available"):
        assert (destination / arm / "prompt.txt").read_bytes() == prompt
        assert sorted(path.name for path in (destination / arm).iterdir()) == [
            "codex-home",
            "prompt.txt",
            "task",
        ]
    assert not list((destination / "absent" / "codex-home" / "skills").iterdir())
    assert (
        destination / "available" / "codex-home" / "skills" / "careful-review" / "SKILL.md"
    ).read_bytes() == BODY
    assert (destination / "treatment.json").exists()
    assert str(tmp_path) not in (destination / "treatment.json").read_text()


@pytest.mark.parametrize(
    "embedded",
    [
        ".agents/skills/existing/SKILL.md",
        ".codex/skills/x/SKILL.md",
        ".codex/config.toml",
        ".codex/hooks.json",
        "nested/.codex/managed_config.toml",
        ".codex/memories/MEMORY.md",
        ".codex/plugins/plugin.json",
    ],
)
def test_contaminated_baseline_is_refused_without_deleting_instructions(
    tmp_path: Path, embedded: str
) -> None:
    target = tmp_path / embedded
    target.parent.mkdir(parents=True)
    target.write_bytes(b"captured input must remain untouched\n")
    with pytest.raises(SkillEvalError, match="unsupported embedded"):
        check_task_baseline(tmp_path)
    assert target.read_bytes() == b"captured input must remain untouched\n"


def test_changed_skill_and_changed_task_invalidate_before_publication(tmp_path: Path) -> None:
    bundle, digest = captured(tmp_path)
    task = tmp_path / "task"
    task.mkdir()
    source = task / "app.py"
    source.write_bytes(b"first\n")
    identity = check_task_baseline(task)
    source.write_bytes(b"second\n")
    with pytest.raises(SkillEvalError, match="task inventory changed"):
        prepare_pair(task, identity["task_sha256"], bundle, digest, tmp_path / "pair", prompt=b"x")
    assert not (tmp_path / "pair").exists()
    skill = bundle / "skills" / "careful-review" / "SKILL.md"
    skill.chmod(0o644)
    skill.write_bytes(BODY + b"changed\n")
    with pytest.raises(SkillEvalError, match="content or inventory mismatch"):
        install_skill(bundle, digest, tmp_path / "home")
    assert not (tmp_path / "home").exists()


def test_existing_home_symlinked_source_and_missing_notice_are_rejected(tmp_path: Path) -> None:
    bundle, digest = captured(tmp_path)
    existing = tmp_path / "existing-home"
    existing.mkdir()
    (existing / "keep").write_bytes(b"do not alter")
    with pytest.raises(SkillEvalError, match="already exists"):
        install_skill(bundle, digest, existing)
    assert (existing / "keep").read_bytes() == b"do not alter"
    source = tmp_path / "selected"
    (source / "link").symlink_to(tmp_path / "LICENSE")
    with pytest.raises(SkillEvalError, match="symlinks"):
        capture_skill(
            source, tmp_path / "LICENSE", tmp_path / "bad", source_url="x", source_revision="y"
        )
    (source / "link").unlink()
    with pytest.raises(SkillEvalError, match="explicit root license"):
        capture_skill(
            source, tmp_path / "missing", tmp_path / "bad", source_url="x", source_revision="y"
        )


def command_event(**changes: Any) -> dict[str, Any]:
    item = {
        "id": "read-1",
        "type": "command_execution",
        "status": "completed",
        "exit_code": 0,
        "command": f"cat {NATIVE_PATH}",
        "aggregated_output": BODY.decode(),
    }
    item.update(changes)
    return {"type": "item.completed", "item": item}


def test_native_successful_read_with_complete_known_output_is_observed(tmp_path: Path) -> None:
    bundle, digest = captured(tmp_path)
    result = observe_consultation([command_event()], bundle, digest, NATIVE_PATH)
    assert result["consultation"] == "observed"
    assert result["observations"] == [{"event_index": 0, "item_id": "read-1"}]
    assert result["semantic_application"] == "unassessed"
    assert result["absence_established"] is False
    wrapped = command_event(command=f"/bin/zsh -lc 'sed -n 1,200p {NATIVE_PATH}'")
    assert (
        observe_consultation([wrapped], bundle, digest, NATIVE_PATH)["consultation"] == "observed"
    )


@pytest.mark.parametrize(
    "event",
    [
        command_event(status="failed", exit_code=1),
        command_event(exit_code=None),
        command_event(exit_code=False),
        command_event(aggregated_output=""),
        command_event(aggregated_output=BODY[:90].decode() + "...truncated..."),
        command_event(command=f"echo {NATIVE_PATH}"),
        command_event(command="cat /other/skills/careful-review/SKILL.md"),
        command_event(command=f"printf fake; cat {NATIVE_PATH}"),
        {"type": "item.started", "item": command_event()["item"]},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "I used the skill"}},
    ],
)
def test_failed_partial_self_reported_or_ambiguous_reads_stay_unmeasured(
    tmp_path: Path, event: dict[str, Any]
) -> None:
    bundle, digest = captured(tmp_path)
    result = observe_consultation([event], bundle, digest, NATIVE_PATH)
    assert result["consultation"] == "unmeasured"
    assert result["absence_established"] is False
    assert result["observations"] == []
    assert observe_consultation([], bundle, digest, NATIVE_PATH)["content_exposure"] == "unknown"


def test_actual_request_metadata_and_body_exposure_are_separate(tmp_path: Path) -> None:
    from skilleval.treatments import request_observer

    bundle, digest = captured(tmp_path)
    observer = request_observer(bundle, digest, NATIVE_PATH)
    description = "Inspect a change with repository evidence."
    catalog = f"careful-review: {description} (file: {NATIVE_PATH})"
    metadata = observer(json.dumps({"instructions": catalog, "input": []}).encode())
    assert metadata["skill_metadata_observed"] is True
    assert metadata["skill_body_observed"] is False
    body = observer(
        json.dumps({"instructions": catalog, "input": [{"output": BODY.decode()}]}).encode()
    )
    assert body["skill_metadata_observed"] is True
    assert body["skill_body_observed"] is True
    assert description not in json.dumps(body)
    assert NATIVE_PATH not in json.dumps(body)
    assert BODY.decode() not in json.dumps(body)
    assert body["absence_established"] is False
    assert observer(b"{invalid")["skill_body_observed"] is None
    assert observer(b"{}")["skill_body_observed"] is False
    partial = observer(json.dumps({"output": BODY[:90].decode()}).encode())
    assert partial["skill_body_observed"] is False


def test_executable_mode_change_invalidates_pinned_skill(tmp_path: Path) -> None:
    bundle, digest = captured(tmp_path)
    (bundle / "skills" / "careful-review" / "SKILL.md").chmod(0o755)
    with pytest.raises(SkillEvalError, match="content or inventory mismatch"):
        install_skill(bundle, digest, tmp_path / "home")
    assert not (tmp_path / "home").exists()


def test_missing_root_license_is_explicit_without_inventing_notice(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_bytes(BODY)
    (source / "NOTICE").write_bytes(b"Existing tree attribution must remain.\n")
    bundle = tmp_path / "bundle"
    receipt = capture_skill(
        source,
        None,
        bundle,
        source_url="https://example.invalid/user-owned-skill",
        source_revision="explicit-fixture",
    )
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    assert manifest["meta"]["license_status"] == "not-provided"
    assert not (bundle / "skills/careful-review/.source-notices").exists()
    home = tmp_path / "home"
    install_skill(bundle, receipt["manifest_sha256"], home)
    assert (home / "skills/careful-review/NOTICE").read_bytes() == (source / "NOTICE").read_bytes()
    assert not (home / "skills/careful-review/.source-notices").exists()
