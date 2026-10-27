import os
from pathlib import Path

import pytest

from skilleval.errors import SkillEvalError
from skilleval.identity import file_digest, runtime_identity


def distribution(root: Path) -> Path:
    root.mkdir()
    for name in ("smolvm", "smolvm-bin", "storage-template.ext4", "overlay-template.ext4"):
        (root / name).write_bytes(b"initial")
    (root / "smolvm").chmod(0o755)
    (root / "lib").mkdir()
    (root / "lib/engine").write_bytes(b"engine")
    (root / "agent-rootfs").mkdir()
    return root / "smolvm"


def test_identity_covers_companion_library_templates_and_link_text(tmp_path: Path) -> None:
    binary = distribution(tmp_path / "runtime")
    secret = tmp_path / "outside"
    secret.write_text("do not read")
    link = binary.parent / "agent-rootfs/link"
    link.symlink_to(secret)
    initial = runtime_identity(str(binary))
    secret.write_text("outside changes are not followed")
    assert runtime_identity(str(binary)) == initial
    for relative in ("smolvm-bin", "lib/engine", "storage-template.ext4"):
        target = binary.parent / relative
        original = target.read_bytes()
        target.write_bytes(b"changed")
        assert runtime_identity(str(binary)) != initial
        target.write_bytes(original)
    link.unlink()
    link.symlink_to("another-guest-target")
    assert runtime_identity(str(binary)) != initial


def test_host_library_cannot_link_outside_distribution(tmp_path: Path) -> None:
    binary = distribution(tmp_path / "runtime")
    (tmp_path / "outside").write_text("external")
    (binary.parent / "lib/other").symlink_to(tmp_path / "outside")
    with pytest.raises(SkillEvalError, match="escapes"):
        runtime_identity(str(binary))


@pytest.mark.skipif(os.geteuid() == 0, reason="root can traverse unreadable directories")
@pytest.mark.parametrize("directory", [".", "lib"])
def test_identity_refuses_unreadable_distribution_directory(tmp_path: Path, directory: str) -> None:
    binary = distribution(tmp_path / "runtime")
    known_readable = runtime_identity(str(binary))
    assert "smolvm-bin" in known_readable["files"]
    assert "lib/engine" in known_readable["files"]
    target = binary.parent / directory
    original_mode = target.stat().st_mode & 0o777
    target.chmod(0o111)  # Known paths remain usable; listing must fail closed.
    try:
        assert (binary.parent / "smolvm-bin").read_bytes() == b"initial"
        assert (binary.parent / "lib/engine").read_bytes() == b"engine"
        with pytest.raises(SkillEvalError):
            runtime_identity(str(binary))
    finally:
        target.chmod(original_mode)


def test_sparse_template_binds_size_and_allocated_bytes(tmp_path: Path) -> None:
    path = tmp_path / "template"
    with path.open("wb") as stream:
        stream.write(b"header")
        stream.truncate(16 * 1024 * 1024)
    before = file_digest(path, sparse=True)
    with path.open("r+b") as stream:
        stream.seek(8 * 1024 * 1024)
        stream.write(b"modified")
    assert file_digest(path, sparse=True) != before
    before = file_digest(path, sparse=True)
    os.truncate(path, path.stat().st_size * 2)
    assert file_digest(path, sparse=True) != before


def test_regular_identity_reader_rejects_links_and_fifos(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("content")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(OSError):
        file_digest(link)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(SkillEvalError, match="regular"):
        file_digest(fifo)
