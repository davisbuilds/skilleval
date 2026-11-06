"""Operator-owned storage outside disposable source checkouts."""

import os
import re
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

from skilleval.errors import SkillEvalError
from skilleval.evidence import _directory, _json_bytes, _parse_json, _read_file

GROUPS = ("experiments", "captures", "images", "tools", "audits")
MARKER = ".skilleval-store.json"
IDENTITY = {"kind": "skilleval-data-store", "schema_version": 1}


def data_root(root: Path | None = None) -> Path:
    """Select a durable root independently of the source checkout."""
    if root is not None:
        selected = root
    elif "SKILLEVAL_DATA_HOME" in os.environ:
        selected = Path(os.environ["SKILLEVAL_DATA_HOME"])
    elif sys.platform == "darwin":
        selected = Path.home() / "Library" / "Application Support" / "skilleval"
    else:
        selected = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
        selected /= "skilleval"
    if not selected.is_absolute() or ".." in selected.parts or selected == Path("/"):
        raise SkillEvalError("storage root must be an absolute, non-root path without '..'")
    return selected


def storage_path(
    group: str, name: str | None = None, *, root: Path | None = None
) -> dict[str, Any]:
    """Return a locator without reserving a producer's output directory."""
    if group not in GROUPS:
        raise SkillEvalError("unknown storage group")
    if name is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name):
        raise SkillEvalError("storage name must be one safe path component, at most 128 characters")
    selected = data_root(root)
    path = selected / group
    if name is not None:
        path /= name
    return {"status": "located", "root": str(selected), "path": str(path)}


def _create_parents(root: Path) -> None:
    """Create private directories without traversing a symlink ancestor."""
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in root.parts[1:]:
            with suppress(FileExistsError):
                os.mkdir(part, mode=0o700, dir_fd=descriptor)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)


def initialize(root: Path | None = None) -> dict[str, Any]:
    """Initialize an empty private store; never adopt an unrelated directory."""
    selected = data_root(root)
    if any((ancestor / ".git").exists() for ancestor in (selected, *selected.parents)):
        raise SkillEvalError("durable storage must be outside a Git checkout")
    _create_parents(selected)
    with _directory(selected) as directory:
        names = os.listdir(directory)
        if MARKER not in names:
            if names:
                raise SkillEvalError("refusing to initialize a nonempty unrecognized storage root")
            os.fchmod(directory, 0o700)
            descriptor = os.open(
                MARKER,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(_json_bytes(IDENTITY))
                stream.flush()
                os.fsync(stream.fileno())
        elif _parse_json(_read_file(directory, MARKER, 1024)[0]) != IDENTITY:
            raise SkillEvalError("unsupported storage identity")
        if os.fstat(directory).st_mode & 0o077:
            raise SkillEvalError("storage root must be private (mode 0700)")
        for name in GROUPS:
            with suppress(FileExistsError):
                os.mkdir(name, mode=0o700, dir_fd=directory)
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory,
            )
            try:
                if os.fstat(descriptor).st_mode & 0o077:
                    raise SkillEvalError("storage group must be private (mode 0700)")
            finally:
                os.close(descriptor)
        os.fsync(directory)
    return {
        "status": "ready",
        **IDENTITY,
        "root": str(selected),
        "paths": {name: str(selected / name) for name in GROUPS},
    }
