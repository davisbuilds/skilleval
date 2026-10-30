"""Bounded native context observations, shared by the host and disposable guest.

Only stdlib imports: this file is copied beside the production guest runner.
Inventories establish bytes/locations, not semantic application or model quality.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

MAX_FILES = 10_000
MAX_BYTES = 32 * 1024 * 1024
INSTRUCTIONS = {"AGENTS.md", "AGENTS.override.md"}


def identity(raw: bytes) -> dict[str, Any]:
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def inventory(root: Path) -> list[dict[str, Any]]:
    """Fail on unreadable, linked, special, changing or oversized context inputs."""
    if not os.path.lexists(root):
        return []
    if root.is_symlink() or not root.is_dir():
        raise ValueError("context root must be a real directory")
    rows: list[dict[str, Any]] = []
    total = 0

    def failed(error: OSError) -> None:
        raise error

    for current, directories, files in os.walk(root, onerror=failed, followlinks=False):
        for name in sorted([*directories, *files]):
            path = Path(current) / name
            before = path.lstat()
            if stat.S_ISDIR(before.st_mode):
                continue
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("context input contains a link or special file")
            if len(rows) >= MAX_FILES or total + before.st_size > MAX_BYTES:
                raise ValueError("context inventory exceeds limit")
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as f:
                raw = f.read(MAX_BYTES + 1)
                after = os.fstat(f.fileno())
            if len(raw) != before.st_size or any(
                getattr(before, k) != getattr(after, k)
                for k in ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
            ):
                raise ValueError("context input changed while reading")
            total += len(raw)
            rows.append({"path": path.relative_to(root).as_posix(), **identity(raw)})
    return sorted(rows, key=lambda row: row["path"])


def preflight(
    root: Path, work: Path, expected_home: list[dict[str, Any]], *, system: Path = Path("/")
) -> dict[str, Any]:
    """Inspect known Codex discovery roots before launching the native process.

    ``system`` supports disposable filesystem tests; production always uses /.
    Root inventories are observations from the trusted bootstrap, not attestation
    against code already executing with the same guest credentials.
    """
    home, codex_home = root / "home", root / "codex-home"
    if inventory(home) or inventory(codex_home) != expected_home:
        raise ValueError("unexpected native home context")
    forbidden = {system / "etc/codex", system / "etc/agents"}
    for parent in {*(work.parents), *(root.parents), system / "root", home}:
        forbidden.update(parent / name for name in (".codex", ".agents", ".claude"))
        forbidden.update(parent / name for name in INSTRUCTIONS)
    observed = []
    for path in sorted(forbidden):
        if os.path.lexists(path):
            raise ValueError("unexpected ambient context path: " + str(path))
        observed.append(str(path))
    instructions = []

    # Task source may be larger than the context budget. Read only instruction files.
    def failed(error: OSError) -> None:
        raise error

    for current, directories, files in os.walk(work, onerror=failed, followlinks=False):
        directories[:] = [name for name in directories if name != ".git"]
        for name in sorted(set(files) & INSTRUCTIONS):
            path = Path(current) / name
            if path.is_symlink() or path.stat().st_size > MAX_BYTES:
                raise ValueError("unsupported task instruction input")
            instructions.append(
                {"path": path.relative_to(work).as_posix(), **identity(path.read_bytes())}
            )
    return {
        "schema_version": 1,
        "status": "passed",
        "absent_paths": observed,
        "initial_codex_home": expected_home,
        "task_instructions": sorted(instructions, key=lambda row: row["path"]),
    }


def request_context(raw: bytes) -> dict[str, Any]:
    """Observe actual native message/tool content without retaining its prose."""
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("context request exceeds limit")
    body = json.loads(raw)
    if not isinstance(body, dict) or not isinstance(body.get("input"), list):
        raise ValueError("unsupported context request")
    messages = []
    catalogs = []
    tools = []
    if body.get("tools"):
        if not isinstance(body["tools"], list):
            raise ValueError("unsupported native tool catalog")
        tools.append(
            {"surface": "tools", **identity(json.dumps(body["tools"], sort_keys=True).encode())}
        )
    for index, item in enumerate(body["input"]):
        if not isinstance(item, dict):
            raise ValueError("unsupported context input item")
        if item.get("type") == "additional_tools":
            if not isinstance(item.get("tools"), list):
                raise ValueError("unsupported native tool catalog")
            # The record id is a fresh transport identifier, not a tool capability.
            # Original request bytes retain it; compare complete definitions here.
            tools.append(
                {
                    "surface": "additional_tools",
                    "role": item.get("role"),
                    **identity(json.dumps(item["tools"], sort_keys=True).encode()),
                }
            )
        if item.get("type", "message") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list) or not all(
            isinstance(part, dict)
            and part.get("type") in {"input_text", "output_text"}
            and isinstance(part.get("text"), str)
            for part in content
        ):
            raise ValueError("unsupported context message content")
        text = "\n".join(part["text"] for part in content)
        row = {"index": index, "role": item.get("role"), **identity(text.encode())}
        if item.get("role") == "user" and text.startswith("# AGENTS.md instructions for "):
            row["instruction_sources"] = re.findall(
                r"^# AGENTS.md instructions for (.+)$", text, re.M
            )
        messages.append(row)
        if item.get("role") == "developer" and text.startswith("<skills_instructions>"):
            roots = dict(re.findall(r"^- `(r\d+)` = `([^`]+)`$", text, re.M))
            skills = []
            for name, locator in re.findall(r"^- ([\w:-]+): .*?\(file: ([^)]+)\)$", text, re.M):
                prefix, separator, tail = locator.partition("/")
                resolved = roots.get(prefix, prefix) + (separator + tail if separator else "")
                skills.append({"name": name, "path": resolved})
            if "### Available skills" not in text or text.count("(file:") != len(skills):
                raise ValueError("unrecognized native skill catalog")
            catalogs.append({"roots": roots, "skills": skills, **identity(text.encode())})
    return {
        "schema_version": 1,
        "messages": messages,
        "skill_catalogs": catalogs,
        "tools": tools,
        "top_level_instructions": identity(str(body.get("instructions", "")).encode()),
    }


def check_request_context(
    observed: dict[str, Any], work: str, codex_home: str, skill: str | None
) -> None:
    for message in observed["messages"]:
        for source in message.get("instruction_sources", []):
            if not Path(source).is_relative_to(work) or ".." in Path(source).parts:
                raise ValueError("unexpected instruction source in native request")
    for catalog in observed["skill_catalogs"]:
        for item in catalog["skills"]:
            path = Path(item["path"])
            builtin = Path(codex_home) / "skills/.system"
            selected = Path(codex_home) / "skills" / skill if skill else None
            if ".." in path.parts or not (
                path.is_relative_to(builtin)
                or (selected is not None and path.is_relative_to(selected))
            ):
                raise ValueError("unexpected skill in native request")
            if skill and item["name"] == skill and path.is_relative_to(builtin):
                raise ValueError("selected skill collides with a harness builtin")
