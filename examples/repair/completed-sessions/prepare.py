#!/usr/bin/env python3
"""Prepare the pinned completed-session inputs without executing imported code."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from skilleval import evidence
from skilleval.errors import SkillEvalError
from skilleval.treatments import capture_skill, check_task_baseline

BASELINE = "5f681e21c8af7766bb21efe60d15bae33c4ac68b"
BASELINE_FILE_COUNT = 620
REFERENCE = "f7009616a272eba3ff96bcb851457cc2b8e4b66c"
SKILL_REVISION = "a496f5513d4a4cf889bc48e189e579f4b0bdf6aa"
SOURCE_URL = "https://github.com/davisbuilds/agentmonitor"
SKILL_URL = "https://github.com/mattpocock/skills"
SKILL_PATH = "skills/engineering/diagnosing-bugs"
SKILL_FILES = {"SKILL.md", "agents/openai.yaml", "scripts/hitl-loop.template.sh"}


def git(root: Path, *args: str, data: bytes = b"", missing: bool = False) -> bytes | None:
    binary = shutil.which("git", path=os.defpath)
    if binary is None:
        raise SkillEvalError("Git is required")
    env = {
        "PATH": os.defpath,
        "HOME": str(root / ".git"),
        "XDG_CONFIG_HOME": str(root / ".git"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_ALLOW_PROTOCOL": "",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "Example maintainer",
        "GIT_COMMITTER_NAME": "Example maintainer",
        "GIT_AUTHOR_EMAIL": "maintainer@example.com",
        "GIT_COMMITTER_EMAIL": "maintainer@example.com",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+0000",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+0000",
        "LC_ALL": "C",
    }
    result = subprocess.run(
        [
            binary,
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            f"core.attributesFile={os.devnull}",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.autocrlf=false",
            "-c",
            f"safe.directory={root}",
            "-C",
            str(root),
            *args,
        ],
        input=data,
        capture_output=True,
        env=env,
        timeout=60,
        check=False,
    )
    if result.returncode:
        if missing and result.returncode == 1:
            return None
        raise SkillEvalError("Git input preparation failed")
    return result.stdout


def tree(repo: Path, revision: str) -> dict[str, tuple[str, str]]:
    raw = git(repo, "ls-tree", "-rz", "--full-tree", revision)
    assert raw is not None
    entries = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        header, name = record.split(b"\t", 1)
        mode, kind, oid = header.decode("ascii").split()
        path = name.decode("ascii")
        if (
            not re.fullmatch(r"[A-Za-z0-9_./@+-]+", path)
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or any(part.casefold() in {".git", ".codex"} for part in path.split("/"))
            or kind != "blob"
            or mode not in {"100644", "100755", "120000"}
        ):
            raise SkillEvalError("unsupported pinned source entry")
        if path.casefold() in {value.casefold() for value in entries}:
            raise SkillEvalError("case-aliased source entries")
        entries[path] = (mode, oid)
    return entries


def export(repo: Path, entries: dict[str, tuple[str, str]], destination: Path) -> list[str]:
    destination.mkdir()
    materialized: list[str] = []
    for name, (mode, oid) in entries.items():
        if mode == "120000":
            if name not in {"CLAUDE.md", "frontend/CLAUDE.md"}:
                raise SkillEvalError("unsupported source link")
            if git(repo, "cat-file", "blob", oid) != b"AGENTS.md":
                raise SkillEvalError("unexpected instruction source link")
            target_name = (Path(name).parent / "AGENTS.md").as_posix()
            target_entry = entries.get(target_name)
            if target_entry is None or target_entry[0] != "100644":
                raise SkillEvalError("instruction source link lacks its regular target")
            mode, oid = target_entry
            materialized.append(name)
        raw = git(repo, "cat-file", "blob", oid)
        assert raw is not None
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        target.chmod(0o755 if mode == "100755" else 0o644)
    return materialized


def seed_git(source: Path) -> dict[str, Any]:
    files = check_task_baseline(source)["files"]
    git(source, "init", "--quiet", "--template=", "--initial-branch=main")
    rows = []
    # Direct index construction includes every explicit file, even ignored archives,
    # and never invokes repository attributes, clean filters or hooks.
    for row in files:
        if row["path"].startswith(".git/"):
            continue
        oid = git(
            source,
            "hash-object",
            "-w",
            "--no-filters",
            "--stdin",
            data=(source / row["path"]).read_bytes(),
        )
        assert oid is not None
        mode = "100755" if row["executable"] else "100644"
        rows.append(f"{mode} {oid.decode().strip()}\t{row['path']}\0".encode("ascii"))
    git(source, "update-index", "-z", "--index-info", data=b"".join(rows))
    tree_id = git(source, "write-tree")
    assert tree_id is not None
    commit = git(source, "commit-tree", tree_id.decode().strip(), data=b"Project snapshot\n")
    assert commit is not None
    git(source, "update-ref", "refs/heads/main", commit.decode().strip())
    # Refresh stat metadata once before copying the same baseline Git view.
    status = git(source, "status", "--porcelain")
    if status:
        raise SkillEvalError("synthetic baseline is not clean")
    git(source, "fsck", "--no-reflogs", "--full")
    return {
        "commit": commit.decode().strip(),
        "tree": tree_id.decode().strip(),
        "branch": "main",
        "indexed_files": len(rows),
    }


def object_controls(
    repo: Path,
    baseline: dict[str, tuple[str, str]],
    reference: dict[str, tuple[str, str]],
    source: Path,
) -> dict[str, Any]:
    positive = baseline["AGENTS.md"][1]
    original = git(repo, "cat-file", "blob", positive)
    if git(source, "cat-file", "blob", positive) != original:
        raise SkillEvalError("baseline positive object control failed")
    baseline_blobs = {oid for _, oid in baseline.values()}
    distinct = sorted({oid for _, oid in reference.values()} - baseline_blobs)
    absent = [REFERENCE, *distinct]
    for oid in absent:
        # Establish each excluded object exists in the upstream store first.
        git(repo, "cat-file", "-e", oid)
        if git(source, "cat-file", "-e", oid, missing=True) is not None:
            raise SkillEvalError("reference object leaked into baseline Git")
    return {"positive_baseline_blob": positive, "excluded_reference_objects": absent}


def prepare(source_repo: Path, skill_repo: Path, fixtures: Path, output: Path) -> dict[str, Any]:
    if os.path.lexists(output):
        raise SkillEvalError("output must be fresh with an existing parent")
    source_repo, skill_repo, fixtures, output = (
        value.resolve() for value in (source_repo, skill_repo, fixtures, output)
    )
    for upstream in (source_repo, skill_repo, fixtures):
        if output.is_relative_to(upstream) or upstream.is_relative_to(output):
            raise SkillEvalError("output and supplied inputs must not be nested")
    if os.path.lexists(output) or not output.parent.is_dir():
        raise SkillEvalError("output must be fresh with an existing parent")
    baseline, reference = tree(source_repo, BASELINE), tree(source_repo, REFERENCE)
    if len(baseline) != BASELINE_FILE_COUNT:
        raise SkillEvalError("pinned baseline inventory differs")
    skill_entries = tree(skill_repo, SKILL_REVISION)
    selected = {
        name.removeprefix(SKILL_PATH + "/"): row
        for name, row in skill_entries.items()
        if name.startswith(SKILL_PATH + "/")
    }
    if set(selected) != SKILL_FILES or "LICENSE" not in skill_entries:
        raise SkillEvalError("pinned skill closure differs")
    fixture_files = check_task_baseline(fixtures)["files"]
    if not fixture_files:
        raise SkillEvalError("supply nonempty authored session fixtures")
    for row in fixture_files:
        name = row["path"]
        if not re.fullmatch(r"[A-Za-z0-9_./@+-]+", name) or any(
            part.casefold() == ".git" for part in name.split("/")
        ):
            raise SkillEvalError("unsupported fixture path")
    temporary = Path(tempfile.mkdtemp(prefix=".completed-sessions-", dir=output.parent))
    try:
        base = temporary / "baseline-source"
        links = export(source_repo, baseline, base)
        if (base / "sample-history").exists():
            raise SkillEvalError("fixture directory collides with pinned source")
        shutil.copytree(fixtures, base / "sample-history")
        git_view = seed_git(base)
        controls = object_controls(source_repo, baseline, reference, base)
        ref = temporary / "reference-source"
        shutil.copytree(base, ref)
        overlay = temporary / "reference-overlay"
        reference_links = export(source_repo, reference, overlay)
        for name in baseline.keys() - reference.keys():
            (ref / name).unlink()
        shutil.copytree(overlay, ref, dirs_exist_ok=True)
        shutil.rmtree(overlay)
        # The reference retains the baseline index/HEAD and no repair objects.
        object_controls(source_repo, baseline, reference, ref)
        skill = temporary / "selected-skill"
        export(skill_repo, selected, skill)
        notice = temporary / "ROOT_LICENSE"
        raw_notice = git(skill_repo, "cat-file", "blob", skill_entries["LICENSE"][1])
        assert raw_notice is not None
        notice.write_bytes(raw_notice)
        captures = {
            "baseline": evidence.snapshot(base, temporary / "baseline-capture"),
            "reference": evidence.snapshot(ref, temporary / "reference-capture"),
            "skill": capture_skill(
                skill,
                notice,
                temporary / "skill-capture",
                source_url=SKILL_URL,
                source_revision=SKILL_REVISION,
            ),
        }
        shutil.rmtree(skill)
        notice.unlink()
        for name, receipt in captures.items():
            receipt["path"] = str(output / (name + "-capture"))
        provenance = {
            "schema_version": 1,
            "source_url": SOURCE_URL,
            "baseline_revision": BASELINE,
            "reference_revision": REFERENCE,
            "baseline_tracked_files": len(baseline),
            "skill_url": SKILL_URL,
            "skill_revision": SKILL_REVISION,
            "materialized_instruction_links": links,
            "reference_materialized_instruction_links": reference_links,
            "synthetic_git": git_view,
            "object_controls": controls,
            "fixtures": fixture_files,
            "captures": captures,
            "limitations": [
                "Ordinary baseline BACKLOG.md already identifies the defect and proposed fix.",
                "CLAUDE.md links are regular copies of their own AGENTS.md targets.",
                "Synthetic main history supports indexed files and HEAD, not historical bisection.",
                "Reference keeps baseline Git metadata; repair bytes are working-tree changes.",
                "No imported source was executed; runtime and checker qualification remain open.",
            ],
        }
        raw = (json.dumps(provenance, sort_keys=True, indent=2) + "\n").encode()
        (temporary / "provenance.json").write_bytes(raw)
        (temporary / "provenance.json").chmod(0o444)
        evidence._publish(temporary, output)
        return {
            "output": str(output),
            "provenance_sha256": hashlib.sha256(raw).hexdigest(),
            "captures": captures,
        }
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-repo", "skill-repo", "fixtures", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.source_repo, args.skill_repo, args.fixtures, args.output)
    except (OSError, ValueError, subprocess.TimeoutExpired, SkillEvalError) as exc:
        parser.exit(1, f"Preparation failed: {exc}\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
