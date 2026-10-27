"""Synthetic source and real native Git; no imported task code is executed."""

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from skilleval import evidence, workspace
from skilleval.errors import SkillEvalError


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(
        ["/usr/bin/git", "-C", str(root), *args],
        env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"},
        stderr=subprocess.PIPE,
    )


def blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def captured(
    tmp_path: Path, patch: str | None = None, extra_files: dict[str, bytes] | None = None
) -> tuple[Path, str]:
    legacy = tmp_path / "legacy"
    case = legacy / "cases" / "synthetic"
    source = case / "source"
    source.mkdir(parents=True)
    files = {
        "changed.txt": b"after\n",
        "added.txt": b"new\n",
        "run.sh": b"#!/bin/sh\nexit 17\n",
        "AGENTS.md": b"Use the captured tests.\n",
        "tests/check.py": b"raise RuntimeError('must never execute')\n",
        ".gitignore": b"ignored.txt\n",
        "ignored.txt": b"retain ignored bytes\r\n",
        ".gitattributes": b"* filter=forbidden\n",
        ".openbench-review.json": b'{"schema":1,"patch":"review.patch"}\n',
    }
    if patch is None:
        patch = (
            "diff --git a/changed.txt b/changed.txt\n"
            f"index {blob(b'before\n')}..{blob(b'after\n')} 100644\n"
            "--- a/changed.txt\n+++ b/changed.txt\n@@ -1 +1 @@\n-before\n+after\n"
            "diff --git a/added.txt b/added.txt\nnew file mode 100644\n"
            f"index {'0' * 40}..{blob(b'new\n')}\n"
            "--- /dev/null\n+++ b/added.txt\n@@ -0,0 +1 @@\n+new\n"
            "diff --git a/deleted.txt b/deleted.txt\ndeleted file mode 100644\n"
            f"index {blob(b'old\n')}..{'0' * 40}\n"
            "--- a/deleted.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n"
            "diff --git a/run.sh b/run.sh\nold mode 100644\nnew mode 100755\n"
        )
    files["review.patch"] = patch.encode()
    files.update(extra_files or {})
    for name, data in files.items():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (source / "run.sh").chmod(0o755)
    (case / "instruction.md").write_text("Review the patch")
    (case / "oracle.md").write_text("Hidden oracle sentinel")
    (legacy / "reports.json").write_text('[{"case_id":"synthetic","findings":[]}]')
    bundle = tmp_path / "bundle"
    receipt = evidence.import_review(legacy, "synthetic", bundle)
    return bundle, receipt["manifest_sha256"]


def test_reconstructs_meaningful_history_without_changing_candidate(tmp_path: Path) -> None:
    bundle, digest = captured(tmp_path)
    target = tmp_path / "work"
    receipt = workspace.prepare_review(bundle, digest, target)
    # The historical task instruction names these refs. Native callers must
    # see the same diff without rewriting ordinary task instructions.
    assert git(target, "diff", "--full-index", "review-base", "HEAD") == git(
        target, "diff", "--full-index", "base", "candidate"
    )
    assert git(target, "rev-parse", "review-candidate") == git(target, "rev-parse", "HEAD")
    assert git(target, "show", "base:changed.txt") == b"before\n"
    assert git(target, "show", "base:deleted.txt") == b"old\n"
    assert git(target, "show", "candidate:changed.txt") == b"after\n"
    assert git(target, "show", "candidate:added.txt") == b"new\n"
    assert git(target, "rev-parse", "candidate^").strip().decode() == receipt["base_commit"]
    assert git(target, "rev-parse", "HEAD").strip().decode() == receipt["candidate_commit"]
    assert git(target, "status", "--porcelain") == b""
    assert b"100644" in git(target, "ls-tree", "base", "run.sh")
    assert b"100755" in git(target, "ls-tree", "candidate", "run.sh")
    assert receipt["original_history_available"] is False
    assert receipt["reconstructed_base"] is True
    original = bundle / "case" / "source"
    expected = {
        str(p.relative_to(original)): p.read_bytes() for p in original.rglob("*") if p.is_file()
    }
    observed = {
        str(p.relative_to(target)): p.read_bytes()
        for p in target.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(target).parts
    }
    assert observed == expected
    for name in expected:
        assert (target / name).stat().st_mode & 0o200
    assert git(target, "show", "candidate:ignored.txt") == b"retain ignored bytes\r\n"
    assert not (target / "oracle.md").exists()
    assert not (target / "reports.json").exists()


@pytest.mark.parametrize(
    "patch",
    [
        "diff --git a/../outside b/../outside\nold mode 100644\nnew mode 100755\n",
        "diff --git a/.git/config b/.git/config\nold mode 100644\nnew mode 100755\n",
        "diff --git a/changed.txt b/changed.txt\nold mode 100644\nnew mode 120000\n",
        "diff --git a/changed.txt b/changed.txt\nold mode 100644\nnew mode 160000\n",
        "diff --git a/old.txt b/new.txt\nsimilarity index 100%\n"
        "rename from old.txt\nrename to new.txt\n",
        "diff --git a/changed.txt b/changed.txt\nGIT binary patch\nliteral 1\nA\n",
    ],
)
def test_unsupported_patch_refused_without_partial_workspace(tmp_path: Path, patch: str) -> None:
    bundle, digest = captured(tmp_path, patch)
    sentinel = tmp_path / "outside"
    sentinel.write_text("retain")
    with pytest.raises(SkillEvalError):
        workspace.prepare_review(bundle, digest, tmp_path / "work")
    assert sentinel.read_text() == "retain"
    assert not (tmp_path / "work").exists()
    assert not list(tmp_path.glob(".skilleval-workspace-*"))


def test_full_blob_identity_refuses_plausible_but_different_base(tmp_path: Path) -> None:
    # The hunk applies cleanly; a wrong full base identity must still fail.
    patch = (
        "diff --git a/changed.txt b/changed.txt\n"
        f"index {blob(b'other\n')}..{blob(b'after\n')} 100644\n"
        "--- a/changed.txt\n+++ b/changed.txt\n@@ -1 +1 @@\n-before\n+after\n"
    )
    bundle, digest = captured(tmp_path, patch)
    with pytest.raises(SkillEvalError, match="identity"):
        workspace.prepare_review(bundle, digest, tmp_path / "work")
    assert not (tmp_path / "work").exists()


@pytest.mark.parametrize("change", ["digest", "bytes", "mode", "symlink", "fifo", "git-dir"])
def test_changed_evidence_refused_before_export(tmp_path: Path, change: str) -> None:
    bundle, digest = captured(tmp_path)
    source = bundle / "case" / "source"
    target = source / "changed.txt"
    if change == "digest":
        digest = "0" * 64
    elif change == "bytes":
        target.chmod(0o644)
        target.write_text("different\n")
    elif change == "mode":
        target.chmod(0o755)
    else:
        source.chmod(0o755)
        if change == "git-dir":
            (source / ".git").mkdir()
            (source / ".git" / "config").write_text("untrusted")
        else:
            target.unlink()
            if change == "symlink":
                target.symlink_to(tmp_path / "outside")
            else:
                os.mkfifo(target)
    with pytest.raises(SkillEvalError):
        workspace.prepare_review(bundle, digest, tmp_path / "work")
    assert not (tmp_path / "work").exists()


def test_existing_nested_and_linked_destinations_refused(tmp_path: Path) -> None:
    bundle, digest = captured(tmp_path)
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "sentinel").write_text("retain")
    link = tmp_path / "linked"
    link.symlink_to(existing, target_is_directory=True)
    for target in (existing, link / "new", bundle / "new"):
        with pytest.raises(SkillEvalError):
            workspace.prepare_review(bundle, digest, target)
    assert (existing / "sentinel").read_text() == "retain"
    assert not (existing / "new").exists()


def test_host_git_config_hooks_and_filters_cannot_execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, digest = captured(tmp_path)
    sentinel = tmp_path / "executed"
    hook = tmp_path / "hooks"
    hook.mkdir()
    program = f"#!/bin/sh\ntouch '{sentinel}'\nexit 23\n"
    (hook / "post-checkout").write_text(program)
    (hook / "post-checkout").chmod(0o755)
    filter_program = tmp_path / "filter"
    filter_program.write_text(program)
    filter_program.chmod(0o755)
    global_config = tmp_path / "host-gitconfig"
    global_config.write_text(
        f"[core]\n\thooksPath = {hook}\n"
        f'[filter "forbidden"]\n\tclean = {filter_program}\n\trequired = true\n'
        f"[init]\n\ttemplateDir = {tmp_path / 'missing-template'}\n"
    )
    # Positive control: the same config and captured attributes invoke its filter
    # through ordinary native Git add, proving the marker detector is live.
    control = tmp_path / "control"
    control.mkdir()
    git(control, "init", "--quiet", "--template=")
    (control / ".gitattributes").write_text("* filter=forbidden\n")
    (control / "data").write_text("data\n")
    with pytest.raises(subprocess.CalledProcessError):
        subprocess.check_output(
            ["/usr/bin/git", "-C", str(control), "add", "data"],
            env={
                "PATH": "/usr/bin:/bin",
                "GIT_CONFIG_GLOBAL": str(global_config),
                "GIT_CONFIG_NOSYSTEM": "1",
            },
            stderr=subprocess.PIPE,
        )
    assert sentinel.exists()
    sentinel.unlink()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_DIR", str(control / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(control))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "filter.forbidden.clean")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(filter_program))
    workspace.prepare_review(bundle, digest, tmp_path / "work")
    assert not sentinel.exists()
    assert git(tmp_path / "work", "show", "candidate:changed.txt") == b"after\n"


def test_errors_do_not_disclose_private_patch_contents(tmp_path: Path) -> None:
    marker = "PRIVATE_PATCH_CONTENT_SENTINEL"
    bundle, digest = captured(tmp_path, marker)
    with pytest.raises(SkillEvalError) as caught:
        workspace.prepare_review(bundle, digest, tmp_path / "work")
    assert marker not in str(caught.value)


@pytest.mark.parametrize(
    "extra",
    [
        {".git/config": b"[include]\npath=/outside\n"},
        {".GiT/config": b"untrusted"},
        {".openbench-review.json": b'{"schema":true,"patch":"review.patch"}'},
    ],
)
def test_reserved_git_paths_and_unsupported_metadata_rejected(
    tmp_path: Path, extra: dict[str, bytes]
) -> None:
    bundle, digest = captured(tmp_path, extra_files=extra)
    # This is a valid pinned bundle: refusal must come from reconstruction rules.
    evidence.verify(bundle, digest)
    with pytest.raises(SkillEvalError):
        workspace.prepare_review(bundle, digest, tmp_path / "work")
    assert not (tmp_path / "work").exists()
