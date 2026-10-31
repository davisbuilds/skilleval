"""Portable boundary input controls, not proof of Linux namespace isolation."""

import json
import os
import socket
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from skilleval.solver_boundary import (
    ETC_PATHS,
    PROFILE,
    TRANSPORT_FILTER,
    _message,
    _receive,
    _tree_entries,
    main,
    run,
    transport_program,
    valid_receipt,
    validate_layout,
)


def layout(tmp_path: Path) -> tuple[Path, Path]:
    root, work = tmp_path / "controller", tmp_path / "task"
    root.mkdir()
    work.mkdir()
    (root / "home").mkdir()
    (root / "codex-home").mkdir()
    (root / "models.json").write_text("{}")
    return root, work


def test_layout_accepts_distinct_real_roots_and_rejects_a_symlink(tmp_path: Path) -> None:
    root, work = layout(tmp_path)
    assert validate_layout(root, work) == (root, work)
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        validate_layout(alias, work)


def test_layout_rejects_task_ancestor_of_controller(tmp_path: Path) -> None:
    root = tmp_path / "controller"
    root.mkdir()
    with pytest.raises(ValueError, match="overlap"):
        validate_layout(root, tmp_path)


def test_layout_rejects_parent_symlink_and_writable_root_symlink(tmp_path: Path) -> None:
    root, work = layout(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        validate_layout(alias / "controller", work)
    (root / "home").rmdir()
    (root / "home").symlink_to(work, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        validate_layout(root, work)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory"])
def test_catalog_is_regular_private_source_not_indirection(tmp_path: Path, kind: str) -> None:
    root, work = layout(tmp_path)
    model = root / "models.json"
    other = root / "settings.json"
    other.write_text('{"hidden":"controller"}')
    model.unlink()
    if kind == "symlink":
        model.symlink_to(other)
    elif kind == "hardlink":
        os.link(other, model)
    else:
        model.mkdir()
    with pytest.raises(ValueError, match="catalog"):
        validate_layout(root, work)


def test_tree_inventory_includes_links_without_traversing_targets(tmp_path: Path) -> None:
    root, work = layout(tmp_path)
    secret = root / "settings.json"
    secret.write_text("hidden controller state")
    public = work / "ordinary.txt"
    public.write_text("ordinary task")
    link = work / "controller-link"
    link.symlink_to(root, target_is_directory=True)
    assert set(_tree_entries(work)) == {work, public, link}
    assert secret.read_text() == "hidden controller state"


def test_tree_refuses_hardlinks_and_special_files(tmp_path: Path) -> None:
    root, work = layout(tmp_path)
    secret = root / "settings.json"
    secret.write_text("hidden controller state")
    linked = work / "controller-link"
    os.link(secret, linked)
    with pytest.raises(ValueError, match="hardlinked"):
        list(_tree_entries(work))
    linked.unlink()
    os.mkfifo(work / "fifo")
    with pytest.raises(ValueError, match="special"):
        list(_tree_entries(work))


def test_nonlinux_refuses_without_mutating_solver_tree(tmp_path: Path) -> None:
    if sys.platform == "linux":
        pytest.skip("requires a real non-Linux process, not a patched platform flag")
    root, work = layout(tmp_path)
    ordinary = work / "source.txt"
    ordinary.write_text("unchanged")
    before = ordinary.stat()
    assert run(root, work, ["sh", "-c", "touch source.txt"]) == 125
    assert ordinary.stat().st_uid == before.st_uid
    assert ordinary.stat().st_mtime_ns == before.st_mtime_ns
    receipt = json.loads((root / "boundary.json").read_text())
    assert receipt["profile"] == PROFILE
    assert receipt["status"] == "failed" and "Linux guest root" in receipt["error"]
    assert receipt["namespace_reaped"] is True
    assert receipt["no_new_privs"] is False
    assert receipt["capabilities_cleared"] is False
    assert receipt["native_returncode"] is None


def test_handshake_detects_closed_peer_and_preserves_confirmed_messages() -> None:
    parent, child = socket.socketpair()
    with parent, child:
        _message(child, {"phase": "dropped", "pid": 1})
        assert _receive(parent) == {"phase": "dropped", "pid": 1}
        child.close()
        with pytest.raises(ValueError, match="interrupted"):
            _receive(parent)


def test_no_broad_etc_or_controller_sources_and_cli_requires_separator() -> None:
    assert set(ETC_PATHS) == {"/etc/alternatives", "/etc/ssl", "/etc/ld.so.cache"}
    assert main(["/tmp/controller", "/workspace/task", "sh"]) == 125


def test_existing_jail_refused_before_reuse(tmp_path: Path) -> None:
    root, work = layout(tmp_path)
    (root / "solver-root").mkdir()
    with pytest.raises(ValueError, match="fresh"):
        validate_layout(root, work)


def test_receipt_requires_successful_drop_and_reaping_not_native_success() -> None:
    receipt = {
        "schema_version": 1,
        "profile": PROFILE,
        "status": "ready",
        "uid": 61000,
        "gid": 61000,
        "groups": [],
        "no_new_privs": True,
        "capabilities_cleared": True,
        "transport_filter": TRANSPORT_FILTER,
        "seccomp": True,
        "namespace_reaped": True,
        "native_returncode": 3,
        "native_elapsed_seconds": 0.2,
        "launcher_elapsed_seconds": 0.5,
    }
    assert valid_receipt(receipt)
    for key in receipt:
        partial = {name: value for name, value in receipt.items() if name != key}
        assert not valid_receipt(partial), key
    for key, bad in (
        ("schema_version", True),
        ("uid", 0),
        ("gid", 0),
        ("groups", [0]),
        ("no_new_privs", 1),
        ("capabilities_cleared", False),
        ("transport_filter", "unrestricted"),
        ("seccomp", 1),
        ("namespace_reaped", False),
        ("status", "failed"),
        ("native_returncode", True),
        ("native_elapsed_seconds", float("nan")),
        ("native_elapsed_seconds", -0.1),
        ("native_elapsed_seconds", True),
        ("launcher_elapsed_seconds", float("inf")),
    ):
        assert not valid_receipt({**receipt, key: bad}), key
    assert not valid_receipt({**receipt, "error": "exec failed"})
    assert not valid_receipt(None)


def evaluate_program(machine: str, arch: int, syscall: int, domain: int = 0) -> int:
    """Independent evaluator for the four cBPF operations in the emitted bytes."""
    data = struct.pack("<iI7Q", syscall, arch, 0, domain, 0, 0, 0, 0, 0)
    encoded = b"".join(struct.pack("<HBBI", *entry) for entry in transport_program(machine))
    accumulator, instruction = 0, 0
    while instruction * 8 < len(encoded):
        code, yes, no, constant = struct.unpack_from("<HBBI", encoded, instruction * 8)
        if code == 0x20:
            accumulator = struct.unpack_from("<I", data, constant)[0]
        elif code == 0x15:
            instruction += yes if accumulator == constant else no
        elif code == 0x35:
            instruction += yes if accumulator >= constant else no
        elif code == 0x06:
            return constant
        else:
            raise AssertionError(f"unexpected cBPF operation {code}")
        instruction += 1
    raise AssertionError("filter did not return an action")


@pytest.mark.parametrize(
    ("machine", "audit_arch", "socket_nr", "socketpair_nr"),
    [("aarch64", 0xC00000B7, 198, 199), ("x86_64", 0xC000003E, 41, 53)],
)
def test_filter_denies_both_vsock_calls_and_low_word_bypasses(
    machine: str, audit_arch: int, socket_nr: int, socketpair_nr: int
) -> None:
    for number in (socket_nr, socketpair_nr):
        for domain in (40, (1 << 32) | 40, (0xFFFFFFFF << 32) | 40):
            assert evaluate_program(machine, audit_arch, number, domain) == 0x00050001
        for domain in (socket.AF_UNIX, socket.AF_INET, socket.AF_INET6):
            assert evaluate_program(machine, audit_arch, number, domain) == 0x7FFF0000


@pytest.mark.parametrize(
    ("machine", "audit_arch", "socket_nr"),
    [("aarch64", 0xC00000B7, 198), ("x86_64", 0xC000003E, 41)],
)
def test_filter_denies_io_uring_and_foreign_compat_x32_abis(
    machine: str, audit_arch: int, socket_nr: int
) -> None:
    for number in (425, 426, 427):
        assert evaluate_program(machine, audit_arch, number) == 0x00050001
    for number in (0, 1, 3, 60, 424, 428):
        assert evaluate_program(machine, audit_arch, number, 40) == 0x7FFF0000
    for foreign in (0, 0x40000003, 0x40000028, audit_arch ^ 0x80000000):
        assert evaluate_program(machine, foreign, socket_nr, 40) == 0x80000000
    for number in (socket_nr | 0x40000000, 0x40000000, -1):
        assert evaluate_program(machine, audit_arch, number, 40) == 0x80000000


def test_filter_unknown_architecture_fails_closed() -> None:
    with pytest.raises(ValueError, match="architecture"):
        transport_program("i686")


@pytest.mark.skipif(sys.platform != "linux", reason="actual seccomp requires a Linux child")
def test_linux_child_enforces_filter_and_preserves_local_socket_work() -> None:
    # This child changes only its own irreversible security state. It neither
    # launches a VM nor establishes the production native-route boundary.
    source = """
import errno,json,socket,subprocess,sys
from skilleval.solver_boundary import Linux, install_transport_filter
receipt = install_transport_filter(Linux(require_root=False))
try:
    socket.socket(40, socket.SOCK_STREAM)
    raise AssertionError('AF_VSOCK was allowed')
except OSError as error:
    assert error.errno == errno.EPERM
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
    listener.bind(('127.0.0.1', 0)); listener.listen(1)
    with socket.create_connection(listener.getsockname(), timeout=2) as client:
        server, _ = listener.accept()
        with server:
            client.sendall(b'ordinary task'); assert server.recv(13) == b'ordinary task'
child_source = (
    'import errno,socket\\n'
    'try:\\n socket.socket(40,socket.SOCK_STREAM)\\n'
    'except OSError as error:\\n assert error.errno==errno.EPERM\\n'
    'else:\\n raise AssertionError("filter not inherited")\\n'
)
child = subprocess.run([sys.executable, '-c', child_source], check=True, capture_output=True)
assert child.returncode == 0
print(json.dumps(receipt))
"""
    result = subprocess.run(
        [sys.executable, "-c", source], capture_output=True, text=True, timeout=10, check=False
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["seccomp"] is True and receipt["seccomp_mode"] == 2
    assert receipt["transport_filter"] == TRANSPORT_FILTER
    assert receipt["transport_filter_probe"]["socket_vsock_high_word"] == 1
    assert receipt["transport_filter_probe"]["unix_socketpair"] is True
