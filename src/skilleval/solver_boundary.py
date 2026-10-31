"""Linux-only solver launcher, run by the trusted disposable-guest controller.

The supervisor remains outside the solver PID namespace and chroot. The native
CLI replaces PID 1, so reaping it also observes the kernel's namespace teardown.
This profile assumes /usr and the standard tool/library trees contain runtime
files only: image qualification must check that assumption and the native route.
This module imports only the standard library so it can be copied on its own.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import math
import os
import signal
import socket
import stat
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any

PROFILE = "solver-isolated-v1"
SOLVER_UID = 61000
SOLVER_GID = 61000
CLONE_NEWNS = 0x00020000
CLONE_NEWPID = 0x20000000
CLONE_NEWUTS = 0x04000000
HOSTNAME = "workspace"
MS_RDONLY, MS_NOSUID, MS_NODEV, MS_NOEXEC = 1, 2, 4, 8
MS_REMOUNT, MS_BIND, MS_REC, MS_PRIVATE = 32, 4096, 16384, 1 << 18
RUNTIME_PATHS = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32")
ETC_PATHS = ("/etc/alternatives", "/etc/ssl", "/etc/ld.so.cache")
TRANSPORT_FILTER = "vsock-deny-v1"
# Linux v6.18 uapi audit.h, asm-generic/unistd.h, x86 syscall_64.tbl.
# The profile intentionally supports only the native little-endian 64-bit ABI.
SECCOMP_ARCHES = {
    "aarch64": (0xC00000B7, 198, 199),
    "x86_64": (0xC000003E, 41, 53),
}
SECCOMP_ALLOW = 0x7FFF0000
SECCOMP_KILL = 0x80000000
SECCOMP_DENY = 0x00050000 | errno.EPERM


def transport_program(machine: str) -> list[tuple[int, int, int, int]]:
    """Exact classic-BPF instructions passed to PR_SET_SECCOMP.

    Argument 0 is a kernel int: compare its low word, including when callers
    supply nonzero high bits. io_uring can create sockets without a socket
    syscall, so its three entry points must also be unavailable in this profile.
    Foreign/compat ABIs and x32/high-numbered syscall forms fail closed.
    """
    if machine not in SECCOMP_ARCHES:
        raise ValueError("unsupported seccomp architecture")
    audit_arch, socket_nr, socketpair_nr = SECCOMP_ARCHES[machine]
    return [
        (0x20, 0, 0, 4),  # 0: LD W ABS seccomp_data.arch
        (0x15, 1, 0, audit_arch),  # 1: native architecture -> 3
        (0x06, 0, 0, SECCOMP_KILL),  # 2: foreign/compat architecture
        (0x20, 0, 0, 0),  # 3: LD W ABS seccomp_data.nr
        (0x35, 0, 1, 0x40000000),  # 4: x32 or negative nr -> 5, ordinary -> 6
        (0x06, 0, 0, SECCOMP_KILL),  # 5
        (0x15, 8, 0, 425),  # 6: io_uring_setup -> 15
        (0x15, 7, 0, 426),  # 7: io_uring_enter -> 15
        (0x15, 6, 0, 427),  # 8: io_uring_register -> 15
        (0x15, 2, 0, socket_nr),  # 9: socket -> 12
        (0x15, 1, 0, socketpair_nr),  # 10: socketpair -> 12
        (0x06, 0, 0, SECCOMP_ALLOW),  # 11: other native syscalls
        (0x20, 0, 0, 16),  # 12: LD W ABS low 32 bits of args[0]
        (0x15, 1, 0, 40),  # 13: AF_VSOCK -> 15
        (0x06, 0, 0, SECCOMP_ALLOW),  # 14: ordinary socket domains
        (0x06, 0, 0, SECCOMP_DENY),  # 15
    ]


def _real_directory(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("boundary roots must be absolute without parent traversal")
    current = Path("/")
    for part in path.parts[1:]:
        current /= part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("boundary root has a symlink component")
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("boundary roots must be existing directories")


def validate_layout(root: Path, work: Path) -> tuple[Path, Path]:
    """Validate the trusted source roots before changing ownership or mounts."""
    _real_directory(root)
    _real_directory(work)
    if root.is_relative_to(work) or work.is_relative_to(root):
        raise ValueError("boundary roots must not overlap")
    # Keep task/controller paths away from the runtime and kernel mount points.
    for path in (root, work):
        if path == Path("/") or any(
            path.is_relative_to(prefix)
            for prefix in (*RUNTIME_PATHS, "/etc", "/proc", "/dev", "/sys", "/run", "/opt")
        ):
            raise ValueError("boundary root overlaps a reserved filesystem tree")
    for name in ("home", "codex-home"):
        _real_directory(root / name)
    model_info = (root / "models.json").lstat()
    if not stat.S_ISREG(model_info.st_mode) or model_info.st_nlink != 1:
        raise ValueError("model catalog must be a regular single-link file")
    if (root / "solver-root").exists() or (root / "solver-root").is_symlink():
        raise ValueError("solver-root must be fresh")
    return root, work


def _tree_entries(path: Path) -> Iterator[Path]:
    """Inventory without following task links, rejecting hard/special files."""
    info = path.lstat()
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        raise ValueError("solver tree contains a hardlinked file")
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
        raise ValueError("solver tree contains a special file")
    yield path
    if stat.S_ISDIR(info.st_mode):
        for child in path.iterdir():
            yield from _tree_entries(child)


def _own_tree(path: Path) -> None:
    """Validate the whole tree before changing any ownership; never follow links."""
    entries = list(_tree_entries(path))
    for entry in entries:
        info = entry.lstat()
        if not stat.S_ISLNK(info.st_mode):
            os.chmod(entry, stat.S_IMODE(info.st_mode) & ~0o6000, follow_symlinks=False)
        os.chown(entry, SOLVER_UID, SOLVER_GID, follow_symlinks=False)


class Linux:
    """Checked Linux syscalls; no shell or mount helper inherits authority."""

    libc: ctypes.CDLL

    def __init__(self, *, require_root: bool = True) -> None:
        if sys.platform != "linux" or (require_root and os.geteuid() != 0):
            raise ValueError("solver boundary requires Linux guest root")
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.mount.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_ulong,
            ctypes.c_char_p,
        ]
        self.libc.mount.restype = ctypes.c_int

    def checked(self, result: int, operation: str) -> int:
        if result == -1:
            number = ctypes.get_errno()
            raise OSError(number, f"{operation}: {os.strerror(number)}")
        return result

    def prctl(self, option: int, *arguments: int) -> int:
        padded = (*arguments, *(0 for _ in range(4 - len(arguments))))
        return self.checked(
            self.libc.prctl(ctypes.c_int(option), *(ctypes.c_ulong(x) for x in padded)),
            "prctl",
        )

    def mount(
        self,
        source: Path | str | None,
        target: Path,
        filesystem: str | None = None,
        flags: int = 0,
        data: str | None = None,
    ) -> None:
        self.checked(
            self.libc.mount(
                os.fsencode(source) if source is not None else None,
                os.fsencode(target),
                filesystem.encode() if filesystem else None,
                flags,
                data.encode() if data else None,
            ),
            "mount",
        )


def install_transport_filter(linux: Linux) -> dict[str, Any]:
    """Install and check this process's immutable, inherited transport filter.

    This deny policy closes the guest-agent AF_VSOCK creation route. It is not
    a general syscall allowlist, network isolation, or a claim against kernel
    vulnerabilities. The caller closes ambient descriptors before installation.
    """
    if sys.byteorder != "little":
        raise ValueError("unsupported seccomp byte order")
    machine = os.uname().machine
    instructions = transport_program(machine)

    class Filter(ctypes.Structure):
        _fields_ = [
            ("code", ctypes.c_ushort),
            ("jt", ctypes.c_ubyte),
            ("jf", ctypes.c_ubyte),
            ("k", ctypes.c_uint32),
        ]

    class Program(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ushort), ("filter", ctypes.POINTER(Filter))]

    filters = (Filter * len(instructions))(*(Filter(*entry) for entry in instructions))
    program = Program(len(instructions), filters)
    if ctypes.sizeof(Filter) != 8 or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise ValueError("unsupported seccomp structure layout")
    linux.prctl(38, 1)  # PR_SET_NO_NEW_PRIVS, also permits unprivileged filter tests.
    linux.prctl(22, 2, ctypes.addressof(program))  # PR_SET_SECCOMP, SECCOMP_MODE_FILTER
    mode = linux.prctl(21)  # PR_GET_SECCOMP: read back the effective kernel mode.
    if mode != 2:
        raise ValueError("seccomp filter mode was not applied")
    linux.libc.syscall.restype = ctypes.c_long
    _, socket_nr, socketpair_nr = SECCOMP_ARCHES[machine]
    probe: dict[str, Any] = {}

    def denied(label: str, number: int, arguments: tuple[int, ...]) -> None:
        ctypes.set_errno(0)
        result = linux.libc.syscall(
            ctypes.c_long(number), *(ctypes.c_ulong(value) for value in arguments)
        )
        observed = ctypes.get_errno()
        if result >= 0 and number == socket_nr:
            os.close(result)
        if result != -1 or observed != errno.EPERM:
            raise ValueError(f"transport filter probe failed: {label} errno={observed}")
        probe[label] = observed

    for domain, suffix in ((40, ""), ((1 << 32) | 40, "_high_word")):
        denied("socket_vsock" + suffix, socket_nr, (domain, socket.SOCK_STREAM, 0))
        pair = (ctypes.c_int * 2)(-1, -1)
        try:
            denied(
                "socketpair_vsock" + suffix,
                socketpair_nr,
                (domain, socket.SOCK_STREAM, 0, ctypes.addressof(pair)),
            )
        finally:
            for descriptor in pair:
                if descriptor >= 0:
                    os.close(descriptor)
    for name, number in (("setup", 425), ("enter", 426), ("register", 427)):
        denied("io_uring_" + name, number, (0, 0, 0, 0, 0, 0))
    left, right = socket.socketpair()
    with left, right:
        left.sendall(b"allowed")
        if right.recv(7) != b"allowed":
            raise ValueError("Unix socket positive control failed")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM):
        pass
    probe.update(unix_socketpair=True, tcp_socket=True)
    return {
        "transport_filter": TRANSPORT_FILTER,
        "seccomp": True,
        "seccomp_mode": mode,
        "seccomp_arch": machine,
        "transport_filter_sha256": hashlib.sha256(bytes(filters)).hexdigest(),
        "transport_filter_probe": probe,
    }


def _destination(jail: Path, path: Path) -> Path:
    return jail / path.relative_to("/")


def _bind(linux: Linux, source: Path, target: Path, *, readonly: bool) -> None:
    target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if source.is_dir():
        target.mkdir(mode=0o755, exist_ok=True)
    else:
        target.touch(exist_ok=False)
    linux.mount(source, target, flags=MS_BIND)
    linux.mount(
        None,
        target,
        flags=MS_REMOUNT | MS_BIND | MS_NOSUID | MS_NODEV | (MS_RDONLY if readonly else 0),
    )


def _runtime_tree(linux: Linux, jail: Path, source: Path) -> None:
    target = _destination(jail, source)
    if source.is_symlink():
        # Merged-/usr layouts are common. Preserve only symlinks that resolve
        # within the explicit runtime allowlist, never arbitrary image paths.
        resolved = source.resolve(strict=True)
        if not resolved.is_relative_to("/usr"):
            raise ValueError("runtime symlink escapes /usr")
        target.symlink_to(os.readlink(source))
    elif source.is_dir():
        _bind(linux, source, target, readonly=True)
    else:
        raise ValueError("runtime tree has an unsupported type")


def _assemble(linux: Linux, root: Path, work: Path) -> tuple[Path, list[str], int]:
    root_info = root.stat()
    if root_info.st_uid != 0 or root_info.st_mode & 0o077:
        raise ValueError("controller root must be root-owned and private")
    for writable in (work, root / "home", root / "codex-home"):
        _own_tree(writable)
    (root / "models.json").chmod(0o444)
    last_cap = int(Path("/proc/sys/kernel/cap_last_cap").read_text().strip())
    if not 0 <= last_cap <= 63:
        raise ValueError("unsupported capability range")
    linux.checked(linux.libc.unshare(CLONE_NEWNS | CLONE_NEWPID | CLONE_NEWUTS), "unshare")
    hostname_bytes = HOSTNAME.encode()
    linux.checked(
        linux.libc.sethostname(
            ctypes.c_char_p(hostname_bytes), ctypes.c_size_t(len(hostname_bytes))
        ),
        "sethostname",
    )
    if os.uname().nodename != HOSTNAME:
        raise ValueError("private solver hostname was not applied")
    linux.mount(None, Path("/"), flags=MS_REC | MS_PRIVATE)
    jail = root / "solver-root"
    jail.mkdir(mode=0o755)
    linux.mount(jail, jail, flags=MS_BIND)
    for name, options in (("tmp", "mode=1777,size=256m"), ("dev", "mode=755,size=1m")):
        target = jail / name
        target.mkdir(mode=0o755)
        linux.mount(
            "tmpfs", target, "tmpfs", MS_NOSUID | (MS_NODEV if name == "tmp" else 0), options
        )
    sources = []
    for name in RUNTIME_PATHS:
        source = Path(name)
        if source.exists() or source.is_symlink():
            _runtime_tree(linux, jail, source)
            sources.append(name)
    if "/usr" not in sources:
        raise ValueError("required /usr runtime tree is unavailable")
    for name in ETC_PATHS:
        source = Path(name)
        if source.exists():
            if source.is_symlink():
                raise ValueError("etc allowlist source must not be a symlink")
            _bind(linux, source, _destination(jail, source), readonly=True)
            sources.append(name)
    etc = jail / "etc"
    etc.mkdir(mode=0o755, exist_ok=True)
    (etc / "passwd").write_text(
        f"solver:x:{SOLVER_UID}:{SOLVER_GID}:Solver:{root / 'home'}:/bin/sh\n"
    )
    (etc / "group").write_text(f"solver:x:{SOLVER_GID}:\n")
    (etc / "nsswitch.conf").write_text("passwd: files\ngroup: files\nhosts: files\n")
    (etc / "hosts").write_text("127.0.0.1 localhost\n::1 localhost\n")
    for name in ("passwd", "group", "nsswitch.conf", "hosts"):
        (etc / name).chmod(0o644)
    for source in (work, root / "home", root / "codex-home", root / "models.json"):
        _bind(linux, source, _destination(jail, source), readonly=source == root / "models.json")
        sources.append(str(source))
    for name, major, minor in (
        ("null", 1, 3),
        ("zero", 1, 5),
        ("random", 1, 8),
        ("urandom", 1, 9),
        ("tty", 5, 0),
    ):
        os.mknod(jail / "dev" / name, stat.S_IFCHR | 0o666, os.makedev(major, minor))
        os.chmod(jail / "dev" / name, 0o666)
    for name, symlink_target in (
        ("fd", "/proc/self/fd"),
        ("stdin", "/proc/self/fd/0"),
        ("stdout", "/proc/self/fd/1"),
        ("stderr", "/proc/self/fd/2"),
    ):
        (jail / "dev" / name).symlink_to(symlink_target)
    (jail / "dev" / "pts").mkdir(mode=0o755)
    linux.mount(
        "devpts",
        jail / "dev" / "pts",
        "devpts",
        MS_NOSUID | MS_NOEXEC,
        f"newinstance,ptmxmode=0666,mode=0620,gid={SOLVER_GID}",
    )
    (jail / "dev" / "ptmx").symlink_to("pts/ptmx")
    (jail / "dev" / "shm").mkdir(mode=0o755)
    linux.mount("tmpfs", jail / "dev" / "shm", "tmpfs", MS_NOSUID | MS_NODEV, "mode=1777,size=64m")
    (jail / "proc").mkdir(mode=0o755)
    return jail, sources, last_cap


def _clear_capabilities(linux: Linux, last_cap: int) -> None:
    linux.prctl(47, 4)  # PR_CAP_AMBIENT_CLEAR_ALL
    for capability in range(last_cap + 1):
        linux.prctl(24, capability)  # PR_CAPBSET_DROP, before losing SETPCAP
    os.setgroups([])
    linux.checked(linux.libc.setresgid(SOLVER_GID, SOLVER_GID, SOLVER_GID), "setresgid")
    linux.checked(linux.libc.setresuid(SOLVER_UID, SOLVER_UID, SOLVER_UID), "setresuid")

    class Header(ctypes.Structure):
        _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]

    class Data(ctypes.Structure):
        _fields_ = [
            ("effective", ctypes.c_uint32),
            ("permitted", ctypes.c_uint32),
            ("inheritable", ctypes.c_uint32),
        ]

    header, data = Header(0x20080522, 0), (Data * 2)()
    linux.checked(linux.libc.capset(ctypes.byref(header), ctypes.byref(data)), "capset")
    linux.checked(linux.libc.capget(ctypes.byref(header), ctypes.byref(data)), "capget")
    if any(entry.effective or entry.permitted or entry.inheritable for entry in data):
        raise ValueError("capabilities were not cleared")
    if any(linux.prctl(23, capability) for capability in range(last_cap + 1)):
        raise ValueError("capability bounding set was not cleared")
    linux.prctl(38, 1)  # PR_SET_NO_NEW_PRIVS
    if linux.prctl(39) != 1:
        raise ValueError("no_new_privs was not applied")
    for syscall_name, expected in (("getresuid", SOLVER_UID), ("getresgid", SOLVER_GID)):
        real, effective, saved = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
        linux.checked(
            getattr(linux.libc, syscall_name)(
                ctypes.byref(real), ctypes.byref(effective), ctypes.byref(saved)
            ),
            syscall_name,
        )
        if (real.value, effective.value, saved.value) != (expected,) * 3:
            raise ValueError("solver identity was not applied")
    if os.getgroups():
        raise ValueError("solver supplementary groups were not cleared")


def _message(channel: socket.socket, value: dict[str, Any]) -> None:
    channel.sendall(json.dumps(value, separators=(",", ":")).encode() + b"\n")


def _receive(channel: socket.socket) -> dict[str, Any]:
    data = bytearray()
    while not data.endswith(b"\n"):
        chunk = channel.recv(1)
        if not chunk or len(data) >= 16384:
            raise ValueError("solver setup handshake was interrupted")
        data.extend(chunk)
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("invalid solver setup handshake")
    return value


def _native(
    linux: Linux,
    channel: socket.socket,
    jail: Path,
    work: Path,
    last_cap: int,
    argv: list[str],
) -> None:
    try:
        # Handshakes after each PDEATHSIG ensure a supervisor lost during setup
        # cannot leave an orphan. setresuid/setresgid clear the first setting.
        linux.prctl(1, signal.SIGKILL)
        _message(channel, {"phase": "forked"})
        if _receive(channel) != {"continue": True}:
            raise ValueError("supervisor refused solver setup")
        if os.getpid() != 1:
            raise ValueError("native process did not become namespace PID 1")
        linux.mount("proc", jail / "proc", "proc", MS_RDONLY | MS_NOSUID | MS_NODEV | MS_NOEXEC)
        linux.mount(None, jail, flags=MS_REMOUNT | MS_BIND | MS_RDONLY | MS_NOSUID | MS_NODEV)
        os.chroot(jail)
        os.chdir(work)
        descriptor = channel.fileno()
        for name in os.listdir("/proc/self/fd"):
            observed = int(name)
            if observed > 2 and observed != descriptor:
                with suppress(OSError):
                    os.close(observed)
        _clear_capabilities(linux, last_cap)
        transport = install_transport_filter(linux)
        linux.prctl(1, signal.SIGKILL)
        for number in (signal.SIGTERM, signal.SIGINT):
            signal.signal(number, signal.SIG_DFL)
        _message(channel, {"phase": "dropped", "pid": os.getpid(), **transport})
        if _receive(channel) != {"continue": True}:
            raise ValueError("supervisor refused native execution")
        channel.set_inheritable(False)
        os.execvpe(argv[0], argv, dict(os.environ))
    except BaseException as error:
        with suppress(OSError):
            _message(channel, {"phase": "failed", "error": str(error)[:1024]})
    os._exit(125)


def _receipt(root: Path, value: dict[str, Any]) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".boundary-", dir=root)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, root / "boundary.json")
    finally:
        with suppress(FileNotFoundError):
            os.unlink(name)


def _finite_duration(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def valid_receipt(value: object) -> bool:
    """Check completed setup/teardown, independently of native task success."""
    return (
        isinstance(value, dict)
        and type(value.get("schema_version")) is int
        and value.get("schema_version") == 1
        and value.get("profile") == PROFILE
        and value.get("status") == "ready"
        and type(value.get("uid")) is int
        and value.get("uid") == SOLVER_UID
        and type(value.get("gid")) is int
        and value.get("gid") == SOLVER_GID
        and value.get("groups") == []
        and value.get("no_new_privs") is True
        and value.get("capabilities_cleared") is True
        and value.get("transport_filter") == TRANSPORT_FILTER
        and value.get("seccomp") is True
        and value.get("namespace_reaped") is True
        and type(value.get("native_returncode")) is int
        and _finite_duration(value.get("native_elapsed_seconds"))
        and _finite_duration(value.get("launcher_elapsed_seconds"))
        and not value.get("error")
    )


def run(root: Path, work: Path, argv: list[str]) -> int:
    """Fail closed; return only after namespace PID 1 has been killed/reaped."""
    launcher_started = time.monotonic()
    validate_layout(root, work)
    if not argv or not argv[0]:
        raise ValueError("native command must be nonempty")
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "profile": PROFILE,
        "status": "failed",
        "uid": SOLVER_UID,
        "gid": SOLVER_GID,
        "groups": [],
        "no_new_privs": False,
        "capabilities_cleared": False,
        "transport_filter": None,
        "seccomp": False,
        "namespace_reaped": False,
        "native_returncode": None,
        "native_elapsed_seconds": None,
        "launcher_elapsed_seconds": None,
    }
    child = 0
    interrupted = 0
    native_started: float | None = None
    previous_umask = os.umask(0o022)
    previous: dict[int, Any] = {}

    def terminate(number: int, _frame: Any) -> None:
        nonlocal interrupted
        interrupted = number
        if child:
            with suppress(ProcessLookupError):
                os.kill(child, signal.SIGKILL)

    try:
        for number in (signal.SIGTERM, signal.SIGINT):
            previous[number] = signal.signal(number, terminate)
        linux = Linux()
        jail, sources, last_cap = _assemble(linux, root, work)
        receipt["mount_sources"] = sources
        if interrupted:
            raise ValueError("boundary setup interrupted")
        parent_channel, native_channel = socket.socketpair()
        child = os.fork()
        if child == 0:
            parent_channel.close()
            _native(linux, native_channel, jail, work, last_cap, argv)
            os._exit(125)
        native_channel.close()
        with parent_channel:
            if interrupted:
                os.kill(child, signal.SIGKILL)
            if _receive(parent_channel) != {"phase": "forked"}:
                raise ValueError("native fork was not confirmed")
            _message(parent_channel, {"continue": True})
            ready = _receive(parent_channel)
            if (
                ready.get("phase") != "dropped"
                or ready.get("pid") != 1
                or ready.get("transport_filter") != TRANSPORT_FILTER
                or ready.get("seccomp") is not True
            ):
                raise ValueError(str(ready.get("error", "native setup was not confirmed")))
            for name in (
                "transport_filter",
                "seccomp",
                "seccomp_mode",
                "seccomp_arch",
                "transport_filter_sha256",
                "transport_filter_probe",
            ):
                receipt[name] = ready[name]
            receipt.update(status="ready", no_new_privs=True, capabilities_cleared=True)
            native_started = time.monotonic()
            _message(parent_channel, {"continue": True})
            # A successful exec closes the channel. Failure is an explicit
            # setup message, never a native-run success inferred from EOF alone.
            tail = parent_channel.recv(16384)
            if tail:
                receipt["status"] = "failed"
                receipt["error"] = tail.decode("utf-8", errors="replace")[:1024]
        _, status = os.waitpid(child, 0)
        child = 0
        receipt["namespace_reaped"] = True
        receipt["native_returncode"] = os.waitstatus_to_exitcode(status)
    except (OSError, ValueError) as error:
        receipt.update(status="failed", error=str(error)[:1024])
    finally:
        if child:
            with suppress(ProcessLookupError):
                os.kill(child, signal.SIGKILL)
            _, status = os.waitpid(child, 0)
            receipt["native_returncode"] = os.waitstatus_to_exitcode(status)
        receipt["namespace_reaped"] = True
        if native_started is not None:
            receipt["native_elapsed_seconds"] = time.monotonic() - native_started
        receipt["launcher_elapsed_seconds"] = time.monotonic() - launcher_started
        if interrupted:
            receipt["interrupted_signal"] = interrupted
        for signal_number, handler in previous.items():
            signal.signal(signal_number, handler)
        os.umask(previous_umask)
        _receipt(root, receipt)
    if interrupted:
        return 128 + interrupted
    if receipt["status"] != "ready":
        return 125
    result = int(receipt["native_returncode"])
    return result if result >= 0 else 128 - result


def main(arguments: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if arguments is None else arguments
    if len(arguments) < 4 or arguments[2] != "--":
        print("usage: solver_boundary.py ROOT WORK -- COMMAND [ARG ...]", file=sys.stderr)
        return 125
    try:
        return run(Path(arguments[0]), Path(arguments[1]), arguments[3:])
    except (OSError, ValueError) as error:
        print(f"solver boundary refused: {error}", file=sys.stderr)
        return 125


if __name__ == "__main__":
    raise SystemExit(main())
