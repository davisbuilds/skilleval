"""Bounded, copied evidence. Digests detect changes; they are not signatures.

Source traversal uses no-follow directory descriptors. Captures are read-only by
convention, not tamper-proof against their owner. Retain the returned digest
outside the bundle to detect replacement of both content and manifest.
"""

import ctypes
import hashlib
import json
import math
import os
import re
import shutil
import stat
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Never

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from skilleval.errors import SkillEvalError

MAX_FILES = 10_000
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024


class _Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    executable: bool

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        parts = PurePosixPath(value).parts
        if (
            not value
            or "\\" in value
            or "\x00" in value
            or value.startswith("/")
            or any(p in (".", "..") for p in value.split("/"))
            or "/".join(parts) != value
            or value == "manifest.json"
        ):
            raise ValueError("unsafe manifest path")
        return value


class _Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1]
    kind: Literal["snapshot", "historical_review_import"]
    meta: dict[str, Any]
    files: list[_Entry]


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
    ).encode()


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SkillEvalError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> Never:
    raise ValueError("nonstandard JSON constant")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite JSON number")
    return number


def _parse_json(data: bytes) -> Any:
    try:
        return json.loads(
            data,
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=_reject_json_constant,
            parse_float=_finite_float,
        )
    except (ValueError, UnicodeError):
        raise SkillEvalError("invalid JSON evidence") from None


@contextmanager
def _directory(path: Path) -> Iterator[int]:
    """Open each component without following a link, including root ancestors."""
    absolute = Path(os.path.abspath(path))
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in absolute.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def _child_directory(parent: int, name: str) -> Iterator[int]:
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _fingerprint(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mode,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _read_file(parent: int, name: str, limit: int = MAX_FILE_BYTES) -> tuple[bytes, bool]:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode):
            raise SkillEvalError("evidence must contain regular files only")
        if initial.st_size > limit:
            raise SkillEvalError("file size limit exceeded")
        chunks: list[bytes] = []
        size = 0
        while chunk := os.read(descriptor, min(CHUNK_BYTES, limit + 1 - size)):
            size += len(chunk)
            if size > limit:
                raise SkillEvalError("file size limit exceeded")
            chunks.append(chunk)
        if size != initial.st_size or _fingerprint(initial) != _fingerprint(os.fstat(descriptor)):
            raise SkillEvalError("source changed while capturing evidence")
        return b"".join(chunks), bool(initial.st_mode & 0o111)
    finally:
        os.close(descriptor)


def _walk(parent: int, prefix: str = "") -> Iterator[tuple[str, bytes, bool]]:
    for name in sorted(os.listdir(parent)):
        relative = f"{prefix}/{name}" if prefix else name
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            with _child_directory(parent, name) as child:
                yield from _walk(child, relative)
        elif stat.S_ISREG(info.st_mode):
            data, executable = _read_file(parent, name)
            yield relative, data, executable
        else:
            raise SkillEvalError("symlinks and non-regular evidence are forbidden")


class _Writer:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.entries: list[_Entry] = []
        self.total = 0

    def add(self, name: str, data: bytes, executable: bool = False) -> None:
        entry = _Entry(
            path=name,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            executable=executable,
        )
        if len(self.entries) >= MAX_FILES or self.total + len(data) > MAX_TOTAL_BYTES:
            raise SkillEvalError("evidence file-count or total-byte limit exceeded")
        if len(data) > MAX_FILE_BYTES:
            raise SkillEvalError("file size limit exceeded")
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            output.write(data)
        target.chmod(0o555 if executable else 0o444)
        self.entries.append(entry)
        self.total += len(data)


def _paths(source: Path, destination: Path) -> tuple[Path, Path]:
    source = Path(os.path.abspath(source))
    destination = Path(os.path.abspath(destination))
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise SkillEvalError("source and destination must not be nested")
    if os.path.lexists(destination):
        raise SkillEvalError("destination already exists")
    # Require an existing explicit parent; do not create unrelated directories.
    with _directory(destination.parent):
        pass
    return source, destination


def _publish(temporary: Path, destination: Path) -> None:
    """Atomic publication which never replaces even an empty existing directory."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(os.fsencode(temporary), os.fsencode(destination), 4)  # RENAME_EXCL
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        rename = libc.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(-100, os.fsencode(temporary), -100, os.fsencode(destination), 1)
    else:
        raise SkillEvalError("atomic no-replace publication unsupported on this platform")
    if result:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _capture(
    source: Path,
    destination: Path,
    kind: Literal["snapshot", "historical_review_import"],
    meta: dict[str, Any],
    populate: Callable[[int, _Writer], None],
) -> dict[str, Any]:
    temporary: Path | None = None
    try:
        source, destination = _paths(source, destination)
        with _directory(source) as source_fd:
            temporary = Path(tempfile.mkdtemp(prefix=".skilleval-", dir=destination.parent))
            writer = _Writer(temporary)
            populate(source_fd, writer)
            manifest = _Manifest(
                schema_version=1,
                kind=kind,
                meta=meta,
                files=sorted(writer.entries, key=lambda e: e.path),
            )
            raw = _json_bytes(manifest.model_dump())
            if len(raw) > MAX_MANIFEST_BYTES:
                raise SkillEvalError("manifest size limit exceeded")
            (temporary / "manifest.json").write_bytes(raw)
            (temporary / "manifest.json").chmod(0o444)
            digest = hashlib.sha256(raw).hexdigest()
            verify(temporary, digest)
            _publish(temporary, destination)
            temporary = None
            return {
                "path": str(destination),
                "manifest_sha256": digest,
                "file_count": len(writer.entries),
                "total_bytes": writer.total,
            }
    except ValidationError:
        raise SkillEvalError("cannot capture evidence: schema validation failed") from None
    except OSError:
        raise SkillEvalError("cannot capture evidence: filesystem operation failed") from None
    except RecursionError:
        raise SkillEvalError("cannot capture evidence: nesting limit exceeded") from None
    finally:
        if temporary is not None:
            # Only remove the private temporary directory allocated by this call.
            try:
                shutil.rmtree(temporary)
            except OSError:
                raise SkillEvalError("cannot clean up temporary evidence") from None


def snapshot(source: Path, destination: Path) -> dict[str, Any]:
    """Copy an explicit tree and return a digest receipt; never execute it."""

    def populate(source_fd: int, writer: _Writer) -> None:
        for name, data, executable in _walk(source_fd):
            writer.add(name, data, executable)

    return _capture(source, destination, "snapshot", {}, populate)


def verify(bundle: Path, expected_digest: str | None = None) -> dict[str, Any]:
    """Verify exact regular-file inventory, bytes and executable flags."""
    try:
        with _directory(bundle) as root:
            raw, _ = _read_file(root, "manifest.json", MAX_MANIFEST_BYTES)
            digest = hashlib.sha256(raw).hexdigest()
            if expected_digest is not None and expected_digest != digest:
                raise SkillEvalError("manifest digest mismatch")
            manifest = _Manifest.model_validate(_parse_json(raw))
            if len(manifest.files) > MAX_FILES:
                raise SkillEvalError("evidence file-count limit exceeded")
            expected = {entry.path: entry for entry in manifest.files}
            if len(expected) != len(manifest.files):
                raise SkillEvalError("duplicate manifest path")
            observed: set[str] = set()
            total = 0
            for name, data, executable in _walk(root):
                if name == "manifest.json":
                    if data != raw:
                        raise SkillEvalError("manifest changed during verification")
                    continue
                total += len(data)
                if len(observed) >= MAX_FILES or total > MAX_TOTAL_BYTES:
                    raise SkillEvalError("evidence inventory limit exceeded")
                entry = expected.get(name)
                if (
                    entry is None
                    or len(data) != entry.size
                    or executable != entry.executable
                    or hashlib.sha256(data).hexdigest() != entry.sha256
                ):
                    raise SkillEvalError("evidence content or inventory mismatch")
                observed.add(name)
            if observed != set(expected):
                raise SkillEvalError("evidence files missing")
            return manifest.model_dump()
    except ValidationError:
        raise SkillEvalError("cannot verify evidence: schema validation failed") from None
    except OSError:
        raise SkillEvalError("cannot verify evidence: filesystem operation failed") from None
    except RecursionError:
        raise SkillEvalError("cannot verify evidence: nesting limit exceeded") from None


def import_review(source_root: Path, case_id: str, destination: Path) -> dict[str, Any]:
    """Import one exposed historical case; preserve unverified source lineage."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", case_id):
        raise SkillEvalError("case_id must be a single safe identifier")

    def populate(source_fd: int, writer: _Writer) -> None:
        original, _ = _read_file(source_fd, "reports.json")
        reports = _parse_json(original)
        if not isinstance(reports, list) or any(not isinstance(r, dict) for r in reports):
            raise SkillEvalError("reports.json must be a list of report objects")
        selected = [report for report in reports if report.get("case_id") == case_id]
        if not selected:
            raise SkillEvalError("no reports found for selected case")
        with (
            _child_directory(source_fd, "cases") as cases,
            _child_directory(cases, case_id) as case,
        ):
            for required in ("instruction.md", "oracle.md"):
                _read_file(case, required)
            with _child_directory(case, "source"):
                pass
            for name, data, executable in _walk(case):
                writer.add(f"case/{name}", data, executable)
        writer.add("reports.json", _json_bytes(selected))
        writer.add("provenance/source-reports.json", original)

    return _capture(
        source_root,
        destination,
        "historical_review_import",
        {
            "case_id": case_id,
            "population": "development",
            "independent_blind": False,
            "historical_import": True,
            "prior_chain_verified": False,
            "source_report_document": "provenance/source-reports.json",
        },
        populate,
    )
