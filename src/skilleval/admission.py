"""Explicit local environment admission; never scored-study or grader approval.

A trusted operator retains the seal's SHA-256 outside the mutable evidence set.
This detects drift, not forgery by that operator or compromise of the host. No
command here launches a VM, runs task code, discovers auth, or contacts a model.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import runpy
import stat
import tarfile
from pathlib import Path
from typing import Any

from skilleval.errors import SkillEvalError
from skilleval.gateway import EFFORT, MODEL, codex_provider_config
from skilleval.harnesses.codex import codex_version_identity, parse_codex_jsonl
from skilleval.identity import file_digest, object_digest, snapshot_identity
from skilleval.lifecycle import _environment
from skilleval.qualification import plan_qualification

POLICY = "bounded-native-control-v1"
MAX_JSON = 16 * 1024 * 1024
MARKERS = (
    "/workspace/synthetic/previous-workspace",
    "/run/qualification/codex-home/sessions/previous-session",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SkillEvalError("environment admission refused: " + message)


def _same(left: Any, right: Any) -> bool:
    """JSON equality preserves boolean versus integer identities."""
    return object_digest(left) == object_digest(right)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def _json(raw: bytes | str) -> Any:
    return json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda _: _invalid_number())


def _invalid_number() -> None:
    raise SkillEvalError("environment admission refused: nonfinite JSON number")


def _read(path: Path, limit: int = MAX_JSON) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        _require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= limit, "unbounded input")
        raw = source.read(limit + 1)
        after = os.fstat(source.fileno())
        _require(len(raw) == before.st_size and len(raw) <= limit, "input changed or exceeds limit")
        _require(
            all(
                getattr(before, key) == getattr(after, key)
                for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            ),
            "input changed while reading",
        )
    return raw


def _document(path: Path) -> dict[str, Any]:
    value = _json(_read(path))
    _require(isinstance(value, dict), "expected JSON object")
    return dict(value)


def _good(row: dict[str, Any]) -> bool:
    return (
        type(row.get("returncode")) is int
        and row["returncode"] == 0
        and row.get("timed_out") is False
        and row.get("output_truncated") is False
        and row.get("pipe_drain_incomplete") is False
    )


def _steps(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = report["steps"]
    _require(isinstance(rows, list), "missing execution steps")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        _require(isinstance(row, dict) and isinstance(row.get("label"), str), "invalid step")
        label = row["label"]
        _require(label not in result, "duplicate execution step")
        result[label] = row
    return result


def _step(rows: dict[str, dict[str, Any]], label: str) -> dict[str, Any]:
    _require(label in rows and _good(rows[label]), "failed or missing step " + label)
    return rows[label]


def _inventory(row: dict[str, Any], runtime: str) -> set[str]:
    _require(
        _good(row) and row.get("argv") == [runtime, "machine", "ls", "--json"],
        "invalid same-state inventory",
    )
    values = _json(row["stdout"])
    _require(isinstance(values, list), "invalid inventory rows")
    names: set[str] = set()
    for value in values:
        _require(isinstance(value, dict) and isinstance(value.get("name"), str), "invalid machine")
        _require(value["name"] not in names, "duplicate machine")
        names.add(value["name"])
    return names


def _guest_invocation(
    row: dict[str, Any],
    runtime: str,
    machine: str,
    *,
    timeout: int = 120,
    workdir: str | None = None,
    command: list[str] | None = None,
    python_fragments: tuple[str, ...] = (),
) -> None:
    prefix = [runtime, "machine", "exec", "--name", machine]
    if workdir is not None:
        prefix += ["--workdir", workdir]
    prefix += ["--timeout", f"{timeout}s", "--"]
    argv = row.get("argv")
    _require(
        isinstance(argv, list) and argv[: len(prefix)] == prefix,
        "guest execution invocation differs",
    )
    assert isinstance(argv, list)
    actual = argv[len(prefix) :]
    if command is not None:
        _require(actual == command, "guest command invocation differs")
    else:
        # Source identity binds the program. Check its authority-bearing route
        # and meaningful command kind without duplicating large inline programs.
        _require(
            len(actual) == 3
            and actual[:2] == ["python3", "-c"]
            and isinstance(actual[2], str)
            and all(fragment in actual[2] for fragment in python_fragments),
            "guest Python control invocation differs",
        )


def _create_invocation(
    runtime: str, machine: str, image: Path, config: dict[str, Any]
) -> list[str]:
    argv = [
        runtime,
        "machine",
        "create",
        "--name",
        machine,
        "--image",
        str(image),
        "--cpus",
        str(config["cpus"]),
        "--mem",
        str(config["memory_mib"]),
    ]
    if "observer_socket" in config:
        argv += [
            "--mount-socket",
            config["observer_socket"] + ":" + config["guest_observer_socket"],
            "--",
            "sleep",
            "infinity",
        ]
    elif "socket_mounts" in config:
        mount = config["socket_mounts"][0]
        argv += ["--mount-socket", mount["host"] + ":" + mount["guest"], "--", "sleep", "infinity"]
    return argv


def _ticket(path: Path, identity: dict[str, Any], image: dict[str, Any]) -> dict[str, Any]:
    ticket = _document(path)
    for key in ("cleanup_complete", "runtime_home_removed", "observer_positive", "observed_absent"):
        _require(ticket.get(key) is True, "incomplete recovery " + key)
    _require(
        _same(ticket.get("identity"), identity) and ticket.get("identity_changed") is False,
        "recovery identity missing or changed",
    )
    _require(
        ticket.get("kind") == "skilleval-lifecycle-recovery"
        and type(ticket.get("schema_version")) is int
        and ticket["schema_version"] == 1
        and ticket.get("phase") == "recovered"
        and ticket.get("create_attempted") is True,
        "invalid recovered ticket",
    )
    _require(
        _same(ticket.get("image"), image)
        and ticket.get("output") == str(path.parent)
        and ticket.get("runtime_state") == str(path.parent / "runtime-state"),
        "recovery artifact binding differs",
    )
    machine = ticket["machine_name"]
    _require(
        isinstance(machine, str)
        and bool(re.fullmatch(r"skilleval-(?:offline|native|lifecycle)-[a-f0-9]{32}", machine)),
        "invalid machine name",
    )
    home = Path(ticket["runtime_home"])
    _require(
        ticket.get("env") == _environment(path.parent / "runtime-state", home),
        "recovery namespace differs",
    )
    config = ticket["config"]
    _require(
        ticket["config_sha256"]
        == hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        "recovery config digest differs",
    )
    _require(
        config["cpus"] == 2
        and config["memory_mib"] == 2048
        and config["guest_network"] is False
        and config["host_mounts"] == []
        and config["secrets"] == [],
        "unqualified VM configuration",
    )
    runtime = identity["runtime"]["binary"]
    rows = _steps(ticket)
    if "observer_socket" in config:
        _require(
            _step(rows, "create")["argv"]
            == _create_invocation(runtime, machine, path.parent / "runtime-state/image.tar", config)
            and _step(rows, "start")["argv"] == [runtime, "machine", "start", "--name", machine],
            "lifecycle VM invocation differs",
        )
    inventories = [
        row for row in rows.values() if row.get("argv") == [runtime, "machine", "ls", "--json"]
    ]
    _require(
        any(machine in _inventory(row, runtime) for row in inventories),
        "machine observer lacks known positive",
    )
    final = rows.get("recovery_inventory_after")
    _require(
        final is not None and machine not in _inventory(final, runtime),
        "machine not observed absent",
    )
    cleanup = ticket["cleanup"]
    _require(
        isinstance(cleanup, list)
        and {row.get("action") for row in cleanup} == {"stop", "delete"}
        and len(cleanup) == 2
        and all(
            _good(row)
            and row.get("argv")
            == [runtime, "machine", row["action"], "--name", machine]
            + (["--force"] if row["action"] == "delete" else [])
            for row in cleanup
        ),
        "recovery cleanup incomplete",
    )
    return ticket


def _native_policy() -> dict[str, Any]:
    # Own trusted source only; the main guard prevents every opt-in operation.
    return runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/qualify-native.py"))


def _native(report: dict[str, Any], policy: dict[str, Any], catalog: Path, tooling: Path) -> None:
    _require(
        report.get("status") == "authenticated_native_passed"
        and report.get("credential_mode") == "existing-host-chatgpt-login",
        "authenticated native control required",
    )
    _require(
        report.get("model") == MODEL and report.get("effort") == EFFORT,
        "native model/effort differs",
    )
    _require(
        report.get("catalog_sha256") == file_digest(catalog) == policy["CATALOG_SHA256"],
        "native catalog differs",
    )
    sources = report["identity"]["sources"]
    for key, source_name in (
        ("probe_sha256", "scripts/qualify-native.py"),
        ("gateway_sha256", "src/skilleval/gateway.py"),
        ("gateway_vendor_sha256", "src/skilleval/_vendor/openbench_gateway.py"),
    ):
        _require(report.get(key) == sources[source_name], "native producer/gateway differs")
    mode = policy["mode_settings"](True)
    _require(
        _same(report.get("mode"), mode)
        and _same(
            report.get("broker_limits"),
            {
                "max_requests": mode["max_requests"],
                "max_body_bytes": 4194304,
                "timeout_seconds": mode["request_timeout_seconds"],
                "max_concurrency": 1,
                "lifetime_seconds": mode["lifetime_seconds"],
            },
        ),
        "native limits or mode differs",
    )
    raw = _read(tooling)
    entries: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for member in archive:
            _require(
                member.isfile()
                and member.name not in entries
                and member.name
                in {"models.json", "config.toml", "runner.py", "mode.json", "targets.json"}
                and 0 < member.size <= MAX_JSON,
                "unsupported native tooling artifact",
            )
            source = archive.extractfile(member)
            assert source is not None
            entries[member.name] = source.read(MAX_JSON + 1)
    expected = {
        "models.json": _read(catalog),
        "config.toml": codex_provider_config(8765, Path("/run/qualification/models.json")).encode(),
        "runner.py": policy["GUEST_RUNNER"].encode(),
        "mode.json": json.dumps(mode).encode(),
    }
    _require(
        set(entries) == {*expected, "targets.json"}
        and all(entries[name] == content for name, content in expected.items())
        and report.get("tooling_sha256")
        == {name: hashlib.sha256(content).hexdigest() for name, content in entries.items()},
        "native configuration/tooling differs",
    )
    targets = _json(entries["targets.json"])
    _require(
        targets.get("host_canary") == str(tooling.parent.parent / "host-canary"),
        "host canary target differs",
    )
    rows = _steps(report)
    runtime, machine = report["identity"]["runtime"]["binary"], report["machine_name"]
    _guest_invocation(
        _step(rows, "codex_version"), runtime, machine, command=["codex", "--version"]
    )
    _guest_invocation(
        _step(rows, "guest_toolchain_hashes"),
        runtime,
        machine,
        python_fragments=(
            "hashlib",
            "/usr/local/bin/codex",
            "/usr/local/bin/codex-code-mode-host",
            "/opt/skilleval/openbench_gateway.py",
        ),
    )
    _require(
        _step(rows, "copy_tooling")["argv"]
        == [runtime, "machine", "cp", str(tooling), machine + ":/run/tooling.tar"],
        "native tooling transfer invocation differs",
    )
    _guest_invocation(
        _step(rows, "prepare_tooling"),
        runtime,
        machine,
        python_fragments=("/run/tooling.tar", "/run/qualification", "extractall", "filter='data'"),
    )
    for filename in (
        "controls.json",
        "native-result.json",
        "relay-stdout.jsonl",
        "relay-stderr.log",
    ):
        _guest_invocation(
            _step(rows, "capture_" + filename),
            runtime,
            machine,
            command=["cat", "/run/qualification/" + filename],
        )
    version = _step(rows, "codex_version")["stdout"]
    observed = codex_version_identity(version)
    _require(
        _same(report.get("codex_identity"), observed)
        and report.get("codex_version") == observed["observed_version"],
        "native observed Codex identity differs",
    )
    toolchain = _json(_step(rows, "guest_toolchain_hashes")["stdout"])
    _require(
        set(toolchain)
        == {
            "/usr/local/bin/codex",
            "/usr/local/bin/codex-code-mode-host",
            "/opt/skilleval/openbench_gateway.py",
        }
        and all(
            isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value)
            for value in toolchain.values()
        ),
        "native toolchain identity incomplete",
    )
    loop = _step(rows, "native_authenticated_loop")
    _guest_invocation(
        loop,
        runtime,
        machine,
        timeout=mode["native_timeout_seconds"] + 15,
        command=["python3", "/run/qualification/runner.py"],
    )
    parsed = parse_codex_jsonl(loop["stdout"])
    summary = policy["native_control_summary"](loop["stdout"])
    _require(
        report.get("native_control") == summary
        and parsed["execution_status"] == "completed"
        and summary["successful_command_marker"] is True
        and summary["parse_error_count"] == 0
        and summary["error_count"] == 0,
        "native events do not establish successful command",
    )
    result = _json(_step(rows, "capture_native-result.json")["stdout"])
    _require(
        result.get("native_tool_ran") is True
        and result.get("timed_out") is False
        and type(result.get("returncode")) is int
        and result["returncode"] == 0
        and result.get("stdout_bytes") == len(loop["stdout"].encode())
        and result.get("stderr_bytes") == len(loop["stderr"].encode()),
        "native independent file effect failed",
    )
    host = report["host_controls"]
    _require(
        set(host)
        == {
            "filesystem_positive",
            "filesystem_unchanged",
            "tcp_positive",
            "external_tcp_positive",
            "external_peer",
            "no_guest_tcp_connections",
        }
        and all(value is True for key, value in host.items() if key != "external_peer"),
        "host positive/denial controls failed",
    )
    network = targets["network"]
    _require(
        len(network) == 5
        and network[0] == [host["external_peer"], 443]
        and [item[0] for item in network[1:]]
        == ["127.0.0.1", "100.96.0.1", "192.168.127.1", "10.0.2.2"]
        and all(type(item[1]) is int and 1 <= item[1] <= 65535 for item in network)
        and len({item[1] for item in network[1:]}) == 1,
        "network denial targets incomplete",
    )
    expected_controls = [
        {"control": "filesystem_" + method, "positive": True, "denied": True}
        for method in ("direct", "symlink", "subprocess")
    ]
    expected_controls += [{"control": "host_write", "denied": True}]
    expected_controls += [
        {"control": "network", "host": host, "port": port, "denied": True} for host, port in network
    ]
    expected_controls += [{"control": "relay_loopback", "positive": True}]
    _require(
        _same(_json(_step(rows, "capture_controls.json")["stdout"]), expected_controls),
        "guest positive/denial controls failed",
    )
    relay = [
        _json(line) for line in _step(rows, "capture_relay-stdout.jsonl")["stdout"].splitlines()
    ]
    _require(
        relay
        == [
            {"event": "ready", "role": "relay", "port": 8765},
            {"event": "stopped", "role": "relay", "clean": True},
        ]
        and not _step(rows, "capture_relay-stderr.log")["stdout"].strip(),
        "relay lifecycle failed",
    )
    receipts = report["broker_receipts"]
    requests = [row for row in receipts if row.get("event") == "request"]
    _require(
        2 <= len(requests) <= mode["max_requests"]
        and report.get("request_count") == len(requests)
        and report.get("request_count_basis") == "broker-request-receipts"
        and len(receipts) == len(requests) + 2
        and receipts[0] == {"event": "ready", "role": "broker"}
        and receipts[-1] == {"event": "stopped", "clean": True},
        "provider requests/lifecycle missing",
    )
    for index, row in enumerate(requests, 1):
        _require(
            row.get("request_id") == index
            and row.get("outcome") == "complete"
            and row.get("upstream_status") == 200
            and row.get("model") == MODEL
            and row.get("effort") == EFFORT
            and row.get("timeout_seconds") == mode["request_timeout_seconds"]
            and isinstance(row.get("upstream_peer", {}).get("ip"), str)
            and row["upstream_peer"].get("port") == 443,
            "provider stream incomplete",
        )
    _require(
        report.get("broker_clean") is True
        and report.get("host_listener_stopped") is True
        and report.get("inference_rejections") == [],
        "broker cleanup/policy failed",
    )


def _quiet(value: dict[str, Any]) -> None:
    _require(
        value.get("stopped") is True
        and type(value.get("before")) is int
        and value["before"] >= 3
        and value.get("after") == value["before"]
        and value.get("quiet_seconds", 0) >= 1
        and value.get("observer_alive") is True,
        "heartbeat termination lacks observation",
    )


def _lifecycle(
    report: dict[str, Any], path: Path, identity: dict[str, Any], image: dict[str, Any]
) -> dict[str, str]:
    _require(
        report.get("status") == "lifecycle_controls_passed"
        and report.get("image_sha256") == image["sha256"],
        "lifecycle control failed",
    )
    controls = report["controls"]
    positive = controls["markers_positive"]
    _require(
        positive.get("both_known_positive") is True and positive.get("paths") == list(MARKERS),
        "workspace/session markers lack known positive",
    )
    timed = controls["execution_timeout"]
    _require(
        timed.get("heartbeat_known_positive") is True
        and type(timed["native_returncode"]) is int
        and timed["native_returncode"] != 0
        and 2.5 <= timed["elapsed_seconds"] < 8
        and timed.get("controller_timed_out") is False
        and timed.get("native_timeout_diagnostic") is True
        and type(timed.get("detached_worker_continued_after_native_timeout")) is bool,
        "execution timeout control incomplete",
    )
    _quiet(controls["whole_machine_termination"])
    fresh = controls["fresh_workspace_session"]
    _require(
        fresh.get("both_absent") is True and fresh.get("observed") == dict.fromkeys(MARKERS, False),
        "fresh workspace/session control incomplete",
    )
    _require(
        report.get("observer_cleanup") == {"thread_stopped": True, "socket_closed": True}
        and report.get("observer_directory_removed") is True,
        "lifecycle observer cleanup failed",
    )
    artifacts: dict[str, str] = {}
    for role in ("timeout", "fresh", "sigterm", "sigkill"):
        ticket_path = path.parent / role / "recovery.json"
        ticket = _ticket(ticket_path, identity, image)
        config = ticket["config"]
        _require(
            set(config)
            == {
                "cpus",
                "memory_mib",
                "guest_network",
                "host_mounts",
                "secrets",
                "observer_socket",
                "guest_observer_socket",
            }
            and isinstance(config["observer_socket"], str)
            and config["guest_observer_socket"] == "/run/lifecycle.sock",
            "lifecycle socket policy",
        )
        artifacts[str(ticket_path)] = file_digest(ticket_path)
        if role in ("timeout", "fresh"):
            _require(ticket in report["sessions"], "lifecycle session differs from artifact")
            rows = _steps(ticket)
            if role == "timeout":
                marker_step = _step(rows, "workspace_session_known_positive")
                _guest_invocation(
                    marker_step,
                    identity["runtime"]["binary"],
                    ticket["machine_name"],
                    timeout=30,
                    python_fragments=(*MARKERS, "write_text", "read_text"),
                )
                markers = _json(marker_step["stdout"])
                _require(
                    set(markers) == set(MARKERS)
                    and len(set(markers.values())) == 1
                    and all(isinstance(value, str) and value for value in markers.values()),
                    "marker positive artifact differs",
                )
                timeout = rows["execution_timeout_with_detached_worker"]
                _guest_invocation(
                    timeout,
                    identity["runtime"]["binary"],
                    ticket["machine_name"],
                    timeout=3,
                    python_fragments=(
                        "subprocess.Popen",
                        "start_new_session=True",
                        "sleep(300)",
                        "/run/lifecycle.sock",
                    ),
                )
                _require(
                    type(timeout["returncode"]) is int
                    and timeout["returncode"] != 0
                    and timeout["timed_out"] is False
                    and timeout["output_truncated"] is False
                    and timeout["pipe_drain_incomplete"] is False
                    and timeout["returncode"] == timed["native_returncode"]
                    and bool(
                        re.search(
                            r"timed?\s*out|timeout",
                            timeout["stdout"] + timeout["stderr"],
                            re.IGNORECASE,
                        )
                    ),
                    "native timeout diagnostic artifact differs",
                )
            else:
                absence_step = _step(rows, "fresh_workspace_session_absence")
                _guest_invocation(
                    absence_step,
                    identity["runtime"]["binary"],
                    ticket["machine_name"],
                    timeout=30,
                    python_fragments=(*MARKERS, ".exists()"),
                )
                observed = _json(absence_step["stdout"])
                _require(observed == dict.fromkeys(MARKERS, False), "fresh marker artifact differs")
        else:
            signal = controls[role.upper()]
            _require(
                signal.get("signal") == role.upper()
                and signal.get("admitted") is False
                and signal.get("heartbeat_known_positive") is True
                and signal.get("heartbeat_survived_controller") is True
                and signal.get("cleanup_complete") is True
                and signal.get("idempotent_recovery") is True
                and signal.get("ticket") == str(ticket_path)
                and signal.get("controller_returncode") == (-9 if role == "sigkill" else 143),
                "controller interruption/recovery control incomplete",
            )
            _quiet(signal["heartbeat_after_recovery"])
    return artifacts


def _validate(inputs: dict[str, str], runtime: str, catalog: Path) -> dict[str, Any]:
    identity = snapshot_identity(runtime)
    paths = {role: Path(value) for role, value in inputs.items()}
    _require(set(paths) == {"plan", "offline", "native", "lifecycle"}, "evidence roles differ")
    raw_documents = {role: _read(path) for role, path in paths.items()}
    documents = {role: _json(raw) for role, raw in raw_documents.items()}
    _require(
        all(isinstance(value, dict) for value in documents.values()), "expected evidence objects"
    )
    plan = documents["plan"]
    _require(
        plan["reconstruct_git"] is True
        and plan["image"] is None
        and isinstance(plan["image_archive"], dict),
        "task requires local image and native Git",
    )
    rebuilt = plan_qualification(
        Path(plan["bundle"]),
        None,
        [item["argv"] for item in plan["commands"]],
        plan["cpus"],
        plan["memory_mib"],
        image_archive=Path(plan["image_archive"]["path"]),
        image_sha256=plan["image_archive"]["sha256"],
        reconstruct_git=True,
    )
    _require(_same(plan, rebuilt), "task plan or workspace changed")
    image = plan["image_archive"]
    artifacts: dict[str, str] = {}
    for role in ("offline", "native", "lifecycle"):
        report = documents[role]
        _require(
            _same(report.get("identity"), identity) and report.get("identity_changed") is False,
            role + " report lacks exact current identity capture",
        )
        _require(
            report.get("admitted") is False
            and type(report.get("schema_version")) is int
            and report["schema_version"] == 1,
            "unsupported qualification receipt",
        )
    for role in ("offline", "native"):
        report, path = documents[role], paths[role]
        for key in (
            "cleanup_complete",
            "runtime_home_removed",
            "machine_observer_positive",
            "machine_observed_absent",
        ):
            _require(report.get(key) is True, role + " cleanup incomplete")
        recorded = report["artifacts"]
        required = {"recovery.json"} | (
            {"runtime-state/tooling.tar"} if role == "native" else set()
        )
        _require(set(recorded) == required, "report artifact inventory differs")
        for name, digest in recorded.items():
            target = path.parent / name
            _require(file_digest(target) == digest, "report artifact digest differs")
            artifacts[str(target)] = digest
        ticket = _ticket(path.parent / "recovery.json", identity, image)
        _require(ticket["machine_name"] == report["machine_name"], "report machine differs")
        _require(
            report.get("runtime_sha256") == identity["runtime"]["files"]["smolvm"]["sha256"],
            "report runtime digest differs",
        )
        for key in ("runtime_binary", "runtime_home", "runtime_state"):
            if key in report:
                expected = identity["runtime"]["binary"] if key == "runtime_binary" else ticket[key]
                _require(report[key] == expected, "report recovery namespace differs")
        _require(_same(report.get("cleanup"), ticket["cleanup"]), "report cleanup differs")
        config = ticket["config"]
        rows = _steps(report)
        runtime_path = identity["runtime"]["binary"]
        _require(
            _step(rows, "runtime_version")["argv"] == [runtime_path, "--version"]
            and rows["runtime_version"]["stdout"].strip() == "smolvm 1.32.3",
            "observed runtime version differs",
        )
        _require(
            _step(rows, "create")["argv"]
            == _create_invocation(
                runtime_path,
                ticket["machine_name"],
                path.parent / "runtime-state/image.tar",
                config,
            )
            and _step(rows, "start")["argv"]
            == [runtime_path, "machine", "start", "--name", ticket["machine_name"]],
            "actual VM invocation differs",
        )
        if role == "offline":
            _require(config == {"cpus": 2, "memory_mib": 2048, **plan["policy"]}, "offline policy")
        else:
            _require(
                config == report["vm_config"]
                and set(config)
                == {
                    "cpus",
                    "memory_mib",
                    "guest_network",
                    "host_mounts",
                    "secrets",
                    "socket_mounts",
                }
                and len(config["socket_mounts"]) == 1
                and config["socket_mounts"][0]["guest"] == "/run/inference.sock",
                "native socket policy",
            )
    offline = documents["offline"]
    _require(
        offline.get("status") == "offline_passed"
        and offline.get("plan") == plan
        and offline.get("workspace") == plan["workspace"],
        "offline task evidence differs",
    )
    rows = _steps(offline)
    for label in (
        "runtime_version",
        "create",
        "start",
        "copy_source",
        "prepare",
        "extract_source",
        "git_version",
        "git_history",
        "git_worktree",
        "git_objects",
    ):
        _step(rows, label)
    runtime_path, machine = identity["runtime"]["binary"], offline["machine_name"]
    _require(
        rows["copy_source"]["argv"]
        == [
            runtime_path,
            "machine",
            "cp",
            str(paths["offline"].parent / "runtime-state/task.tar"),
            machine + ":/workspace/task.tar",
        ],
        "task source transfer invocation differs",
    )
    _guest_invocation(
        rows["prepare"], runtime_path, machine, command=["mkdir", "-p", "/workspace/task"]
    )
    guest_commands = {
        "extract_source": ["tar", "-xf", "/workspace/task.tar", "-C", "/workspace/task"],
        "git_version": ["git", "--version"],
        "git_history": ["git", "rev-parse", "HEAD", "HEAD^", "candidate^{tree}", "base^{tree}"],
        "git_worktree": ["git", "status", "--porcelain"],
        "git_objects": ["git", "fsck", "--no-reflogs"],
    }
    for label, command in guest_commands.items():
        _guest_invocation(
            rows[label], runtime_path, machine, workdir="/workspace/task", command=command
        )
    workspace = plan["workspace"]
    _require(
        rows["git_history"]["stdout"].splitlines()
        == [
            workspace[key]
            for key in ("candidate_commit", "base_commit", "candidate_tree", "base_tree")
        ]
        and not rows["git_worktree"]["stdout"].strip(),
        "guest Git workspace differs",
    )
    for index, control in enumerate(plan["commands"]):
        row = _step(rows, f"project_control_{index}")
        _require(
            row["argv"]
            == [
                identity["runtime"]["binary"],
                "machine",
                "exec",
                "--name",
                offline["machine_name"],
                "--workdir",
                "/workspace/task",
                "--timeout",
                str(control["timeout_seconds"]) + "s",
                "--",
                *control["argv"],
            ],
            "task control argv differs",
        )
    _require(
        _same(documents["native"]["image_archive"], image)
        and documents["native"].get("image_sha256") == image["sha256"],
        "native image differs",
    )
    _native(
        documents["native"],
        _native_policy(),
        catalog,
        paths["native"].parent / "runtime-state/tooling.tar",
    )
    artifacts.update(_lifecycle(documents["lifecycle"], paths["lifecycle"], identity, image))
    file_hashes = {role: hashlib.sha256(raw).hexdigest() for role, raw in raw_documents.items()}
    _require(
        all(file_digest(paths[role]) == digest for role, digest in file_hashes.items()),
        "evidence changed during admission",
    )
    _require(_same(snapshot_identity(runtime), identity), "identity changed during admission")
    return {
        "identity": identity,
        "policy_sha256": file_digest(Path(__file__)),
        "files": file_hashes,
        "artifacts": artifacts,
        "catalog_sha256": file_digest(catalog),
    }


def _outcome(snapshot: Path, digest: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "environment_admitted",
        "scope": "bounded-native-control",
        "scored_study_admitted": False,
        "task_quality": "unassessed",
        "grader": "unassessed",
        "skill_effectiveness": "unassessed",
        "snapshot": str(snapshot),
        "sha256": digest,
    }


def seal_admission(
    plan_path: Path,
    offline_path: Path,
    native_path: Path,
    lifecycle_path: Path,
    catalog: Path,
    runtime_binary: str,
    output: Path,
) -> dict[str, Any]:
    """Validate fresh evidence and exclusively write its explicit admission seal."""
    try:
        inputs = {
            role: str(path.absolute())
            for role, path in (
                ("plan", plan_path),
                ("offline", offline_path),
                ("native", native_path),
                ("lifecycle", lifecycle_path),
            )
        }
        catalog = catalog.absolute()
        bindings = _validate(inputs, runtime_binary, catalog)
        destination = output.resolve()
        bundle_root = Path(_document(Path(inputs["plan"]))["bundle"]).resolve()
        runtime_root = Path(bindings["identity"]["runtime"]["binary"]).parent.resolve()
        _require(
            not any(
                destination == root or destination.is_relative_to(root)
                for root in (bundle_root, runtime_root)
            ),
            "snapshot destination overlaps a bound input inventory",
        )
        snapshot = {
            "schema_version": 1,
            "policy": POLICY,
            "inputs": inputs,
            "runtime_binary": bindings["identity"]["runtime"]["binary"],
            "catalog": str(catalog),
            "bindings": bindings,
        }
        raw = (json.dumps(snapshot, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        return _outcome(output.absolute(), hashlib.sha256(raw).hexdigest())
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError):
        raise SkillEvalError(
            "environment admission refused: invalid or unavailable evidence"
        ) from None


def check_admission(snapshot: Path, expected_sha256: str) -> dict[str, Any]:
    """Require an externally retained seal digest, then revalidate every binding."""
    try:
        raw = _read(snapshot)
        _require(
            bool(re.fullmatch(r"[a-f0-9]{64}", expected_sha256))
            and hashlib.sha256(raw).hexdigest() == expected_sha256,
            "snapshot digest differs",
        )
        value = _json(raw)
        _require(
            set(value)
            == {"schema_version", "policy", "inputs", "runtime_binary", "catalog", "bindings"}
            and value["schema_version"] == 1
            and value["policy"] == POLICY,
            "unsupported admission policy",
        )
        current = _validate(value["inputs"], value["runtime_binary"], Path(value["catalog"]))
        _require(_same(current, value["bindings"]), "sealed evidence, artifact, or policy changed")
        return _outcome(snapshot.absolute(), expected_sha256)
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError):
        raise SkillEvalError(
            "environment admission refused: invalid or unavailable evidence"
        ) from None
