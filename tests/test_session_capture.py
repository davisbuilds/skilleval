"""Authored Git stores exercise capture fidelity without running imported source."""

import importlib.util
import json
from pathlib import Path

import pytest

from skilleval import evidence
from skilleval.errors import SkillEvalError

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/repair/completed-sessions/prepare.py"
SPEC = importlib.util.spec_from_file_location("session_capture_example", EXAMPLE)
assert SPEC is not None and SPEC.loader is not None
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)


def commit(repo: Path) -> str:
    files = sorted(
        path.relative_to(repo).as_posix()
        for path in repo.rglob("*")
        if ".git" not in path.relative_to(repo).parts and (path.is_file() or path.is_symlink())
    )
    capture.git(
        repo,
        "add",
        "--force",
        "--pathspec-from-file=-",
        "--pathspec-file-nul",
        data=b"\0".join(name.encode() for name in files) + b"\0",
    )
    tree_id = capture.git(repo, "write-tree").decode().strip()
    parent = repo / ".git/refs/heads/main"
    args = ["-p", parent.read_text().strip()] if parent.exists() else []
    oid = (
        capture.git(repo, "commit-tree", tree_id, *args, data=b"Authored source\n").decode().strip()
    )
    capture.git(repo, "update-ref", "refs/heads/main", oid)
    return oid


def authored_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "upstream"
    repo.mkdir()
    capture.git(repo, "init", "--quiet", "--template=", "--initial-branch=main")
    for name, content in {
        "AGENTS.md": b"Root ordinary guidance.\n",
        "frontend/AGENTS.md": b"Frontend ordinary guidance.\n",
        "docs/archive/context.md": b"Tracked archived context.\n",
        ".gitignore": b"**/archive/\n",
        "src/reader.ts": b"throw new Error('never execute imported source');\n",
    }.items():
        file = repo / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)
    (repo / "CLAUDE.md").symlink_to("AGENTS.md")
    (repo / "frontend/CLAUDE.md").symlink_to("AGENTS.md")
    (repo / "src/reader.ts").chmod(0o755)
    return repo


def test_materializes_only_instruction_links_to_their_own_targets(tmp_path):
    repo = authored_repo(tmp_path)
    revision = commit(repo)
    output = tmp_path / "export"
    links = capture.export(repo, capture.tree(repo, revision), output)
    assert links == ["CLAUDE.md", "frontend/CLAUDE.md"]
    assert (output / "CLAUDE.md").read_bytes() == b"Root ordinary guidance.\n"
    assert (output / "frontend/CLAUDE.md").read_bytes() == b"Frontend ordinary guidance.\n"
    assert not (output / "CLAUDE.md").is_symlink()
    assert (output / "src/reader.ts").stat().st_mode & 0o111
    (repo / "other-link").symlink_to("AGENTS.md")
    revision = commit(repo)
    with pytest.raises(SkillEvalError, match="source link"):
        capture.export(repo, capture.tree(repo, revision), tmp_path / "rejected")


def test_full_capture_preserves_context_and_excludes_future_git_objects(tmp_path, monkeypatch):
    repo = authored_repo(tmp_path)
    base_revision = commit(repo)
    baseline_count = len(capture.tree(repo, base_revision))
    (repo / "src/reader.ts").write_bytes(b"Reference repair bytes, never run.\n")
    (repo / "src/new-reader.ts").write_bytes(b"Distinct future blob.\n")
    reference_revision = commit(repo)
    skill_repo = tmp_path / "skill-upstream"
    skill_repo.mkdir()
    capture.git(skill_repo, "init", "--quiet", "--template=", "--initial-branch=main")
    skill = skill_repo / capture.SKILL_PATH
    (skill / "agents").mkdir(parents=True)
    (skill / "scripts").mkdir()
    skill_body = b"---\nname: diagnosing-bugs\ndescription: Authored fixture.\n---\nRead records.\n"
    (skill / "SKILL.md").write_bytes(skill_body)
    (skill / "agents/openai.yaml").write_bytes(b"interface: {}\n")
    (skill / "scripts/hitl-loop.template.sh").write_bytes(b"exit 77\n")
    (skill_repo / "LICENSE").write_bytes(b"Authored root notice.\n")
    skill_revision = commit(skill_repo)
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "rollout.jsonl").write_bytes(b'{"type":"session_meta"}\n')
    monkeypatch.setattr(capture, "BASELINE", base_revision)
    monkeypatch.setattr(capture, "REFERENCE", reference_revision)
    monkeypatch.setattr(capture, "SKILL_REVISION", skill_revision)
    monkeypatch.setattr(capture, "BASELINE_FILE_COUNT", baseline_count)
    output = tmp_path / "prepared"
    result = capture.prepare(repo, skill_repo, fixtures, output)
    provenance = json.loads((output / "provenance.json").read_bytes())
    baseline = output / "baseline-source"
    reference = output / "reference-source"
    assert capture.git(baseline, "status", "--porcelain") == b""
    assert capture.git(baseline, "rev-parse", "HEAD") == capture.git(reference, "rev-parse", "HEAD")
    indexed = capture.git(baseline, "ls-files").decode().splitlines()
    assert "docs/archive/context.md" in indexed
    assert "sample-history/rollout.jsonl" in indexed
    assert (baseline / "src/reader.ts").read_bytes().startswith(b"throw")
    assert (reference / "src/reader.ts").read_bytes().startswith(b"Reference")
    baseline_git = {
        path.relative_to(baseline / ".git").as_posix(): path.read_bytes()
        for path in (baseline / ".git").rglob("*")
        if path.is_file()
    }
    reference_git = {
        path.relative_to(reference / ".git").as_posix(): path.read_bytes()
        for path in (reference / ".git").rglob("*")
        if path.is_file()
    }
    assert baseline_git == reference_git
    assert capture.git(reference, "status", "--porcelain")
    for source in (baseline, reference):
        for oid in provenance["object_controls"]["excluded_reference_objects"]:
            assert capture.git(source, "cat-file", "-e", oid, missing=True) is None
        assert (
            capture.git(
                source, "cat-file", "-e", provenance["object_controls"]["positive_baseline_blob"]
            )
            == b""
        )
        assert (source / "sample-history/rollout.jsonl").read_bytes() == (
            fixtures / "rollout.jsonl"
        ).read_bytes()
    for receipt in result["captures"].values():
        evidence.verify(Path(receipt["path"]), receipt["manifest_sha256"])
    captured_skill = output / "skill-capture/skills/diagnosing-bugs"
    assert (captured_skill / "SKILL.md").read_bytes() == skill_body
    assert (captured_skill / ".source-notices/ROOT_LICENSE").read_bytes() == (
        skill_repo / "LICENSE"
    ).read_bytes()
    assert not (captured_skill / "scripts/hitl-loop.template.sh").stat().st_mode & 0o111
    assert not (baseline / "provenance.json").exists()
    with pytest.raises(SkillEvalError, match="fresh"):
        capture.prepare(repo, skill_repo, fixtures, output)
    injected = fixtures / "sub/.git/config"
    injected.parent.mkdir(parents=True)
    injected.write_bytes(b"Not an ordinary transcript fixture.\n")
    with pytest.raises(SkillEvalError, match="fixture path"):
        capture.prepare(repo, skill_repo, fixtures, tmp_path / "rejected-fixture")
    assert not (tmp_path / "rejected-fixture").exists()
