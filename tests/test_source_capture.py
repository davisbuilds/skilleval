"""Real Git captures: exclusions must not survive in files or recoverable objects."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from skilleval import evidence
from skilleval.errors import SkillEvalError
from skilleval.source_capture import SourceExclusions, capture_source


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=check,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
    )


def commit(repo: Path) -> str:
    git(repo, "add", "--force", "--all")
    git(
        repo,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.com",
        "commit",
        "-qm",
        "fixture",
    )
    return git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def source(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repository"
    repo.mkdir()
    git(repo, "init", "--quiet", "--template=", "--initial-branch=main")
    for name, body in {
        "AGENTS.md": "Ordinary repository instructions.\n",
        "docs/archive/specs/old.md": "SPEC_EXEMPLAR_PRIVATE\n## Contract\n",
        "docs/archive/plans/old.md": "An ordinary retained plan.\n",
        "docs/specs-adjacent/keep.md": "Keep path-prefix neighbors.\n",
        "src/task.py": "raise RuntimeError('never execute imported source')\n",
        ".gitignore": "docs/archive/\n",
    }.items():
        file = repo / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(body)
    (repo / "src/task.py").chmod(0o755)
    (repo / "CLAUDE.md").symlink_to("AGENTS.md")
    revision = commit(repo)
    # A later commit and untracked/modified working files must not enter the capture.
    (repo / "future.md").write_text("FUTURE_PRIVATE\n")
    commit(repo)
    (repo / "AGENTS.md").write_text("Uncommitted operator edits.\n")
    (repo / "untracked-secret").write_text("PRIVATE_HOST_STATE\n")
    git(repo, "remote", "add", "origin", "https://invalid.example/private.git")
    return repo, revision


def policy(*paths: str) -> SourceExclusions:
    return SourceExclusions.model_validate(
        {
            "schema_version": 1,
            "exclude": [
                {"path": path, "reason": "Withhold prior specification examples"} for path in paths
            ],
        }
    )


def test_exclusion_hides_files_and_git_objects_with_retained_positive_control(source, tmp_path):
    repo, revision = source
    output = tmp_path / "selected"
    receipt = capture_source(repo, revision, output, exclusions=policy("docs/archive/specs"))
    baseline = output / "baseline"
    evidence.verify(baseline, receipt["baseline"]["manifest_sha256"])
    assert not (baseline / "docs/archive/specs/old.md").exists()
    assert (baseline / "docs/archive/plans/old.md").read_bytes() == (
        repo / "docs/archive/plans/old.md"
    ).read_bytes()
    assert (baseline / "docs/specs-adjacent/keep.md").exists()
    assert (baseline / "AGENTS.md").read_text() == "Ordinary repository instructions.\n"
    assert (baseline / "CLAUDE.md").read_bytes() == (baseline / "AGENTS.md").read_bytes()
    assert not (baseline / "CLAUDE.md").is_symlink()
    assert (baseline / "src/task.py").stat().st_mode & 0o111
    assert not (baseline / "future.md").exists()
    assert not (baseline / "untracked-secret").exists()
    assert not (baseline / "provenance.json").exists()
    assert git(baseline, "remote").stdout == ""
    assert git(baseline, "rev-list", "--all", "--count").stdout.strip() == "1"
    assert git(baseline, "status", "--porcelain").stdout == "?? manifest.json\n"
    excluded_oid = git(repo, "rev-parse", f"{revision}:docs/archive/specs/old.md").stdout.strip()
    assert git(repo, "cat-file", "-p", excluded_oid).stdout.startswith("SPEC_EXEMPLAR_PRIVATE")
    assert git(baseline, "cat-file", "-e", excluded_oid, check=False).returncode != 0
    assert git(baseline, "cat-file", "-e", revision, check=False).returncode != 0
    retained_oid = git(repo, "rev-parse", f"{revision}:AGENTS.md").stdout.strip()
    assert (
        git(baseline, "cat-file", "-p", retained_oid).stdout
        == "Ordinary repository instructions.\n"
    )
    provenance = json.loads((output / "provenance.json").read_text())
    assert provenance["excluded_files"][0]["path"] == "docs/archive/specs/old.md"
    assert provenance["excluded_files"][0]["git_blob"] == excluded_oid
    assert receipt["excluded_file_count"] == 1
    assert (
        receipt["provenance_sha256"]
        == hashlib.sha256((output / "provenance.json").read_bytes()).hexdigest()
    )
    assert git(repo, "status", "--porcelain").stdout == " M AGENTS.md\n?? untracked-secret\n"


def test_complete_capture_keeps_tracked_ignored_docs(source, tmp_path):
    repo, revision = source
    result = capture_source(repo, revision, tmp_path / "complete")
    assert result["excluded_file_count"] == 0
    assert (tmp_path / "complete/baseline/docs/archive/specs/old.md").exists()
    assert (tmp_path / "complete/baseline/docs/archive/plans/old.md").exists()


@pytest.mark.parametrize("selector", ["docs/absent", "docs/archive/spec"])
def test_unmatched_exclusion_does_not_publish(source, tmp_path, selector):
    repo, revision = source
    with pytest.raises(SkillEvalError, match="match"):
        capture_source(repo, revision, tmp_path / "out", exclusions=policy(selector))
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "paths",
    [
        ["../outside"],
        ["/absolute"],
        ["docs/**"],
        ["docs/"],
        [".git"],
        ["docs/../src"],
        ["docs", "docs/archive"],
        ["docs", "docs"],
        ["docs", "DOCS"],
    ],
)
def test_invalid_or_overlapping_paths_are_rejected(paths):
    with pytest.raises(ValidationError):
        policy(*paths)


def test_file_selector_is_exact_and_empty_alias_is_disclosed(source, tmp_path):
    repo, _ = source
    (repo / "docs/archive/specs/.gitkeep").write_text("")
    (repo / "retained-empty").write_text("")
    revision = commit(repo)
    result = capture_source(
        repo, revision, tmp_path / "out", exclusions=policy("docs/archive/specs")
    )
    assert result["excluded_file_count"] == 2
    provenance = json.loads((tmp_path / "out/provenance.json").read_text())
    assert len(provenance["object_controls"]["shared_empty_blobs"]) == 1
    assert len(provenance["object_controls"]["absent_excluded_blobs"]) == 1

    capture_source(
        repo, revision, tmp_path / "file-only", exclusions=policy("docs/archive/specs/old.md")
    )
    assert (tmp_path / "file-only/baseline/docs/archive/specs/.gitkeep").exists()
    assert not (tmp_path / "file-only/baseline/docs/archive/specs/old.md").exists()


def test_duplicate_nonempty_content_cannot_bypass_exclusion(source, tmp_path):
    repo, _ = source
    (repo / "copy.md").write_bytes((repo / "docs/archive/specs/old.md").read_bytes())
    revision = commit(repo)
    with pytest.raises(SkillEvalError, match="also exists"):
        capture_source(repo, revision, tmp_path / "out", exclusions=policy("docs/archive/specs"))
    assert not (tmp_path / "out").exists()


def test_instruction_exclusion_is_rejected(source, tmp_path):
    repo, revision = source
    with pytest.raises(SkillEvalError, match="ordinary agent instructions"):
        capture_source(repo, revision, tmp_path / "out", exclusions=policy("AGENTS.md"))
    assert not (tmp_path / "out").exists()


def test_source_hooks_and_filters_are_not_executed_and_failure_cleans_staging(source, tmp_path):
    repo, revision = source
    marker = tmp_path / "executed"
    # Only trusted fixture strings are interpolated; no imported code is invoked.
    hook = repo / ".git/hooks/post-checkout"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    hook.chmod(0o755)
    (repo / ".gitattributes").write_text("*.md filter=danger\n")
    # Write this fixture directly to the index; never trigger its configured filter.
    oid = git(repo, "hash-object", "-w", "--no-filters", ".gitattributes").stdout.strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"100644,{oid},.gitattributes")
    git(
        repo,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.com",
        "commit",
        "-qm",
        "attributes",
    )
    revision = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "config", "filter.danger.clean", f"touch '{marker}'")
    assert not marker.exists()
    capture_source(repo, revision, tmp_path / "out", exclusions=policy("docs/archive/specs"))
    assert not marker.exists()
    assert "filter" not in (tmp_path / "out/baseline/.git/config").read_text()
    git(repo, "hash-object", "--path=AGENTS.md", "AGENTS.md")
    assert marker.exists(), "positive control must execute the authored filter"
    git(repo, "config", "--unset", "filter.danger.clean")

    # Unsupported links are not followed, and an unsuccessful capture is unpublished.
    (repo / "outside-link").symlink_to(tmp_path / "outside")
    git(repo, "add", "outside-link")
    git(
        repo,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.com",
        "commit",
        "-qm",
        "link",
    )
    revision = git(repo, "rev-parse", "HEAD").stdout.strip()
    with pytest.raises(SkillEvalError, match="symlink"):
        capture_source(repo, revision, tmp_path / "failed")
    assert not (tmp_path / "failed").exists()
    assert not list(tmp_path.glob(".source-capture-*"))
