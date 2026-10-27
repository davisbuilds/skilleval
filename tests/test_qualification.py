import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from skilleval.errors import SkillEvalError
from skilleval.evidence import import_review
from skilleval.qualification import LOG_BYTES, plan_qualification, run_offline_qualification

IMAGE = "docker.io/library/python@sha256:" + "a" * 64


def bundle_at(root: Path, reconstruct_git: bool = False) -> Path:
    case = root / "legacy" / "cases" / "synthetic"
    (case / "source").mkdir(parents=True)
    (case / "source" / "task.txt").write_text("captured task" + ("\n" if reconstruct_git else ""))
    if reconstruct_git:

        def blob(data: bytes) -> str:
            return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

        (case / "source" / ".openbench-review.json").write_text(
            '{"schema":1,"patch":"review.patch"}\n'
        )
        (case / "source" / "review.patch").write_text(
            "diff --git a/task.txt b/task.txt\n"
            f"index {blob(b'base task\n')}..{blob(b'captured task\n')} 100644\n"
            "--- a/task.txt\n+++ b/task.txt\n@@ -1 +1 @@\n-base task\n+captured task\n"
        )
    (case / "oracle.md").write_text("hidden answer")
    (case / "instruction.md").write_text("public instruction")
    (root / "legacy" / "reports.json").write_text('[ {"case_id": "synthetic", "report_id": "r1"} ]')
    bundle = root / "bundle"
    import_review(root / "legacy", "synthetic", bundle)
    return bundle


def test_plan_pins_inputs_without_launching(tmp_path: Path) -> None:
    bundle = bundle_at(tmp_path)
    plan = plan_qualification(bundle, IMAGE, [["python3", "-m", "pytest"]])
    assert plan["status"] == "planned"
    assert plan["admitted"] is False
    assert plan["policy"] == {"guest_network": False, "host_mounts": [], "secrets": []}
    assert plan["commands"][0]["argv"] == ["python3", "-m", "pytest"]
    assert plan["commands"][0]["timeout_seconds"] > 0
    assert len(plan["bundle_sha256"]) == 64
    before = plan["bundle_sha256"]
    assert before
    other = bundle_at(tmp_path / "other")
    assert plan_qualification(other, IMAGE, [["true"]])["bundle_sha256"] == before


@pytest.mark.parametrize("image", ["python:latest", "python:3.12", "python@sha256:bad"])
def test_mutable_or_invalid_image_is_rejected(tmp_path: Path, image: str) -> None:
    with pytest.raises(SkillEvalError, match="digest"):
        plan_qualification(tmp_path, image, [["true"]])


def test_symlinks_are_not_followed(tmp_path: Path) -> None:
    bundle = bundle_at(tmp_path)
    bundle.chmod(0o755)
    (bundle / "escape").symlink_to("/etc/passwd")
    with pytest.raises(SkillEvalError, match="symlink"):
        plan_qualification(bundle, IMAGE, [["true"]])


@pytest.mark.parametrize("commands", [[], [[]], [[""]], [["true", "\x00"]]])
def test_argv_is_explicit_and_nonempty(tmp_path: Path, commands: list[list[str]]) -> None:
    with pytest.raises(SkillEvalError, match="command"):
        plan_qualification(tmp_path, IMAGE, commands)


def fake_runtime(tmp_path: Path, failure: str = "") -> Path:
    """A real subprocess contract fixture, never evidence of VM isolation."""
    import sys

    runtime = tmp_path / "runtime"
    script = f"""#!{sys.executable}
import json, os, pathlib, subprocess, sys, time
arguments = sys.argv[1:]
state = pathlib.Path(os.environ['XDG_CONFIG_HOME']).parent
with (state / 'calls.jsonl').open('a') as log:
    log.write(json.dumps({{'argv': arguments, 'env': dict(os.environ)}}) + '\\n')
if arguments == ['--version']:
    print('smolvm 1.32.3')
    raise SystemExit(0)
if {failure!r} == 'start' and arguments[:2] == ['machine', 'start']:
    raise SystemExit(7)
if {failure!r} == 'socket-path' and arguments[:2] == ['machine', 'start']:
    socket_path = os.environ['HOME'] + '/Library/Caches/smolvm/vms/0123456789abcdef/agent.sock'
    if len(socket_path.encode()) > 103:
        print('socket path exceeds macOS limit', file=sys.stderr)
        raise SystemExit(9)
# v1.32.3 delete prompts without --force and DEVNULL cannot confirm.
if arguments[:2] == ['machine', 'delete'] and '--force' not in arguments:
    raise SystemExit(2)
if {failure!r} == 'delete' and arguments[:2] == ['machine', 'delete']:
    raise SystemExit(8)
if arguments[:2] == ['machine', 'exec'] and 'git' in arguments:
    command = arguments[arguments.index('--') + 1:]
    allowed = [
        ['git', 'rev-parse', 'HEAD', 'HEAD^', 'candidate^{{tree}}', 'base^{{tree}}'],
        ['git', 'status', '--porcelain'], ['git', 'fsck', '--no-reflogs']
    ]
    if command in allowed:
        if {failure!r} == 'git-history' and command[1] == 'rev-parse':
            print('wrong commit')
            raise SystemExit(0)
        if {failure!r} == 'git-worktree' and command[1] == 'status':
            print(' M task.txt')
            raise SystemExit(0)
        result = subprocess.run(['/usr/bin/git', '-C', str(state / 'workspace'), *command[1:]],
            env={{'PATH':'/usr/bin:/bin', 'GIT_CONFIG_GLOBAL':'/dev/null',
                 'GIT_CONFIG_NOSYSTEM':'1'}}, capture_output=True)
        sys.stdout.buffer.write(result.stdout)
        sys.stderr.buffer.write(result.stderr)
        raise SystemExit(result.returncode)
if arguments[:2] == ['machine', 'exec'] and 'big-output' in arguments:
    print('x' * 200000)
if arguments[:2] == ['machine', 'exec'] and 'failing-control' in arguments:
    raise SystemExit(3)
print('ok')
"""
    runtime.write_text(script)
    runtime.chmod(0o755)
    return runtime


def test_subprocess_contract_copies_only_source_and_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = bundle_at(tmp_path)
    sentinel = tmp_path / "host-effect"
    plan = plan_qualification(bundle, IMAGE, [["big-output", f"$(touch {sentinel})"]])
    runtime = fake_runtime(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-a-secret")
    report = run_offline_qualification(plan, tmp_path / "result", str(runtime))
    assert report["status"] == "offline_passed"
    assert report["admitted"] is False
    assert report["cleanup_complete"] is True
    calls = [
        json.loads(line)
        for line in (tmp_path / "result" / "runtime-state" / "calls.jsonl").read_text().splitlines()
    ]
    assert calls[-2]["argv"][1] == "stop"
    assert calls[-1]["argv"][1] == "delete"
    assert "--force" in calls[-1]["argv"]
    assert all("OPENAI_API_KEY" not in call["env"] for call in calls)
    create = next(call["argv"] for call in calls if call["argv"][:2] == ["machine", "create"])
    assert "--net" not in create and "--volume" not in create
    with tarfile.open(tmp_path / "result" / "runtime-state" / "task.tar") as archive:
        assert archive.getnames() == ["task.txt"]
        assert archive.extractfile("task.txt").read() == b"captured task"  # type: ignore[union-attr]
    assert not sentinel.exists()  # command text was never evaluated by a host shell
    control = next(step for step in report["steps"] if step["label"] == "project_control_0")
    assert control["output_truncated"] is True
    assert len(control["stdout"].encode()) == LOG_BYTES
    assert json.loads((tmp_path / "result" / "report.json").read_text()) == report


@pytest.mark.parametrize("failure", ["start", "delete"])
def test_subprocess_contract_failure_never_admits_and_attempts_cleanup(
    tmp_path: Path,
    failure: str,
) -> None:
    bundle = bundle_at(tmp_path)
    report = run_offline_qualification(
        plan_qualification(bundle, IMAGE, [["true"]]),
        tmp_path / "result",
        str(fake_runtime(tmp_path, failure)),
    )
    assert report["status"] == "partial"
    assert report["admitted"] is False
    assert [step["action"] for step in report["cleanup"]] == ["stop", "delete"]


def test_modified_plan_or_bundle_refused_before_launch(tmp_path: Path) -> None:
    bundle = bundle_at(tmp_path)
    plan = plan_qualification(bundle, IMAGE, [["true"]])
    plan["policy"]["guest_network"] = True
    with pytest.raises(SkillEvalError, match="changed"):
        run_offline_qualification(plan, tmp_path / "result", "/missing/runtime")
    assert not (tmp_path / "result").exists()


def test_failing_control_is_partial(tmp_path: Path) -> None:
    bundle = bundle_at(tmp_path)
    report = run_offline_qualification(
        plan_qualification(bundle, IMAGE, [["failing-control"]]),
        tmp_path / "result",
        str(fake_runtime(tmp_path)),
    )
    assert report["status"] == "partial"
    assert report["cleanup_complete"] is True


def test_deep_output_does_not_extend_runtime_socket_paths(tmp_path: Path) -> None:
    bundle = bundle_at(tmp_path)
    output = tmp_path / ("nested-output-" * 8) / "result"
    report = run_offline_qualification(
        plan_qualification(bundle, IMAGE, [["true"]]),
        output,
        str(fake_runtime(tmp_path, "socket-path")),
    )
    assert report["status"] == "offline_passed"
    assert report["runtime_home_removed"] is True
    assert not Path(report["runtime_home"]).exists()
    assert (output / "report.json").is_file()


def test_captured_task_tampering_refused_before_launch(tmp_path: Path) -> None:
    bundle = bundle_at(tmp_path)
    plan = plan_qualification(bundle, IMAGE, [["true"]])
    target = bundle / "case" / "source" / "task.txt"
    target.chmod(0o644)
    target.write_text("replaced")
    with pytest.raises(SkillEvalError, match=r"digest|changed|match"):
        run_offline_qualification(plan, tmp_path / "result", "/missing/runtime")
    assert not (tmp_path / "result").exists()


def test_timeout_kills_real_process_with_bounded_output(tmp_path: Path) -> None:
    """Process cancellation contract only; this is not a VM isolation test."""
    import sys

    from skilleval.qualification import _invoke

    result = _invoke(
        [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(20)"],
        tmp_path,
        {"PATH": "/usr/bin:/bin"},
        1,
    )
    assert result["timed_out"] is True
    assert result["returncode"] < 0
    assert result["stdout"] == "started\n"
    assert result["pipe_drain_incomplete"] is False


def test_version_mismatch_creates_no_vm(tmp_path: Path) -> None:
    bundle = bundle_at(tmp_path)
    runtime = fake_runtime(tmp_path)
    runtime.write_text(runtime.read_text().replace("smolvm 1.32.3", "smolvm 1.32.4"))
    report = run_offline_qualification(
        plan_qualification(bundle, IMAGE, [["true"]]),
        tmp_path / "result",
        str(runtime),
    )
    assert report["status"] == "partial"
    assert report["cleanup"] == []
    assert [step["label"] for step in report["steps"]] == ["runtime_version"]
    assert "version differs" in report["error"]


def test_lifecycle_timeout_attempts_stop_and_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real subprocess fixture checks cleanup, not the smolvm runtime boundary."""
    from skilleval import qualification

    original = qualification._invoke

    def short_timeout(
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        timeout: int,
    ) -> dict[str, object]:
        return original(argv, cwd, env, min(timeout, 1))

    monkeypatch.setattr(qualification, "_invoke", short_timeout)
    runtime = fake_runtime(tmp_path)
    runtime.write_text(
        runtime.read_text().replace(
            "print('ok')",
            "if arguments[:2] == ['machine', 'start']: time.sleep(20)\nprint('ok')",
        )
    )
    report = run_offline_qualification(
        plan_qualification(bundle_at(tmp_path), IMAGE, [["true"]]),
        tmp_path / "result",
        str(runtime),
    )
    assert report["status"] == "partial"
    assert report["cleanup_complete"] is True
    assert report["steps"][-1]["timed_out"] is True
    assert [step["action"] for step in report["cleanup"]] == ["stop", "delete"]


def test_sigterm_offline_controller_cleans_machine_child_and_writes_report(tmp_path: Path) -> None:
    """Exercise the real controller with synthetic processes, never a real VM."""
    import os
    import signal
    import subprocess
    import sys
    import time
    from contextlib import suppress

    from skilleval.lifecycle import recover

    distribution = tmp_path / "distribution"
    distribution.mkdir()
    runtime = fake_runtime(tmp_path)
    heartbeat = tmp_path / "control-heartbeat"
    runtime.write_text(
        runtime.read_text().replace(
            "print('ok')",
            "machines = state / 'machines.json'\n"
            "names = json.loads(machines.read_text()) if machines.exists() else []\n"
            "name = arguments[arguments.index('--name') + 1] if '--name' in arguments else None\n"
            "if arguments[:2] == ['machine', 'create']:\n"
            "    ticket = json.loads((state.parent / 'recovery.json').read_text())\n"
            "    assert ticket['create_attempted']\n"
            "    names.append(name); machines.write_text(json.dumps(names))\n"
            "if arguments[:2] == ['machine', 'ls']:\n"
            "    print(json.dumps([{'name': item} for item in names])); raise SystemExit\n"
            "if arguments[:2] == ['machine', 'delete']:\n"
            "    machines.write_text(json.dumps([item for item in names if item != name]))\n"
            "if arguments[:2] == ['machine', 'exec'] and 'long-control' in arguments:\n"
            f"    beat = pathlib.Path({str(heartbeat)!r})\n"
            "    while True:\n"
            "        sample = {'pid': os.getpid(), 'tick': time.monotonic_ns()}\n"
            "        pending = beat.with_suffix('.pending')\n"
            "        pending.write_text(json.dumps(sample)); pending.replace(beat)\n"
            "        time.sleep(.05)\n"
            "print('ok')",
        )
    )
    runtime = runtime.rename(distribution / "smolvm")
    for directory in ("lib", "agent-rootfs"):
        (distribution / directory).mkdir()
    for component in ("smolvm-bin", "storage-template.ext4", "overlay-template.ext4"):
        (distribution / component).write_bytes(b"synthetic-runtime-component")
    archive, digest = docker_archive(tmp_path / "image")
    plan = plan_qualification(
        bundle_at(tmp_path), None, [["long-control"]], image_archive=archive, image_sha256=digest
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    output = tmp_path / "result"
    controller = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import json; from pathlib import Path; "
            "from skilleval.qualification import run_offline_qualification; "
            f"run_offline_qualification(json.loads(Path({str(plan_path)!r}).read_text()), "
            f"Path({str(output)!r}), {str(runtime)!r}, capture_identity=True)",
        ],
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    child_pid = None
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if heartbeat.exists() and heartbeat.stat().st_size:
                first = heartbeat.read_text()
                child_pid = json.loads(first)["pid"]
                time.sleep(0.15)
                if heartbeat.read_text() != first:
                    break
            time.sleep(0.02)
        else:
            pytest.fail("synthetic project control did not establish a live heartbeat")
        ticket_path = output / "recovery.json"
        before = json.loads(ticket_path.read_text())
        assert before["create_attempted"] and before["observer_positive"]
        controller.send_signal(signal.SIGTERM)
        controller.wait(timeout=5)
        report = json.loads((output / "report.json").read_text())
        assert report["status"] == "partial" and report["admitted"] is False
        assert report["cleanup_complete"] and report["runtime_home_removed"]
        assert report["machine_observer_positive"] and report["machine_observed_absent"]
        assert not Path(before["runtime_home"]).exists()
        last = heartbeat.read_text()
        time.sleep(0.15)
        assert heartbeat.read_text() == last
        ticket = json.loads(ticket_path.read_text())
        assert ticket["cleanup_complete"] and ticket["phase"] == "recovered"
        assert [row["action"] for row in ticket["cleanup"]] == ["stop", "delete"]
        assert recover(output) == ticket
    finally:
        if controller.poll() is None:
            controller.kill()
            controller.wait(timeout=5)
        if child_pid is not None:
            with suppress(ProcessLookupError):
                os.killpg(child_pid, signal.SIGKILL)
        if (output / "recovery.json").exists():
            recover(output)


def test_packed_registry_route_refused_before_any_subprocess(tmp_path: Path) -> None:
    """v1.32.3 non-Hub refs can restore a pack with manifest.network=true."""
    bundle = bundle_at(tmp_path)
    image = "registry.smolmachines.com/library/network-enabled@sha256:" + "b" * 64
    plan = plan_qualification(bundle, image, [["true"]])
    assert plan["status"] == "planned"  # still useful as a reviewable plan
    runtime = fake_runtime(tmp_path)
    with pytest.raises(SkillEvalError, match=r"docker\.io"):
        run_offline_qualification(plan, tmp_path / "result", str(runtime))
    assert not (tmp_path / "result").exists()


def test_output_alias_into_bundle_refused_before_launch(tmp_path: Path) -> None:
    bundle = bundle_at(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(bundle, target_is_directory=True)
    with pytest.raises(SkillEvalError, match="outside captured"):
        run_offline_qualification(
            plan_qualification(bundle, IMAGE, [["true"]]),
            alias / "result",
            str(fake_runtime(tmp_path)),
        )
    assert not (bundle / "result").exists()


def docker_archive(root: Path, extra: tuple[str, bytes] | None = None) -> tuple[Path, str]:
    root.mkdir(parents=True, exist_ok=True)
    archive = root / "image.tar"
    layer = b"synthetic layer bytes"
    config = json.dumps(
        {
            "architecture": "arm64",
            "os": "linux",
            "rootfs": {
                "type": "layers",
                "diff_ids": ["sha256:" + hashlib.sha256(layer).hexdigest()],
            },
        }
    ).encode()
    config_name = hashlib.sha256(config).hexdigest() + ".json"
    manifest = json.dumps(
        [{"Config": config_name, "RepoTags": ["synthetic:local"], "Layers": ["layer/layer.tar"]}]
    ).encode()
    entries = [("manifest.json", manifest), (config_name, config), ("layer/layer.tar", layer)]
    if extra:
        entries.append(extra)
    with tarfile.open(archive, "w") as output:
        for name, data in entries:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            output.addfile(info, io.BytesIO(data))
    return archive, hashlib.sha256(archive.read_bytes()).hexdigest()


def test_local_archive_plan_binds_exact_image_bytes(tmp_path: Path) -> None:
    archive, digest = docker_archive(tmp_path / "image")
    plan = plan_qualification(
        bundle_at(tmp_path), None, [["true"]], image_archive=archive, image_sha256=digest
    )
    assert plan["runtime"]["image_route"] == "local-docker-save"
    assert plan["image_archive"]["sha256"] == digest
    assert plan["image_archive"]["path"] == str(archive)


def test_local_archive_run_copies_hash_bound_bytes_without_registry_route(tmp_path: Path) -> None:
    archive, digest = docker_archive(tmp_path / "image")
    plan = plan_qualification(
        bundle_at(tmp_path), None, [["true"]], image_archive=archive, image_sha256=digest
    )
    report = run_offline_qualification(plan, tmp_path / "result", str(fake_runtime(tmp_path)))
    assert report["status"] == "offline_passed"
    assert report["cleanup_complete"] is True
    state = tmp_path / "result" / "runtime-state"
    copied = state / "image.tar"
    assert copied.read_bytes() == archive.read_bytes()
    create = next(item for item in report["steps"] if item["label"] == "create")
    assert create["argv"][create["argv"].index("--image") + 1] == str(copied)
    assert "--net" not in create["argv"]


@pytest.mark.parametrize("change", ["bytes", "symlink", "fifo", "oversized"])
def test_image_archive_changes_refused_before_runtime_launch(tmp_path: Path, change: str) -> None:
    import os

    from skilleval.qualification import MAX_IMAGE_BYTES

    archive, digest = docker_archive(tmp_path / "image")
    plan = plan_qualification(
        bundle_at(tmp_path), None, [["true"]], image_archive=archive, image_sha256=digest
    )
    if change == "bytes":
        archive.write_bytes(b"replacement")
    else:
        archive.unlink()
        if change == "symlink":
            target, _ = docker_archive(tmp_path / "other-image")
            archive.symlink_to(target)
        elif change == "fifo":
            os.mkfifo(archive)
        else:
            with archive.open("wb") as output:
                output.truncate(MAX_IMAGE_BYTES + 1)
    with pytest.raises(SkillEvalError):
        run_offline_qualification(plan, tmp_path / "result", "/missing/runtime")
    assert not (tmp_path / "result").exists()


@pytest.mark.parametrize(
    "extra",
    [
        (".smolmachine/manifest.json", b'{"network":true}'),
        ("../outside", b"escape"),
        ("manifest.json", b"duplicate"),
        ("unrecognized-policy.json", b'{"mounts":["/host"]}'),
    ],
)
def test_non_docker_save_policy_and_unsafe_members_rejected(
    tmp_path: Path, extra: tuple[str, bytes]
) -> None:
    archive, digest = docker_archive(tmp_path / "image", extra)
    with pytest.raises(SkillEvalError):
        plan_qualification(
            bundle_at(tmp_path), None, [["true"]], image_archive=archive, image_sha256=digest
        )


def test_archive_digest_and_route_must_be_explicit(tmp_path: Path) -> None:
    archive, digest = docker_archive(tmp_path / "image")
    bundle = bundle_at(tmp_path)
    for image, path, identity in [
        (None, archive, None),
        (IMAGE, archive, digest),
        (IMAGE, None, digest),
    ]:
        with pytest.raises(SkillEvalError):
            plan_qualification(bundle, image, [["true"]], image_archive=path, image_sha256=identity)


def test_reconstructed_git_transfer_binds_and_checks_native_history(tmp_path: Path) -> None:
    from skilleval.workspace import _git

    bundle = bundle_at(tmp_path, reconstruct_git=True)
    plan = plan_qualification(bundle, IMAGE, [["true"]], reconstruct_git=True)
    report = run_offline_qualification(plan, tmp_path / "result", str(fake_runtime(tmp_path)))
    assert report["status"] == "offline_passed"
    assert report["workspace"] == plan["workspace"]
    state = tmp_path / "result" / "runtime-state"
    with tarfile.open(state / "task.tar") as archive:
        names = archive.getnames()
        assert ".git/HEAD" in names
        assert ".git/refs/heads/base" in names
        assert "task.txt" in names and "review.patch" in names
        assert not {"oracle.md", "reports.json", "manifest.json"} & set(names)
        assert archive.extractfile("task.txt").read() == b"captured task\n"  # type: ignore[union-attr]
    assert _git(state / "workspace", "show", "base:task.txt") == b"base task\n"
    checks = [step["label"] for step in report["steps"]]
    assert checks.index("git_history") < checks.index("project_control_0")
    assert "git_worktree" in checks and "git_objects" in checks
    assert plan["workspace"]["original_history_available"] is False


@pytest.mark.parametrize("failure", ["git-history", "git-worktree"])
def test_guest_git_mismatch_is_partial_and_skips_project_controls(
    tmp_path: Path, failure: str
) -> None:
    plan = plan_qualification(
        bundle_at(tmp_path, reconstruct_git=True), IMAGE, [["true"]], reconstruct_git=True
    )
    report = run_offline_qualification(
        plan, tmp_path / "result", str(fake_runtime(tmp_path, failure))
    )
    assert report["status"] == "partial"
    assert report["cleanup_complete"] is True
    assert not any(step["label"].startswith("project_control") for step in report["steps"])
    assert "guest native Git" in report["error"]


def test_modified_workspace_plan_refused_before_runtime_launch(tmp_path: Path) -> None:
    plan = plan_qualification(
        bundle_at(tmp_path, reconstruct_git=True), IMAGE, [["true"]], reconstruct_git=True
    )
    plan["workspace"]["base_commit"] = "a" * 40
    with pytest.raises(SkillEvalError, match="changed"):
        run_offline_qualification(plan, tmp_path / "result", "/missing/runtime")
    assert not (tmp_path / "result").exists()


@pytest.mark.parametrize(
    "kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.XHDTYPE]
)
def test_archive_links_devices_and_parser_extensions_refused(tmp_path: Path, kind: bytes) -> None:
    archive, _ = docker_archive(tmp_path / "image")
    with tarfile.open(archive, "a") as output:
        info = tarfile.TarInfo("untrusted-extension")
        info.type = kind
        info.linkname = "../outside"
        output.addfile(info)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(SkillEvalError, match="unsupported"):
        plan_qualification(
            bundle_at(tmp_path), None, [["true"]], image_archive=archive, image_sha256=digest
        )
