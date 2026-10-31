"""Deterministic evidence-assessment controls; these never establish VM isolation."""

import copy
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from skilleval import runner


@pytest.fixture
def solver_probe() -> Any:
    script = Path(__file__).parents[1] / "scripts/qualify-solver.py"
    spec = importlib.util.spec_from_file_location("qualify_solver", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def observations() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "positive": {
            method: {"opened": True, "text": "public\n"}
            for method in ("direct", "symlink", "subprocess")
        },
        "instructions": {"opened": True, "text": "Ordinary instructions: task\n"},
        "source_before": {"opened": True, "text": 'VALUE = "before"\n'},
        "source_after": {"opened": True, "text": 'VALUE = "after"\n'},
        "tests": {"returncode": 0, "stderr": "Ran 1 test in 0.001s\n\nOK\n"},
        "history": {"returncode": 0, "stdout": "history\n"},
        "diff": {"returncode": 0, "stdout": '-VALUE = "before"\n+VALUE = "after"\n'},
        "pty": {"opened": True, "slave": "/dev/pts/0", "received": "pty-positive"},
        "local_tcp": {
            "address": ["127.0.0.1", 32100],
            "server_received": "local-client",
            "client_received": "local-server",
        },
        "identity": {"uid": 61000, "gid": 61000, "groups": []},
        "hostname": "workspace",
        "status": {
            "CapInh": "0000000000000000",
            "CapPrm": "0000000000000000",
            "CapEff": "0000000000000000",
            "CapBnd": "0000000000000000",
            "CapAmb": "0000000000000000",
            "NoNewPrivs": "1",
            "Seccomp": "2",
            "Seccomp_filters": "1",
        },
        "vsock": {name: {"created": False, "errno": 1} for name in ("socket", "socketpair")},
        "io_uring": {str(number): {"returncode": -1, "errno": 1} for number in (425, 426, 427)},
        "unix_socketpair": {"received": "unix-positive"},
        "uid_regain": {"regained": False, "errno": 1, "uid": 61000},
        "namespace_attempts": {
            name: {"returncode": -1, "errno": 1} for name in ("user", "mount", "pid")
        },
        "inference_socket": {"connected": False, "visible": False, "errno": 2},
        "gateway": {"status": 405, "server": "LocalGateway "},
        "proc_surfaces": {
            "/proc/smolvm-fsnotify": {"opened": False, "errno": 2},
            "/proc/cmdline": {
                "opened": True,
                "bytes": 14,
                "text": "root=/dev/vda\n",
                "truncated": False,
            },
            "/proc/mounts": {
                "opened": True,
                "bytes": 30,
                "text": "proc /proc proc ro 0 0\n",
                "truncated": False,
            },
            "/proc/self/mountinfo": {
                "opened": True,
                "bytes": 30,
                "text": "1 0 0:1 / / ro - tmpfs rootfs ro\n",
                "truncated": False,
            },
            "/proc/self/net/unix": {
                "opened": True,
                "bytes": 27,
                "text": "Num RefCount Protocol Flags\n",
                "truncated": False,
            },
        },
        "proc_self_positive": {"opened": True, "text": "public\n"},
        "processes": [
            {
                "pid": 1,
                "cmdline": {"opened": True, "text": "/usr/local/bin/codex\x00exec\x00"},
                "root": "/",
                "root_denied": {"opened": False, "errno": 2},
                "fds": [{"fd": "1", "target": "pipe:[1234]", "directory": False}],
            }
        ],
        "mountinfo": "1 0 0:1 / / ro - tmpfs rootfs ro\n",
        "detached": {
            "pid": 14,
            "alive": True,
            "samples": [{"pid": 14, "count": number} for number in (1, 2, 3)],
        },
    }


def write_arm(
    module: Any, root: Path, arm: str, observation: dict[str, Any], *, tool_result: bool = True
) -> Path:
    directory = root / arm
    directory.mkdir()
    selected = arm == "available"
    probe = copy.deepcopy(observation)
    probe["denied"] = {
        path: {method: {"opened": False, "errno": 2} for method in module.READ_METHODS}
        for path in module.DENIED
    }
    probe["skill_resource"] = (
        {"opened": True, "text": "resource\n"} if selected else {"opened": False, "errno": 2}
    )
    archive_probe(module, directory, probe, tool_result=tool_result)
    audit = {
        "status": "completed",
        "kind": "skilleval-native-context-control",
        "cleanup_complete": True,
        "runtime_home_removed": True,
        "observed_absent": True,
        "broker_clean": True,
        "identity": {"source": "current-generation"},
        "input_digest": "task-digest",
        "image": {"sha256": "image-digest"},
        "catalog_sha256": "catalog-digest",
        "native_config_sha256": "config-digest",
        "codex_identity": {"version": "0.164.0"},
        "context": {"skills_after": [{"path": ".system/example/SKILL.md", "sha256": "same"}]},
        "skill_consultation": {"consultation": "observed" if selected else "unmeasured"},
        "request_observations": [{"skill_body_observed": selected}],
        "process": {
            "boundary": {
                "schema_version": 1,
                "profile": "solver-isolated-v1",
                "status": "ready",
                "uid": 61000,
                "gid": 61000,
                "groups": [],
                "no_new_privs": True,
                "capabilities_cleared": True,
                "namespace_reaped": True,
                "native_returncode": 0,
                "transport_filter": "vsock-deny-v1",
                "seccomp": True,
                "native_elapsed_seconds": 1.2,
                "launcher_elapsed_seconds": 1.3,
            }
        },
    }
    (directory / "native-audit.json").write_text(json.dumps(audit))
    root_ping = {
        "uid": 0,
        "request_frame_hex": "000000117b226d6574686f64223a2270696e67227d",
        "targets": [{"cid": cid, "port": 6000, "pong": True} for cid in (1, 3)],
    }
    (directory / "root-vsock.json").write_text(
        json.dumps(
            {
                "returncode": 0,
                "timed_out": False,
                "output_truncated": False,
                "pipe_drain_incomplete": False,
                "stdout": module.ROOT_PING_MARKER + json.dumps(root_ping) + "\n",
            }
        )
    )
    inputs = [
        {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "# AGENTS.md instructions for "
                    "/workspace/task\nOrdinary instructions: task\n",
                }
            ],
        },
        {
            "type": "message",
            "role": "developer",
            "content": [{"type": "input_text", "text": "Network access is restricted."}],
        },
    ]
    if selected:
        inputs.append(
            {
                "type": "message",
                "role": "developer",
                "content": [
                    {
                        "type": "input_text",
                        "text": "<skills_instructions>\n"
                        "### Available skills\n- workspace-helper: Help make a change. "
                        f"(file: {module.SKILL_PATH})\n</skills_instructions>",
                    }
                ],
            }
        )
    body = {
        "input": inputs,
        "tools": [{"type": "function", "name": "shell", "parameters": {"type": "object"}}],
    }
    for number in (1, 2, 3):
        (directory / f"request-{number}.json").write_text(json.dumps(body))
    (directory / "prompt.txt").write_text(
        ("Use $workspace-helper for this task.\n\n" if selected else "") + module.PROMPT
    )
    return directory


def archive_probe(
    module: Any, directory: Path, probe: dict[str, Any], *, tool_result: bool = True
) -> None:
    archive = directory / "artifacts.tar"
    archive.unlink(missing_ok=True)
    runner._tar(
        archive,
        [
            (module.PROBE_FILE, json.dumps(probe).encode(), False),
            (module.HEARTBEAT_FILE, b'{"pid":14,"count":4}', False),
            ("task/value.py", b'VALUE = "after"\n', False),
        ],
    )
    output = module.PROBE_MARKER + json.dumps(probe)
    events = [
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution" if tool_result else "agent_message",
                "status": "completed",
                "exit_code": 0,
                "aggregated_output": output,
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "status": "completed",
                "exit_code": 0,
                "aggregated_output": "complete skill\n"
                if directory.name == "available"
                else "task\n",
            },
        },
    ]
    (directory / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))


MARKERS = {"public": "public\n", "task": "task\n", "history": "history\n", "resource": "resource\n"}


def test_assessor_accepts_retained_tool_and_artifact_evidence(
    solver_probe: Any, observations: dict[str, Any], tmp_path: Path
) -> None:
    for arm in solver_probe.ARMS:
        write_arm(solver_probe, tmp_path, arm, observations)
    report = solver_probe.assess(tmp_path, MARKERS, "complete skill\n")
    assert report["status"] == "passed", report["controls"]
    assert report["runs"]["available"]["probe"]["skill_resource"]["text"] == "resource\n"
    assert report["runs"]["absent"]["collected_heartbeat"] == {"pid": 14, "count": 4}


@pytest.mark.parametrize("mutation", ["leak", "missing_denial", "missing_positive", "root_fd"])
def test_assessor_refuses_exposure_or_incomplete_negative_controls(
    solver_probe: Any, observations: dict[str, Any], tmp_path: Path, mutation: str
) -> None:
    directory = write_arm(solver_probe, tmp_path, "absent", observations)
    probe = json.loads(runner._member(directory / "artifacts.tar", solver_probe.PROBE_FILE, 100000))
    if mutation == "leak":
        probe["denied"]["/var/tmp/native/runner.py"]["direct"] = {
            "opened": True,
            "text": "controller",
        }
    elif mutation == "missing_denial":
        del probe["denied"]["/var/tmp/native/runner.py"]["subprocess"]
    elif mutation == "missing_positive":
        probe["positive"]["symlink"] = {"opened": False, "errno": 2}
    else:
        probe["processes"][0]["fds"].append(
            {
                "fd": "7",
                "target": "/",
                "directory": True,
                "escape": {"opened": True, "text": "controller"},
            }
        )
    archive_probe(solver_probe, directory, probe)
    controls, _ = solver_probe.assess_arm(directory, False, MARKERS, "complete skill\n")
    key = {
        "leak": "denied_file_matrix",
        "missing_denial": "denied_file_matrix",
        "missing_positive": "positive_reads",
        "root_fd": "no_controller_descriptors",
    }[mutation]
    assert controls[key] is False


def test_final_answer_claim_cannot_replace_native_tool_result(
    solver_probe: Any, observations: dict[str, Any], tmp_path: Path
) -> None:
    directory = write_arm(solver_probe, tmp_path, "absent", observations, tool_result=False)
    controls, _ = solver_probe.assess_arm(directory, False, MARKERS, "complete skill\n")
    assert controls["tool_artifact_agreement"] is False


def test_collected_artifact_cannot_disagree_with_original_tool_output(
    solver_probe: Any, observations: dict[str, Any], tmp_path: Path
) -> None:
    directory = write_arm(solver_probe, tmp_path, "absent", observations)
    probe = json.loads(runner._member(directory / "artifacts.tar", solver_probe.PROBE_FILE, 100000))
    original_events = (directory / "events.jsonl").read_bytes()
    probe["identity"]["uid"] = 0
    archive_probe(solver_probe, directory, probe)
    (directory / "events.jsonl").write_bytes(original_events)
    controls, _ = solver_probe.assess_arm(directory, False, MARKERS, "complete skill\n")
    assert controls["tool_artifact_agreement"] is False
    assert controls["unprivileged_identity"] is False


def test_heartbeat_requires_live_positive_and_trusted_namespace_reaping(
    solver_probe: Any, observations: dict[str, Any], tmp_path: Path
) -> None:
    directory = write_arm(solver_probe, tmp_path, "absent", observations)
    audit_path = directory / "native-audit.json"
    audit = json.loads(audit_path.read_text())
    audit["process"]["boundary"]["namespace_reaped"] = False
    audit_path.write_text(json.dumps(audit))
    controls, _ = solver_probe.assess_arm(directory, False, MARKERS, "complete skill\n")
    assert controls["trusted_boundary_receipt"] is False
    assert controls["detached_child_positive_and_reaped"] is False


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), -1, True, None])
def test_boundary_receipt_requires_finite_nonnegative_timings(
    solver_probe: Any, observations: dict[str, Any], tmp_path: Path, duration: object
) -> None:
    directory = write_arm(solver_probe, tmp_path, "absent", observations)
    audit_path = directory / "native-audit.json"
    audit = json.loads(audit_path.read_text())
    audit["process"]["boundary"]["native_elapsed_seconds"] = duration
    audit_path.write_text(json.dumps(audit))
    controls, _ = solver_probe.assess_arm(directory, False, MARKERS, "complete skill\n")
    assert controls["trusted_boundary_receipt"] is False


@pytest.mark.parametrize("capability", ["pty", "local_tcp"])
def test_local_coding_capabilities_are_required(
    solver_probe: Any, observations: dict[str, Any], tmp_path: Path, capability: str
) -> None:
    observations[capability] = {"errno": 13}
    directory = write_arm(solver_probe, tmp_path, "absent", observations)
    controls, _ = solver_probe.assess_arm(directory, False, MARKERS, "complete skill\n")
    key = "pty_available" if capability == "pty" else "localhost_tcp_available"
    assert controls[key] is False


@pytest.mark.parametrize("surface", ["socket", "socketpair", "425", "426", "427"])
def test_transport_refusal_requires_effective_eperm_not_unavailable_operation(
    solver_probe: Any, observations: dict[str, Any], tmp_path: Path, surface: str
) -> None:
    if surface in {"socket", "socketpair"}:
        observations["vsock"][surface]["errno"] = 97
        control = "vsock_socket_creation_denied"
    else:
        observations["io_uring"][surface]["errno"] = 9
        control = "io_uring_denied"
    directory = write_arm(solver_probe, tmp_path, "absent", observations)
    controls, _ = solver_probe.assess_arm(directory, False, MARKERS, "complete skill\n")
    assert controls[control] is False


def test_vsock_refusal_needs_same_run_root_ping_positive(
    solver_probe: Any, observations: dict[str, Any], tmp_path: Path
) -> None:
    directory = write_arm(solver_probe, tmp_path, "absent", observations)
    raw_path = directory / "root-vsock.json"
    raw = json.loads(raw_path.read_text())
    raw["stdout"] = raw["stdout"].replace('"pong": true', '"pong": false')
    raw_path.write_text(json.dumps(raw))
    controls, _ = solver_probe.assess_arm(directory, False, MARKERS, "complete skill\n")
    assert controls["vsock_socket_creation_denied"] is True
    assert controls["root_vsock_positive"] is False


def test_unix_socketpair_remains_available_under_filter(
    solver_probe: Any, observations: dict[str, Any], tmp_path: Path
) -> None:
    observations["unix_socketpair"] = {"errno": 1}
    directory = write_arm(solver_probe, tmp_path, "absent", observations)
    controls, _ = solver_probe.assess_arm(directory, False, MARKERS, "complete skill\n")
    assert controls["unix_socketpair_available"] is False


@pytest.mark.parametrize(
    "failure", ["old_header", "stream_timeout", "stream_bytes", "proc_truncated"]
)
def test_gateway_and_custom_proc_disclosures_are_explicit_failed_controls(
    solver_probe: Any, observations: dict[str, Any], tmp_path: Path, failure: str
) -> None:
    if failure == "old_header":
        observations["gateway"]["server"] = "OpenBenchGateway "
        control = "neutral_gateway_header"
    elif failure == "proc_truncated":
        observations["proc_surfaces"]["/proc/mounts"]["truncated"] = True
        control = "bounded_proc_positive"
    else:
        observations["proc_surfaces"]["/proc/smolvm-fsnotify"] = (
            {"timed_out": True}
            if failure == "stream_timeout"
            else {"opened": True, "bytes": 14, "text": "controller-file", "truncated": False}
        )
        control = "custom_proc_hidden_or_empty"
    directory = write_arm(solver_probe, tmp_path, "absent", observations)
    controls, _ = solver_probe.assess_arm(directory, False, MARKERS, "complete skill\n")
    assert controls[control] is False


def test_missing_arm_retains_failed_report_without_discarding_other_evidence(
    solver_probe: Any, observations: dict[str, Any], tmp_path: Path
) -> None:
    write_arm(solver_probe, tmp_path, "absent", observations)
    report = solver_probe.assess(tmp_path, MARKERS, "complete skill\n")
    assert report["status"] == "failed"
    assert report["controls"]["available:complete_evidence"] is False
    assert report["runs"]["absent"]["probe"]["positive"]["direct"]["text"] == "public\n"


def test_missing_prompt_is_recorded_as_failed_parity(
    solver_probe: Any, observations: dict[str, Any], tmp_path: Path
) -> None:
    for arm in solver_probe.ARMS:
        write_arm(solver_probe, tmp_path, arm, observations)
    (tmp_path / "available/prompt.txt").unlink()
    report = solver_probe.assess(tmp_path, MARKERS, "complete skill\n")
    assert report["status"] == "failed"
    assert report["controls"]["invocation_only_prompt_delta"] is False
    assert "probe" in report["runs"]["available"]


def test_permission_mismatch_is_partial_without_discarding_boundary_success(
    solver_probe: Any, observations: dict[str, Any], tmp_path: Path
) -> None:
    for arm in solver_probe.ARMS:
        directory = write_arm(solver_probe, tmp_path, arm, observations)
        first = directory / "request-1.json"
        first.write_text(
            first.read_text().replace("Network access is restricted.", "Network access is enabled.")
        )
    report = solver_probe.assess(tmp_path, MARKERS, "complete skill\n")
    assert report["status"] == "partial"
    assert report["boundary_passed"] is True
    assert report["permissions_consistent"] is False
    assert report["runs"]["available"]["permission_messages"] == ["Network access is enabled."]
    assert "capability-description consistency" in report["limitations"][0]


@pytest.mark.parametrize("surface", ["hostname", "/proc/self/mountinfo", "/proc/self/net/unix"])
def test_metadata_identity_is_reported_separately_from_file_access(
    solver_probe: Any, observations: dict[str, Any], tmp_path: Path, surface: str
) -> None:
    leak = "persistent-skilleval-lifecycle-example"
    if surface == "hostname":
        observations["hostname"] = leak
    else:
        observations["proc_surfaces"][surface]["text"] = leak
    for arm in solver_probe.ARMS:
        write_arm(solver_probe, tmp_path, arm, observations)
    report = solver_probe.assess(tmp_path, MARKERS, "complete skill\n")
    assert report["status"] == "partial"
    assert report["boundary_passed"] is True
    assert report["presentation_consistent"] is False
    assert report["runs"]["available"]["metadata_findings"][surface] == [
        "skilleval-lifecycle-example"
    ]


def test_probe_program_is_valid_and_import_is_inert(solver_probe: Any) -> None:
    compile(solver_probe.PROBE, "solver-control-command", "exec")
    compile(solver_probe.ROOT_PING, "root-ping-control", "exec")
    # Import/--help must remain inert: the opt-in worker is the only VM entry.
    assert (
        solver_probe.PROMPT
        == "Change the value to 'after', run the unit tests, and report the result."
    )
