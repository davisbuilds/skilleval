"""Shared frozen task transfer and trusted guest preparation; no model execution."""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path
from typing import Any

from skilleval import evidence
from skilleval.errors import SkillEvalError
from skilleval.native_process import NATIVE_PAX_PATH_BYTES as NATIVE_PAX_PATH_BYTES
from skilleval.native_process import _pax_path_size

# /run is a 64 MiB tmpfs in the qualified guest. Repository archives, native
# logs and output collection need guest disk capacity, outside the task tree.
GUEST_ROOT = "/var/tmp/native"
GUEST_TOOLING = "/var/tmp/skilleval-tooling.tar"
GUEST_WORK = "/workspace/task"

_PREPARE_TASK_PROGRAM = """
import os,pathlib,stat,sys,tarfile
archive,task,native=map(pathlib.Path,sys.argv[1:])
for target in (task,native):
    if any(parent.is_symlink() for parent in target.parents):
        raise ValueError('guest preparation ancestors must be real directories')
    if os.path.lexists(target):
        if not stat.S_ISDIR(target.lstat().st_mode) or any(target.iterdir()):
            raise ValueError('guest preparation requires an empty real directory')
    elif not target.parent.is_dir():
        raise ValueError('guest preparation requires an existing parent')
for target in (task,native):
    target.mkdir(mode=0o700,exist_ok=True)
    target.chmod(0o700)
with tarfile.open(archive) as source:
    for member in source:
        if not member.isfile() or not member.name.startswith(('task/','native/')):
            raise ValueError('unsupported guest preparation archive member')
        source.extract(member,task.parent if member.name.startswith('task/') else native.parent,
                       filter='data')
"""


def preparation_argv(archive: Path, task: Path, native: Path) -> list[str]:
    """Exact trusted guest bootstrap; arguments are paths, never shell text."""
    return ["python3", "-c", _PREPARE_TASK_PROGRAM, str(archive), str(task), str(native)]


def _snapshot_files(
    bundle: Path, expected: str
) -> tuple[dict[str, Any], list[tuple[str, bytes, bool]]]:
    manifest = evidence.verify(bundle, expected)
    if manifest["kind"] != "snapshot":
        raise SkillEvalError("native input must be a source-only generic snapshot")
    expected_files = {entry["path"]: entry for entry in manifest["files"]}
    files = []
    with evidence._directory(bundle) as root:
        for name, data, executable in evidence._walk(root):
            if name == "manifest.json":
                continue
            entry = expected_files.get(name)
            if (
                entry is None
                or entry["size"] != len(data)
                or entry["sha256"] != hashlib.sha256(data).hexdigest()
                or entry["executable"] != executable
            ):
                raise SkillEvalError("native source changed before copy")
            files.append((name, data, executable))
    if {name for name, _, _ in files} != set(expected_files):
        raise SkillEvalError("native source inventory changed before copy")
    return manifest, files


def _tar(path: Path, entries: list[tuple[str, bytes, bool]]) -> None:
    if any(_pax_path_size(name) > NATIVE_PAX_PATH_BYTES for name, _, _ in entries):
        raise SkillEvalError("task transfer path exceeds native PAX metadata limit")
    with tarfile.open(path, "x", format=tarfile.PAX_FORMAT) as target:
        for name, data, executable in entries:
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755 if executable else 0o644
            target.addfile(info, io.BytesIO(data))
    path.chmod(0o600)
