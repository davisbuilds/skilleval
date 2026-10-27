"""Reconstruct a local review parent from pinned source bytes and a text patch.

This provisional exporter supports the captured schema-1 review format only.
Git plumbing reads data without checking out or executing imported code. Synthetic
commits preserve the reconstructed trees, never original commit identities.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from skilleval import evidence
from skilleval.errors import SkillEvalError

_MODE = r"100(?:644|755)"
_INDEX = re.compile(rf"index ([0-9a-f]{{40}})\.\.([0-9a-f]{{40}})(?: ({_MODE}))?")
_ZERO = "0" * 40


def _safe_path(value: str) -> str:
    # Deliberately restrict this initial exporter instead of guessing Git's
    # quoting, platform aliases, or arbitrary filename encodings.
    if (
        not re.fullmatch(r"[A-Za-z0-9_./@+-]+", value)
        or value.startswith("/")
        or any(
            part in ("", ".", "..") or part.lower().rstrip(". ") == ".git"
            for part in value.split("/")
        )
    ):
        raise SkillEvalError("unsupported or unsafe review source path")
    return value


def _git(root: Path, *argv: str, data: bytes = b"") -> bytes:
    binary = shutil.which("git", path=os.defpath)
    if binary is None:
        raise SkillEvalError("native Git is required for review reconstruction")
    env = {
        "PATH": os.defpath,
        "HOME": str(root / ".git"),
        "XDG_CONFIG_HOME": str(root / ".git"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_ALLOW_PROTOCOL": "",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_AUTHOR_NAME": "Captured review",
        "GIT_AUTHOR_EMAIL": "capture@example.invalid",
        "GIT_COMMITTER_NAME": "Captured review",
        "GIT_COMMITTER_EMAIL": "capture@example.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+0000",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+0000",
        "LC_ALL": "C",
    }
    try:
        completed = subprocess.run(
            [binary, "-c", f"core.hooksPath={os.devnull}", "-C", str(root), *argv],
            input=data,
            capture_output=True,
            env=env,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SkillEvalError("native Git reconstruction could not complete") from None
    if completed.returncode:
        # Git diagnostics may contain private paths or patch contents.
        raise SkillEvalError("native Git rejected review reconstruction")
    return completed.stdout


def _patch_sections(patch: bytes) -> list[dict[str, str]]:
    try:
        lines = patch.decode("utf-8").splitlines()
    except UnicodeError:
        raise SkillEvalError("only UTF-8 text review patches are supported") from None
    sections: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    in_hunk = False
    for line in lines:
        if line.startswith("diff --git "):
            match = re.fullmatch(r"diff --git a/(\S+) b/(\S+)", line)
            if match is None or match[1] != match[2]:
                raise SkillEvalError("renamed or quoted patch paths are unsupported")
            current = {"path": _safe_path(match[1])}
            sections.append(current)
            in_hunk = False
        elif current is None:
            raise SkillEvalError("review patch must begin with a Git diff header")
        elif line.startswith("@@ "):
            in_hunk = True
            current["hunk"] = "yes"
        elif in_hunk:
            if not line.startswith((" ", "+", "-", "\\")):
                raise SkillEvalError("unsupported review patch hunk")
        else:
            key: str
            value: str
            if match := _INDEX.fullmatch(line):
                if "old_blob" in current:
                    raise SkillEvalError("duplicate review patch index header")
                current["old_blob"] = match[1]
                current["new_blob"] = match[2]
                if match[3]:
                    current["mode"] = match[3]
                continue
            if match := re.fullmatch(
                rf"(old mode|new mode|new file mode|deleted file mode) ({_MODE})", line
            ):
                key, value = match[1], match[2]
            elif line.startswith(("--- ", "+++ ")):
                key, value = line[:3], line[4:]
                allowed = {"/dev/null", f"{'a' if key == '---' else 'b'}/{current['path']}"}
                if value not in allowed:
                    raise SkillEvalError("patch content path does not match its Git header")
            else:
                raise SkillEvalError("unsupported review patch header or file mode")
            if key in current:
                raise SkillEvalError("duplicate review patch header")
            current[key] = value
    if not sections or len({s["path"] for s in sections}) != len(sections):
        raise SkillEvalError("empty or duplicate review patch paths")
    for section in sections:
        if "hunk" in section:
            if not all(key in section for key in ("old_blob", "new_blob", "---", "+++")):
                raise SkillEvalError("text patch requires full blob IDs and content paths")
        elif set(section) != {"path", "old mode", "new mode"}:
            raise SkillEvalError("unsupported non-text review patch")
    return sections


def _check_index(root: Path, sections: list[dict[str, str]], side: str) -> None:
    entries: dict[str, tuple[str, str]] = {}
    for record in _git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        meta, name = record.decode("ascii").split("\t")
        mode, oid, stage = meta.split()
        if stage != "0" or mode not in ("100644", "100755"):
            raise SkillEvalError("unsupported reconstructed index entry")
        entries[name] = (mode, oid)
    for section in sections:
        entry = entries.get(section["path"])
        expected_oid = section.get(f"{side}_blob")
        if expected_oid == _ZERO:
            if entry is not None:
                raise SkillEvalError("patch addition or deletion does not match source")
            continue
        expected_mode = section.get(f"{side} mode", section.get("mode"))
        if side == "new":
            expected_mode = section.get("new file mode", expected_mode)
        else:
            expected_mode = section.get("deleted file mode", expected_mode)
        if (
            entry is None
            or (expected_oid is not None and entry[1] != expected_oid)
            or (expected_mode and entry[0] != expected_mode)
        ):
            raise SkillEvalError("patch blob identity or file mode does not match source")


def _reconstruct(root: Path, files: list[tuple[str, bytes, bool]], patch: bytes) -> dict[str, Any]:
    sections = _patch_sections(patch)
    _git(root, "init", "--quiet", "--template=", "--initial-branch=candidate")
    # Persist protections for subsequent ordinary native Git inspection too.
    for key, value in (
        ("core.hooksPath", os.devnull),
        ("core.attributesFile", os.devnull),
        ("core.autocrlf", "false"),
        ("core.filemode", "true"),
        ("core.ignorecase", "false"),
        ("core.protectHFS", "true"),
        ("core.protectNTFS", "true"),
        ("commit.gpgsign", "false"),
    ):
        _git(root, "config", "--local", key, value)
    index = bytearray()
    for name, data, executable in files:
        # Avoid add/checkout: attributes, filters and ignore rules must not alter
        # captured bytes or omit ignored candidate files.
        oid = _git(root, "hash-object", "-w", "--no-filters", "--stdin", data=data).strip()
        index.extend((b"100755" if executable else b"100644") + b" " + oid + b"\t")
        index.extend(name.encode("ascii") + b"\0")
    _git(root, "update-index", "-z", "--index-info", data=bytes(index))
    _check_index(root, sections, "new")
    candidate_tree = _git(root, "write-tree").strip()
    _git(root, "apply", "--cached", "--reverse", "--whitespace=nowarn", "-", data=patch)
    _check_index(root, sections, "old")
    base_tree = _git(root, "write-tree").strip()
    _git(root, "apply", "--cached", "--whitespace=nowarn", "-", data=patch)
    if _git(root, "write-tree").strip() != candidate_tree or base_tree == candidate_tree:
        raise SkillEvalError("review patch does not reconstruct a distinct round-trip tree")
    base = _git(
        root, "commit-tree", base_tree.decode(), data=b"Reconstructed review base\n"
    ).strip()
    candidate = _git(
        root,
        "commit-tree",
        candidate_tree.decode(),
        "-p",
        base.decode(),
        data=b"Captured review candidate\n",
    ).strip()
    _git(root, "update-ref", "refs/heads/base", base.decode())
    _git(root, "update-ref", "refs/heads/candidate", candidate.decode())
    _git(root, "update-ref", "refs/heads/review-base", base.decode())
    _git(root, "update-ref", "refs/heads/review-candidate", candidate.decode())
    return {
        "base_commit": base.decode(),
        "candidate_commit": candidate.decode(),
        "base_tree": base_tree.decode(),
        "candidate_tree": candidate_tree.decode(),
        "patch_sha256": hashlib.sha256(patch).hexdigest(),
        "original_history_available": False,
        "reconstructed_base": True,
    }


def prepare_review(bundle: Path, expected_digest: str, destination: Path) -> dict[str, Any]:
    """Export exact source into a fresh writable tree with synthetic Git history.

    Only source files enter this directory. Oracle, reports and provenance are
    never copied. The caller must retain the expected manifest identity outside
    the captured bundle. Existing or nested destinations are refused.
    """
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise SkillEvalError("a pinned evidence manifest digest is required")
    temporary: Path | None = None
    try:
        bundle, destination = evidence._paths(bundle, destination)
        manifest = evidence.verify(bundle, expected_digest)
        if manifest["kind"] != "historical_review_import":
            raise SkillEvalError("review reconstruction requires a historical review import")
        expected = {
            entry["path"].removeprefix("case/source/"): entry
            for entry in manifest["files"]
            if entry["path"].startswith("case/source/")
        }
        files: list[tuple[str, bytes, bool]] = []
        aliases: set[str] = set()
        with (
            evidence._directory(bundle) as root,
            evidence._child_directory(root, "case") as case,
            evidence._child_directory(case, "source") as source,
        ):
            for name, data, executable in evidence._walk(source):
                _safe_path(name)
                if name.lower() in aliases:
                    raise SkillEvalError("case-aliased source paths are unsupported")
                aliases.add(name.lower())
                entry = expected.get(name)
                if (
                    entry is None
                    or entry["size"] != len(data)
                    or entry["executable"] != executable
                    or entry["sha256"] != hashlib.sha256(data).hexdigest()
                ):
                    raise SkillEvalError("review source changed before export")
                files.append((name, data, executable))
        if {name for name, _, _ in files} != set(expected):
            raise SkillEvalError("review source inventory changed before export")
        content = {name: data for name, data, _ in files}
        if ".openbench-review.json" not in content:
            raise SkillEvalError("captured review metadata is required")
        metadata = evidence._parse_json(content[".openbench-review.json"])
        if (
            not isinstance(metadata, dict)
            or type(metadata.get("schema")) is not int
            or metadata != {"schema": 1, "patch": "review.patch"}
            or "review.patch" not in content
        ):
            raise SkillEvalError("unsupported captured review metadata")
        temporary = Path(tempfile.mkdtemp(prefix=".skilleval-workspace-", dir=destination.parent))
        for name, data, executable in files:
            target = temporary / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o755 if executable else 0o644)
        receipt = _reconstruct(temporary, files, content["review.patch"])
        # Detect concurrent replacement of the pinned manifest before publication.
        evidence.verify(bundle, expected_digest)
        evidence._publish(temporary, destination)
        temporary = None
        return {"path": str(destination), "manifest_sha256": expected_digest, **receipt}
    except (OSError, UnicodeError):
        raise SkillEvalError(
            "cannot prepare review workspace: filesystem or encoding error"
        ) from None
    finally:
        if temporary is not None:
            shutil.rmtree(temporary)
