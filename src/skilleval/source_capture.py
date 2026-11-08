"""Export pinned Git source without importing its configuration or history.

The Git export/materialization pattern follows the existing completed-sessions
preparer. Selection provenance stays outside the solver's evidence snapshot.
This is source preparation, not task or environment qualification.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from skilleval import evidence
from skilleval.errors import SkillEvalError
from skilleval.treatments import check_task_baseline


def _path(value: str) -> str:
    if (
        not re.fullmatch(r"[A-Za-z0-9_./@+-]+", value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(part.casefold() in {".git", ".codex"} for part in value.split("/"))
    ):
        raise ValueError("expected a supported repository-relative path without globs")
    return value


class SourceExclusion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str
    reason: str = Field(min_length=1, max_length=2000)

    _validate_path = field_validator("path")(_path)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("exclusion reason must be nonempty")
        return value


class SourceExclusions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    exclude: list[SourceExclusion] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def distinct_roots(self) -> SourceExclusions:
        paths = sorted(row.path.casefold() for row in self.exclude)
        for index, path in enumerate(paths):
            if any(path == other or path.startswith(other + "/") for other in paths[:index]):
                raise ValueError("exclusion paths must not overlap or repeat")
        return self


def _git(root: Path, *args: str, data: bytes = b"", missing: bool = False) -> bytes | None:
    binary = shutil.which("git", path=os.defpath)
    if binary is None:
        raise SkillEvalError("Git is required for source capture")
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
        "GIT_AUTHOR_NAME": "Project maintainer",
        "GIT_COMMITTER_NAME": "Project maintainer",
        "GIT_AUTHOR_EMAIL": "maintainer@example.com",
        "GIT_COMMITTER_EMAIL": "maintainer@example.com",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+0000",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+0000",
        "LC_ALL": "C",
    }
    try:
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
    except (OSError, subprocess.TimeoutExpired):
        raise SkillEvalError("source capture Git operation failed") from None
    if result.returncode:
        if missing and result.returncode in {1, 128}:
            return None
        raise SkillEvalError("source capture Git operation failed")
    return result.stdout


def _required_git(root: Path, *args: str, data: bytes = b"") -> bytes:
    result = _git(root, *args, data=data)
    assert result is not None
    return result


def _tree(repo: Path, revision: str) -> dict[str, dict[str, Any]]:
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise SkillEvalError("source revision must be a full SHA-1 commit ID")
    if _required_git(repo, "cat-file", "-t", revision) != b"commit\n":
        raise SkillEvalError("source revision must identify a commit")
    raw = _required_git(repo, "ls-tree", "-rlz", "--full-tree", revision)
    entries: dict[str, dict[str, Any]] = {}
    spellings: dict[str, str] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            header, name = record.split(b"\t", 1)
            mode, kind, oid, size = header.decode("ascii").split()
            path = _path(name.decode("ascii"))
            if kind != "blob" or mode not in {"100644", "100755", "120000"}:
                raise ValueError("unsupported source entry")
            length = int(size)
        except (UnicodeError, ValueError):
            raise SkillEvalError("unsupported pinned source entry") from None
        parts = path.split("/")
        for end in range(1, len(parts) + 1):
            prefix = "/".join(parts[:end])
            previous = spellings.setdefault(prefix.casefold(), prefix)
            if previous != prefix:
                raise SkillEvalError("case-aliased source paths are unsupported")
        entries[path] = {"path": path, "mode": mode, "git_blob": oid, "size": length}
        if len(entries) > evidence.MAX_FILES:
            raise SkillEvalError("source inventory file-count limit exceeded")
    if not entries:
        raise SkillEvalError("source commit must contain files")
    return entries


def _export(repo: Path, entries: dict[str, dict[str, Any]], target: Path) -> list[dict[str, str]]:
    target.mkdir()
    links: list[dict[str, str]] = []
    total = 0
    for name, entry in entries.items():
        if entry["mode"] == "120000":
            if (
                Path(name).name != "CLAUDE.md"
                or entry["size"] != len(b"AGENTS.md")
                or _required_git(repo, "cat-file", "blob", entry["git_blob"]) != b"AGENTS.md"
            ):
                raise SkillEvalError("unsupported source symlink")
            link_target = (Path(name).parent / "AGENTS.md").as_posix()
            if link_target not in entries or entries[link_target]["mode"] != "100644":
                raise SkillEvalError("instruction link target must be retained")
            entry = entries[link_target]
            links.append({"path": name, "target": link_target, "kind": "materialized"})
        total += entry["size"]
        if entry["size"] > evidence.MAX_FILE_BYTES or total > evidence.MAX_TOTAL_BYTES:
            raise SkillEvalError("source capture byte limit exceeded")
        raw = _required_git(repo, "cat-file", "blob", entry["git_blob"])
        if len(raw) != entry["size"]:
            raise SkillEvalError("source blob size differs from pinned inventory")
        file = target / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(raw)
        file.chmod(0o755 if entry["mode"] == "100755" else 0o644)
    return links


def _select(
    entries: dict[str, dict[str, Any]], policy: SourceExclusions
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    retained = dict(entries)
    removed = []
    for rule in policy.exclude:
        matches = [
            name for name in entries if name == rule.path or name.startswith(rule.path + "/")
        ]
        if not matches:
            raise SkillEvalError("source exclusion did not match any committed files")
        if any(Path(name).name.casefold() in {"agents.md", "claude.md"} for name in matches):
            raise SkillEvalError("source exclusions must preserve ordinary agent instructions")
        for name in matches:
            removed.append({**retained.pop(name), "selector": rule.path, "reason": rule.reason})
    if not retained:
        raise SkillEvalError("source exclusions must retain a nonempty baseline")
    retained_blobs = {entry["git_blob"] for entry in retained.values()}
    if any(row["size"] and row["git_blob"] in retained_blobs for row in removed):
        raise SkillEvalError("excluded nonempty content also exists in retained files")
    return retained, sorted(removed, key=lambda row: row["path"])


def _object_controls(
    repo: Path,
    exported: Path,
    retained: dict[str, dict[str, Any]],
    removed: list[dict[str, Any]],
) -> dict[str, Any]:
    # A successful known-present read establishes that the missing-object check
    # can see this exact generated Git store. Batch output distinguishes missing
    # objects from a command failure instead of accepting arbitrary exit errors.
    positive = next(row["git_blob"] for row in retained.values() if row["mode"] != "120000")
    retained_blobs = {row["git_blob"] for row in retained.values() if row["mode"] != "120000"}
    absent = sorted({row["git_blob"] for row in removed} - retained_blobs)
    shared_empty = sorted({row["git_blob"] for row in removed if row["size"] == 0} & retained_blobs)
    oids = [positive, *absent]
    request = ("\n".join(oids) + "\n").encode("ascii")
    upstream = _required_git(
        repo, "cat-file", "--batch-check=%(objectname) %(objecttype)", data=request
    )
    if upstream.decode().splitlines() != [f"{oid} blob" for oid in oids]:
        raise SkillEvalError("source object positive control failed")
    captured = _required_git(
        exported, "cat-file", "--batch-check=%(objectname) %(objecttype)", data=request
    )
    if captured.decode().splitlines() != [
        f"{positive} blob",
        *[f"{oid} missing" for oid in absent],
    ]:
        raise SkillEvalError("excluded source object remains accessible")
    return {
        "retained_positive_blob": positive,
        "absent_excluded_blobs": absent,
        "shared_empty_blobs": shared_empty,
    }


def _seed_git(source: Path) -> dict[str, Any]:
    files = check_task_baseline(source)["files"]
    _required_git(
        source, "init", "--quiet", "--template=", "--object-format=sha1", "--initial-branch=main"
    )
    rows = []
    for row in files:
        oid = (
            _required_git(
                source,
                "hash-object",
                "-w",
                "--no-filters",
                "--stdin",
                data=(source / row["path"]).read_bytes(),
            )
            .decode()
            .strip()
        )
        mode = "100755" if row["executable"] else "100644"
        rows.append(f"{mode} {oid}\t{row['path']}\0".encode("ascii"))
    _required_git(source, "update-index", "-z", "--index-info", data=b"".join(rows))
    tree = _required_git(source, "write-tree").decode().strip()
    commit = _required_git(source, "commit-tree", tree, data=b"Project snapshot\n").decode().strip()
    _required_git(source, "update-ref", "refs/heads/main", commit)
    if _required_git(source, "status", "--porcelain"):
        raise SkillEvalError("synthetic source baseline is not clean")
    _required_git(source, "fsck", "--no-reflogs", "--full")
    return {"commit": commit, "tree": tree, "branch": "main", "indexed_files": len(rows)}


def capture_source(
    source: Path,
    revision: str,
    destination: Path,
    *,
    exclusions: SourceExclusions | None = None,
) -> dict[str, Any]:
    """Capture committed files and a fresh Git baseline; never execute imported code."""
    policy = exclusions or SourceExclusions()
    source, destination = evidence._paths(source, destination)
    entries = _tree(source, revision)
    retained, removed = _select(entries, policy)
    temporary = Path(tempfile.mkdtemp(prefix=".source-capture-", dir=destination.parent))
    try:
        exported = temporary / "export"
        links = _export(source, retained, exported)
        git_view = _seed_git(exported)
        controls = _object_controls(source, exported, retained, removed)
        baseline = evidence.snapshot(exported, temporary / "baseline")
        shutil.rmtree(exported)
        provenance = {
            "schema_version": 1,
            "kind": "source-capture",
            "source_revision": revision,
            "source_file_count": len(entries),
            "exclusions": policy.model_dump(),
            "excluded_files": removed,
            "materialized_links": links,
            "git": git_view,
            "object_controls": controls,
            "baseline_sha256": baseline["manifest_sha256"],
        }
        raw = evidence._json_bytes(provenance)
        (temporary / "provenance.json").write_bytes(raw)
        evidence._publish(temporary, destination)
        return {
            "path": str(destination),
            "source_revision": revision,
            "provenance_sha256": hashlib.sha256(raw).hexdigest(),
            "baseline": {**baseline, "path": str(destination / "baseline")},
            "excluded_file_count": len(removed),
        }
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
