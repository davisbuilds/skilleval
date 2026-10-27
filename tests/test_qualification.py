import json
import tarfile
from pathlib import Path

import pytest

from skilleval.errors import SkillEvalError
from skilleval.evidence import import_review
from skilleval.qualification import LOG_BYTES, plan_qualification, run_offline_qualification

IMAGE = "docker.io/library/python@sha256:" + "a" * 64


def bundle_at(root: Path) -> Path:
    case = root / "legacy" / "cases" / "synthetic"
    (case / "source").mkdir(parents=True)
    (case / "source" / "task.txt").write_text("captured task")
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
import json, os, pathlib, sys, time
arguments = sys.argv[1:]
state = pathlib.Path(os.environ['HOME']).parent
with (state / 'calls.jsonl').open('a') as log:
    log.write(json.dumps({{'argv': arguments, 'env': dict(os.environ)}}) + '\\n')
if arguments == ['--version']:
    print('smolvm 1.32.3')
    raise SystemExit(0)
if {failure!r} == 'start' and arguments[:2] == ['machine', 'start']:
    raise SystemExit(7)
# v1.32.3 delete prompts without --force and DEVNULL cannot confirm.
if arguments[:2] == ['machine', 'delete'] and '--force' not in arguments:
    raise SystemExit(2)
if {failure!r} == 'delete' and arguments[:2] == ['machine', 'delete']:
    raise SystemExit(8)
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
