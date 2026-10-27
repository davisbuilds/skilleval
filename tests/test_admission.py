"""Filesystem controls only; synthetic receipts never prove actual VM isolation."""

import hashlib
import io
import json
import signal
import tarfile
from pathlib import Path
from typing import Any

import pytest
from test_qualification import bundle_at, docker_archive

from skilleval import admission
from skilleval.errors import SkillEvalError
from skilleval.identity import file_digest, snapshot_identity
from skilleval.lifecycle import _environment
from skilleval.qualification import plan_qualification


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True))


def step(label: str, argv: list[str], stdout: str = "") -> dict[str, Any]:
    return {
        "label": label,
        "argv": argv,
        "stdout": stdout,
        "stderr": "",
        "returncode": 0,
        "timed_out": False,
        "output_truncated": False,
        "pipe_drain_incomplete": False,
    }


def guest_argv(
    runtime: str, machine: str, command: list[str], timeout: int = 120, workdir: str | None = None
) -> list[str]:
    return (
        [runtime, "machine", "exec", "--name", machine]
        + (["--workdir", workdir] if workdir else [])
        + ["--timeout", f"{timeout}s", "--", *command]
    )


def ticket_at(
    root: Path, identity: dict[str, Any], image: dict[str, Any], config: dict[str, Any], number: int
) -> dict[str, Any]:
    machine = "skilleval-lifecycle-" + f"{number:032x}"
    runtime = identity["runtime"]["binary"]
    ticket = {
        "schema_version": 1,
        "kind": "skilleval-lifecycle-recovery",
        "admitted": False,
        "identity": identity,
        "identity_changed": False,
        "image": image,
        "output": str(root),
        "runtime_state": str(root / "runtime-state"),
        "runtime_home": "/tmp/synthetic",
        "machine_name": machine,
        "config": config,
        "env": _environment(root / "runtime-state", Path("/tmp/synthetic")),
        "config_sha256": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        "phase": "recovered",
        "create_attempted": True,
        "observer_positive": True,
        "observed_absent": True,
        "cleanup_complete": True,
        "runtime_home_removed": True,
        "steps": [
            step(
                "inventory_known_present",
                [runtime, "machine", "ls", "--json"],
                json.dumps([{"name": machine}]),
            ),
            step("recovery_inventory_after", [runtime, "machine", "ls", "--json"], "[]"),
        ],
        "cleanup": [
            dict(
                action=action,
                **step(
                    "recovery_" + action,
                    [runtime, "machine", action, "--name", machine]
                    + (["--force"] if action == "delete" else []),
                ),
            )
            for action in ("stop", "delete")
        ],
    }
    if "observer_socket" in config:
        ticket["steps"][:0] = [
            step(
                "create",
                [
                    runtime,
                    "machine",
                    "create",
                    "--name",
                    machine,
                    "--image",
                    str(root / "runtime-state/image.tar"),
                    "--cpus",
                    "2",
                    "--mem",
                    "2048",
                    "--mount-socket",
                    config["observer_socket"] + ":" + config["guest_observer_socket"],
                    "--",
                    "sleep",
                    "infinity",
                ],
            ),
            step("start", [runtime, "machine", "start", "--name", machine]),
        ]
    write(root / "recovery.json", ticket)
    return ticket


@pytest.fixture
def evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    runtime = tmp_path / "runtime" / "smolvm"
    runtime.parent.mkdir()
    runtime.write_text("synthetic launcher, never executed")
    runtime.chmod(0o755)
    for name in ("smolvm-bin", "storage-template.ext4", "overlay-template.ext4"):
        (runtime.parent / name).write_bytes(b"synthetic")
    for name in ("lib", "agent-rootfs"):
        (runtime.parent / name).mkdir()
    (runtime.parent / "lib" / "dependency.dylib").write_bytes(b"dependency")
    image_path, image_sha = docker_archive(tmp_path)
    plan = plan_qualification(
        bundle_at(tmp_path, True),
        None,
        [["python3", "-m", "pytest"]],
        image_archive=image_path,
        image_sha256=image_sha,
        reconstruct_git=True,
    )
    write(tmp_path / "plan.json", plan)
    identity = snapshot_identity(str(runtime))
    image = plan["image_archive"]
    config = {
        "cpus": 2,
        "memory_mib": 2048,
        "guest_network": False,
        "host_mounts": [],
        "secrets": [],
    }
    native_config = {
        **config,
        "socket_mounts": [
            {"host": "/tmp/synthetic/inference.sock", "guest": "/run/inference.sock"}
        ],
    }
    observer_config = {
        **config,
        "observer_socket": "/tmp/synthetic/observer.sock",
        "guest_observer_socket": "/run/lifecycle.sock",
    }
    offline_ticket = ticket_at(tmp_path / "offline", identity, image, config, 1)
    native_ticket = ticket_at(tmp_path / "native", identity, image, native_config, 2)
    runtime_arg = str(runtime)
    offline_rows = [
        step(label, [runtime_arg, "synthetic", label])
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
        )
    ]
    machine = offline_ticket["machine_name"]
    commands = {
        "prepare": ["mkdir", "-p", "/workspace/task"],
        "extract_source": ["tar", "-xf", "/workspace/task.tar", "-C", "/workspace/task"],
        "git_version": ["git", "--version"],
        "git_history": ["git", "rev-parse", "HEAD", "HEAD^", "candidate^{tree}", "base^{tree}"],
        "git_worktree": ["git", "status", "--porcelain"],
        "git_objects": ["git", "fsck", "--no-reflogs"],
    }
    for row in offline_rows:
        if row["label"] in commands:
            row["argv"] = guest_argv(
                runtime_arg,
                machine,
                commands[row["label"]],
                workdir=None if row["label"] == "prepare" else "/workspace/task",
            )
        elif row["label"] == "copy_source":
            row["argv"] = [
                runtime_arg,
                "machine",
                "cp",
                str(tmp_path / "offline/runtime-state/task.tar"),
                machine + ":/workspace/task.tar",
            ]
    offline_rows[0]["stdout"] = "smolvm 1.32.3\n"
    workspace = plan["workspace"]
    next(row for row in offline_rows if row["label"] == "git_history")["stdout"] = "\n".join(
        workspace[key] for key in ("candidate_commit", "base_commit", "candidate_tree", "base_tree")
    )
    offline_rows.append(
        step(
            "project_control_0",
            [
                runtime_arg,
                "machine",
                "exec",
                "--name",
                offline_ticket["machine_name"],
                "--workdir",
                "/workspace/task",
                "--timeout",
                "120s",
                "--",
                "python3",
                "-m",
                "pytest",
            ],
        )
    )
    common = {
        "schema_version": 1,
        "admitted": False,
        "identity": identity,
        "identity_changed": False,
        "cleanup_complete": True,
        "runtime_sha256": file_digest(runtime),
        "runtime_home_removed": True,
        "machine_observer_positive": True,
        "machine_observed_absent": True,
    }
    offline = {
        **common,
        "status": "offline_passed",
        "plan": plan,
        "workspace": workspace,
        "machine_name": offline_ticket["machine_name"],
        "steps": offline_rows,
        "artifacts": {"recovery.json": file_digest(tmp_path / "offline/recovery.json")},
    }
    write(tmp_path / "offline/report.json", offline)
    catalog = tmp_path / "catalog.json"
    catalog.write_text('{"synthetic_catalog":true}')
    policy = admission._native_policy()
    # A synthetic catalog replaces the costly external pinned artifact, while
    # all receipt/config hashing and admission semantics remain real.
    policy["CATALOG_SHA256"] = file_digest(catalog)
    monkeypatch.setattr(admission, "_native_policy", lambda: policy)
    mode = policy["mode_settings"](True)
    targets = {
        "host_canary": str(tmp_path / "native/host-canary"),
        "network": [
            ["93.184.216.34", 443],
            ["127.0.0.1", 12345],
            ["100.96.0.1", 12345],
            ["192.168.127.1", 12345],
            ["10.0.2.2", 12345],
        ],
    }
    entries = {
        "models.json": catalog.read_bytes(),
        "config.toml": admission.codex_provider_config(
            8765, Path("/run/qualification/models.json")
        ).encode(),
        "runner.py": policy["GUEST_RUNNER"].encode(),
        "mode.json": json.dumps(mode).encode(),
        "targets.json": json.dumps(targets).encode(),
    }
    tooling = tmp_path / "native/runtime-state/tooling.tar"
    tooling.parent.mkdir()
    with tarfile.open(tooling, "w") as archive:
        for name, raw in entries.items():
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
    command = {
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "status": "completed",
            "exit_code": 0,
            "aggregated_output": "NATIVE_TOOL_OK",
        },
    }
    events = json.dumps(command) + "\n" + json.dumps({"type": "turn.completed"})
    controls = [
        {"control": "filesystem_" + method, "positive": True, "denied": True}
        for method in ("direct", "symlink", "subprocess")
    ]
    controls += [{"control": "host_write", "denied": True}]
    controls += [
        {"control": "network", "host": host, "port": port, "denied": True}
        for host, port in targets["network"]
    ]
    controls += [{"control": "relay_loopback", "positive": True}]
    native_rows = [
        step("runtime_version", [runtime_arg, "--version"], "smolvm 1.32.3\n"),
        step("codex_version", [runtime_arg, "codex", "--version"], "codex-cli 0.164.0\n"),
        step("native_authenticated_loop", [runtime_arg, "runner"], events),
        step(
            "guest_toolchain_hashes",
            [runtime_arg, "hashes"],
            json.dumps(
                {
                    "/usr/local/bin/codex": "a" * 64,
                    "/usr/local/bin/codex-code-mode-host": "b" * 64,
                    "/opt/skilleval/openbench_gateway.py": "c" * 64,
                }
            ),
        ),
        step("capture_controls.json", [runtime_arg, "capture"], json.dumps(controls)),
        step(
            "capture_native-result.json",
            [runtime_arg, "capture"],
            json.dumps(
                {
                    "returncode": 0,
                    "stdout_bytes": len(events.encode()),
                    "stderr_bytes": 0,
                    "timed_out": False,
                    "native_tool_ran": True,
                }
            ),
        ),
        step(
            "capture_relay-stdout.jsonl",
            [runtime_arg, "capture"],
            '{"event":"ready","role":"relay","port":8765}\n'
            '{"event":"stopped","role":"relay","clean":true}\n',
        ),
        step("capture_relay-stderr.log", [runtime_arg, "capture"]),
    ]
    machine = native_ticket["machine_name"]
    native_commands = {
        "codex_version": ["codex", "--version"],
        "guest_toolchain_hashes": [
            "python3",
            "-c",
            "import hashlib; "
            "names=['/usr/local/bin/codex','/usr/local/bin/codex-code-mode-host',"
            "'/opt/skilleval/openbench_gateway.py']; hashlib.sha256",
        ],
        "native_authenticated_loop": ["python3", "/run/qualification/runner.py"],
    }
    for row in native_rows:
        if row["label"] in native_commands:
            row["argv"] = guest_argv(
                runtime_arg,
                machine,
                native_commands[row["label"]],
                timeout=255 if row["label"] == "native_authenticated_loop" else 120,
            )
        elif row["label"].startswith("capture_"):
            row["argv"] = guest_argv(
                runtime_arg, machine, ["cat", "/run/qualification/" + row["label"][8:]]
            )
    native_rows += [
        step(
            "copy_tooling",
            [runtime_arg, "machine", "cp", str(tooling), machine + ":/run/tooling.tar"],
        ),
        step(
            "prepare_tooling",
            guest_argv(
                runtime_arg,
                machine,
                [
                    "python3",
                    "-c",
                    "import tarfile; tarfile.open('/run/tooling.tar')"
                    ".extractall('/run/qualification',filter='data')",
                ],
            ),
        ),
    ]
    native = {
        **common,
        "status": "authenticated_native_passed",
        "credential_mode": "existing-host-chatgpt-login",
        "image_archive": image,
        "image_sha256": image_sha,
        "machine_name": native_ticket["machine_name"],
        "codex_identity": admission.codex_version_identity("codex-cli 0.164.0"),
        "codex_version": "0.164.0",
        "catalog_sha256": file_digest(catalog),
        "model": admission.MODEL,
        "effort": admission.EFFORT,
        "probe_sha256": identity["sources"]["scripts/qualify-native.py"],
        "gateway_sha256": identity["sources"]["src/skilleval/gateway.py"],
        "gateway_vendor_sha256": identity["sources"]["src/skilleval/_vendor/openbench_gateway.py"],
        "mode": mode,
        "vm_config": native_config,
        "tooling_sha256": {name: hashlib.sha256(raw).hexdigest() for name, raw in entries.items()},
        "broker_limits": {
            "max_requests": 4,
            "max_body_bytes": 4194304,
            "timeout_seconds": 90,
            "max_concurrency": 1,
            "lifetime_seconds": 300,
        },
        "broker_clean": True,
        "host_listener_stopped": True,
        "inference_rejections": [],
        "host_controls": {
            "filesystem_positive": True,
            "filesystem_unchanged": True,
            "tcp_positive": True,
            "external_tcp_positive": True,
            "external_peer": "93.184.216.34",
            "no_guest_tcp_connections": True,
        },
        "native_control": policy["native_control_summary"](events),
        "steps": native_rows,
        "broker_receipts": [
            {"event": "ready", "role": "broker"},
            *[
                {
                    "event": "request",
                    "request_id": i,
                    "outcome": "complete",
                    "upstream_status": 200,
                    "model": admission.MODEL,
                    "effort": admission.EFFORT,
                    "timeout_seconds": 90,
                    "upstream_peer": {"ip": "93.184.216.34", "port": 443},
                }
                for i in (1, 2)
            ],
            {"event": "stopped", "clean": True},
        ],
        "request_count": 2,
        "request_count_basis": "broker-request-receipts",
        "artifacts": {
            "recovery.json": file_digest(tmp_path / "native/recovery.json"),
            "runtime-state/tooling.tar": file_digest(tooling),
        },
    }
    offline["cleanup"] = offline_ticket["cleanup"]
    native["cleanup"] = native_ticket["cleanup"]
    for role, report, ticket in (
        ("offline", offline, offline_ticket),
        ("native", native, native_ticket),
    ):
        rows = report["steps"]
        runtime_row = next(row for row in rows if row["label"] == "runtime_version")
        runtime_row["argv"] = [runtime_arg, "--version"]
        machine = ticket["machine_name"]
        argv = [
            runtime_arg,
            "machine",
            "create",
            "--name",
            machine,
            "--image",
            str(tmp_path / role / "runtime-state/image.tar"),
            "--cpus",
            "2",
            "--mem",
            "2048",
        ]
        if role == "native":
            argv += [
                "--mount-socket",
                "/tmp/synthetic/inference.sock:/run/inference.sock",
                "--",
                "sleep",
                "infinity",
            ]
            rows += [
                step("create", argv),
                step("start", [runtime_arg, "machine", "start", "--name", machine]),
            ]
        else:
            next(row for row in rows if row["label"] == "create")["argv"] = argv
            next(row for row in rows if row["label"] == "start")["argv"] = [
                runtime_arg,
                "machine",
                "start",
                "--name",
                machine,
            ]
    write(tmp_path / "offline/report.json", offline)
    write(tmp_path / "native/report.json", native)
    quiet = {"before": 10, "after": 10, "quiet_seconds": 1, "stopped": True, "observer_alive": True}
    tickets = {
        role: ticket_at(tmp_path / "lifecycle" / role, identity, image, observer_config, n)
        for n, role in enumerate(("timeout", "fresh", "sigterm", "sigkill"), 3)
    }
    positive = step(
        "workspace_session_known_positive",
        guest_argv(
            runtime_arg,
            tickets["timeout"]["machine_name"],
            ["python3", "-c", repr(list(admission.MARKERS)) + "; write_text; read_text"],
            timeout=30,
        ),
        json.dumps(dict.fromkeys(admission.MARKERS, "synthetic-marker")),
    )
    timeout_step = step(
        "execution_timeout_with_detached_worker",
        guest_argv(
            runtime_arg,
            tickets["timeout"]["machine_name"],
            [
                "python3",
                "-c",
                "subprocess.Popen; start_new_session=True; sleep(300); /run/lifecycle.sock",
            ],
            timeout=3,
        ),
        "Command timed out",
    )
    timeout_step["returncode"] = 124
    tickets["timeout"]["steps"] += [positive, timeout_step]
    tickets["fresh"]["steps"].append(
        step(
            "fresh_workspace_session_absence",
            guest_argv(
                runtime_arg,
                tickets["fresh"]["machine_name"],
                ["python3", "-c", repr(list(admission.MARKERS)) + "; .exists()"],
                timeout=30,
            ),
            json.dumps(dict.fromkeys(admission.MARKERS, False)),
        )
    )
    for role in ("timeout", "fresh"):
        write(tmp_path / "lifecycle" / role / "recovery.json", tickets[role])
    lifecycle = {
        "schema_version": 1,
        "status": "lifecycle_controls_passed",
        "admitted": False,
        "identity": identity,
        "identity_changed": False,
        "image_sha256": image_sha,
        "observer_cleanup": {"thread_stopped": True, "socket_closed": True},
        "observer_directory_removed": True,
        "sessions": [tickets["timeout"], tickets["fresh"]],
        "controls": {
            "markers_positive": {"paths": list(admission.MARKERS), "both_known_positive": True},
            "execution_timeout": {
                "native_returncode": 124,
                "controller_timed_out": False,
                "elapsed_seconds": 3.1,
                "heartbeat_known_positive": True,
                "native_timeout_diagnostic": True,
                "detached_worker_continued_after_native_timeout": True,
            },
            "whole_machine_termination": quiet,
            "fresh_workspace_session": {
                "observed": dict.fromkeys(admission.MARKERS, False),
                "both_absent": True,
            },
            **{
                name: {
                    "signal": name,
                    "admitted": False,
                    "heartbeat_known_positive": True,
                    "controller_returncode": -signal.SIGKILL if name == "SIGKILL" else 143,
                    "heartbeat_survived_controller": True,
                    "cleanup_complete": True,
                    "ticket": str(tmp_path / "lifecycle" / name.lower() / "recovery.json"),
                    "heartbeat_after_recovery": quiet,
                    "idempotent_recovery": True,
                }
                for name in ("SIGTERM", "SIGKILL")
            },
        },
    }
    write(tmp_path / "lifecycle/report.json", lifecycle)
    return {
        "root": tmp_path,
        "plan_path": tmp_path / "plan.json",
        "offline_path": tmp_path / "offline/report.json",
        "native_path": tmp_path / "native/report.json",
        "lifecycle_path": tmp_path / "lifecycle/report.json",
        "catalog": catalog,
        "runtime_binary": str(runtime),
        "output": tmp_path / "admission.json",
    }


def seal(evidence: dict[str, Any]) -> dict[str, Any]:
    return admission.seal_admission(
        **{key: value for key, value in evidence.items() if key != "root"}
    )


def test_complete_fixture_admits_only_bounded_environment(evidence: dict[str, Any]) -> None:
    result = seal(evidence)
    assert result["status"] == "environment_admitted"
    assert result["scored_study_admitted"] is False
    assert result["grader"] == "unassessed"
    assert admission.check_admission(evidence["output"], result["sha256"]) == result
    with pytest.raises(SkillEvalError):
        seal(evidence)


def test_passed_status_cannot_override_failed_native_effect(evidence: dict[str, Any]) -> None:
    path = evidence["native_path"]
    value = json.loads(path.read_text())
    next(row for row in value["steps"] if row["label"] == "capture_native-result.json")[
        "stdout"
    ] = json.dumps({"returncode": 0, "timed_out": False, "native_tool_ran": False})
    write(path, value)
    with pytest.raises(SkillEvalError, match=r"native.*effect"):
        seal(evidence)


@pytest.mark.parametrize(
    ("role", "fields", "value"),
    [
        ("native", ("broker_receipts",), []),
        ("native", ("credential_mode",), "synthetic"),
        ("native", ("request_count",), 0),
        ("native", ("image_sha256",), "f" * 64),
        ("native", ("native_control", "successful_command_marker"), False),
        ("native", ("host_controls", "tcp_positive"), False),
        ("native", ("broker_limits", "max_requests"), 8),
        ("native", ("codex_identity", "observed_version"), "0.165.0"),
        ("native", ("identity_changed",), True),
        ("native", ("image_archive", "sha256"), "f" * 64),
        ("offline", ("workspace", "patch_sha256"), "f" * 64),
        ("offline", ("machine_observer_positive",), False),
        ("lifecycle", ("controls", "markers_positive", "both_known_positive"), False),
        ("lifecycle", ("controls", "execution_timeout", "controller_timed_out"), True),
        ("lifecycle", ("controls", "SIGKILL", "heartbeat_survived_controller"), False),
        ("lifecycle", ("observer_cleanup", "thread_stopped"), False),
    ],
)
def test_refuses_semantically_failed_receipt_even_when_status_still_passes(
    evidence: dict[str, Any],
    role: str,
    fields: tuple[str, ...],
    value: Any,
) -> None:
    path = evidence[role + "_path"]
    document = json.loads(path.read_text())
    target = document
    for field in fields[:-1]:
        target = target[field]
    target[fields[-1]] = value
    write(path, document)
    with pytest.raises(SkillEvalError):
        seal(evidence)  # Digests are calculated from the mutated receipt, not stale hashes.
    assert not evidence["output"].exists()


@pytest.mark.parametrize("role", ["offline", "native", "lifecycle"])
def test_legacy_pass_without_identity_capture_refused(evidence: dict[str, Any], role: str) -> None:
    path = evidence[role + "_path"]
    document = json.loads(path.read_text())
    del document["identity"]
    write(path, document)
    with pytest.raises(SkillEvalError, match="identity capture"):
        seal(evidence)


@pytest.mark.parametrize("change", ["present", "blind", "namespace", "config"])
def test_recovery_ticket_requires_bound_positive_and_absence(
    evidence: dict[str, Any],
    change: str,
) -> None:
    path = evidence["native_path"]
    report = json.loads(path.read_text())
    ticket_path = path.parent / "recovery.json"
    ticket = json.loads(ticket_path.read_text())
    if change == "present":
        ticket["steps"][-1]["stdout"] = json.dumps([{"name": ticket["machine_name"]}])
    elif change == "blind":
        next(row for row in ticket["steps"] if row["label"] == "inventory_known_present")[
            "stdout"
        ] = "[]"
    elif change == "namespace":
        ticket["env"]["HOME"] = "/tmp/another-state"
    else:
        ticket["config"]["guest_network"] = True
        ticket["config_sha256"] = hashlib.sha256(
            json.dumps(ticket["config"], sort_keys=True).encode()
        ).hexdigest()
    write(ticket_path, ticket)
    report["artifacts"]["recovery.json"] = file_digest(ticket_path)
    write(path, report)
    with pytest.raises(SkillEvalError):
        seal(evidence)


@pytest.mark.parametrize("change", ["runtime", "image", "catalog", "task", "receipt", "artifact"])
def test_seal_invalidates_on_current_input_or_evidence_drift(
    evidence: dict[str, Any],
    change: str,
) -> None:
    result = seal(evidence)
    root = evidence["root"]
    targets = {
        "runtime": root / "runtime/lib/dependency.dylib",
        "image": root / "image.tar",
        "catalog": evidence["catalog"],
        "task": root / "bundle/case/source/task.txt",
        "receipt": evidence["native_path"],
        "artifact": root / "native/runtime-state/tooling.tar",
    }
    target = targets[change]
    if change == "image":
        plan = json.loads(evidence["plan_path"].read_text())
        target = Path(plan["image_archive"]["path"])
    target.chmod(0o644)
    target.write_bytes(target.read_bytes() + b"changed")
    with pytest.raises(SkillEvalError):
        admission.check_admission(evidence["output"], result["sha256"])


def test_changed_seal_needs_external_digest_and_still_revalidates_facts(
    evidence: dict[str, Any],
) -> None:
    result = seal(evidence)
    path = evidence["native_path"]
    report = json.loads(path.read_text())
    report["broker_receipts"] = []
    write(path, report)
    snapshot = json.loads(evidence["output"].read_text())
    snapshot["bindings"]["files"]["native"] = file_digest(path)
    write(evidence["output"], snapshot)
    with pytest.raises(SkillEvalError, match="snapshot digest"):
        admission.check_admission(evidence["output"], result["sha256"])
    with pytest.raises(SkillEvalError, match="provider requests"):
        admission.check_admission(evidence["output"], file_digest(evidence["output"]))


def test_duplicate_json_keys_and_symlink_receipts_refused(evidence: dict[str, Any]) -> None:
    path = evidence["native_path"]
    original = path.read_bytes()
    path.write_bytes(original[:-1] + b',"status":"authenticated_native_passed"}')
    with pytest.raises(SkillEvalError, match="duplicate JSON key"):
        seal(evidence)
    target = path.with_name("original.json")
    target.write_bytes(original)
    path.unlink()
    path.symlink_to(target)
    with pytest.raises(SkillEvalError):
        seal(evidence)


@pytest.mark.parametrize(
    ("role", "label"),
    [
        ("native", "native_authenticated_loop"),
        ("native", "capture_controls.json"),
        ("native", "guest_toolchain_hashes"),
        ("native", "prepare_tooling"),
        ("offline", "git_history"),
        ("offline", "copy_source"),
        ("offline", "extract_source"),
    ],
)
def test_successful_output_requires_actual_bound_execution_route(
    evidence: dict[str, Any],
    role: str,
    label: str,
) -> None:
    path = evidence[role + "_path"]
    report = json.loads(path.read_text())
    row = next(row for row in report["steps"] if row["label"] == label)
    row["argv"] = ["/usr/bin/printf", "old successful native evidence"]
    write(path, report)
    with pytest.raises(SkillEvalError, match=r"invocation|route"):
        seal(evidence)


@pytest.mark.parametrize("role", ["timeout", "fresh", "sigterm", "sigkill"])
@pytest.mark.parametrize("action", ["create", "start"])
def test_lifecycle_invocations_cannot_add_host_authority_or_change_machine(
    evidence: dict[str, Any],
    role: str,
    action: str,
) -> None:
    report_path = evidence["lifecycle_path"]
    report = json.loads(report_path.read_text())
    path = report_path.parent / role / "recovery.json"
    ticket = json.loads(path.read_text())
    row = next(row for row in ticket["steps"] if row["label"] == action)
    if action == "create":
        row["argv"] += ["--net", "--volume", "/:/host"]
    else:
        row["argv"][-1] = "another-machine"
    write(path, ticket)
    if role in ("timeout", "fresh"):
        report["sessions"][0 if role == "timeout" else 1] = ticket
        write(report_path, report)
    with pytest.raises(SkillEvalError, match="invocation"):
        seal(evidence)


def test_identity_change_during_validation_refuses_sealing(
    evidence: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = admission._native_policy()

    def changed_runtime() -> dict[str, Any]:
        (evidence["root"] / "runtime/lib/dependency.dylib").write_bytes(b"changed mid-check")
        return policy

    monkeypatch.setattr(admission, "_native_policy", changed_runtime)
    with pytest.raises(SkillEvalError, match="identity changed during admission"):
        seal(evidence)


@pytest.mark.parametrize("destination", ["runtime", "bundle", "alias"])
def test_seal_destination_cannot_invalidate_its_own_bound_inputs(
    evidence: dict[str, Any],
    destination: str,
) -> None:
    root = evidence["root"]
    if destination == "alias":
        (root / "runtime-alias").symlink_to(root / "runtime", target_is_directory=True)
        output = root / "runtime-alias" / "admission.json"
    else:
        output = root / destination / "admission.json"
    evidence["output"] = output
    with pytest.raises(SkillEvalError, match="destination"):
        seal(evidence)
    assert not output.exists()
