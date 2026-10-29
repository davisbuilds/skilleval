"""Real filesystem/process contracts, never proof of real VM or model behavior."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_lifecycle import runtime_at
from test_qualification import docker_archive

from skilleval import evidence, guest_runner, runner
from skilleval.errors import SkillEvalError
from skilleval.gateway import HostAuth
from skilleval.identity import file_digest

RUNTIME_BODY = r"""
import io,json,os,pathlib,shutil,sys,tarfile
args=sys.argv[1:]
state=pathlib.Path(os.environ['XDG_CONFIG_HOME']).parent
machines=state/'machines.json'
names=json.loads(machines.read_text()) if machines.exists() else []
if args==['--version']: print('smolvm 1.32.3');raise SystemExit
name=args[args.index('--name')+1] if '--name' in args else None
if args[:2]==['machine','create']:
 ticket=json.loads((state.parent/'recovery.json').read_text())
 assert ticket['create_attempted']
 names.append(name);machines.write_text(json.dumps(names))
elif args[:2]==['machine','ls']:
 print(json.dumps([{'name':n} for n in names]));raise SystemExit
elif args[:2]==['machine','delete']:
 names.remove(name);machines.write_text(json.dumps(names))
elif args[:2]==['machine','stop']:
 scenario=json.loads((state/'scenario.json').read_text())
 if scenario.get('fault')=='stop-failure':raise SystemExit(7)
elif args[:2]==['machine','cp']:
 if args[2].endswith(':/var/tmp/native/artifacts.tar'):
  scenario=json.loads((state/'scenario.json').read_text())
  if scenario.get('fault')=='large-copy':
   with open(args[3],'wb') as out:
    for _ in range(1000):out.write(b'x'*65536);out.flush()
  else:shutil.copyfile(state/'artifacts.tar',args[3])
 else:shutil.copyfile(args[2],state/'tooling-copy.tar')
elif args[:2]==['machine','exec']:
 command=args[args.index('--')+1:]
 if command==['codex','--version']:
  print('codex-cli 0.164.0');raise SystemExit
 if command[0]=='cat':
  print(json.dumps({'status':'complete'}));raise SystemExit
 if len(command)>1 and command[1]=='/run/native/controller.py':
  (state/'scenario.json').write_text('{}')
  with tarfile.open(state/'tooling-copy.tar') as source:
   payload=json.loads(source.extractfile('native/requests.json').read())
   assert set(payload)=={'timeout_seconds','requests'}
   assert all('expected' not in r for r in payload['requests'])
   observed=[r.get('value') for r in payload['requests']]
  print(json.dumps({'schema_version':1,'returncode':0,'timed_out':False,
   'output_limit_reached':False,'stdout':json.dumps({'observations':observed}),
   'stderr':'','worker_uid':65534,'expected_answers_supplied':False}));raise SystemExit
 if len(command)>1 and command[1]=='/var/tmp/native/runner.py':
  entries={}
  with tarfile.open(state/'tooling-copy.tar') as source:
   for m in source:
    if m.name.startswith('task/'):
     entries[m.name]=(source.extractfile(m).read(),m.mode)
  scenario=json.loads(entries['task/scenario.json'][0])
  (state/'scenario.json').write_text(json.dumps(scenario))
  fault=scenario.get('fault')
  process={'returncode':0,'timed_out':False,'codex_version':'codex-cli 0.164.0'}
  if scenario.get('complete_usage'):process['agent_elapsed_seconds']=0.01
  output_size=5*1024*1024 if fault=='large-output' else 90000
  events=[{'type':'thread.started','thread_id':'synthetic'},
   {'type':'item.completed','item':{'type':'command_execution','status':'completed',
    'command':'synthetic-fixture-command',
    'exit_code':1,'aggregated_output':'expected failing probe '+('x'*output_size)}}]
  if scenario.get('complete_usage'):events.insert(1,{'type':'turn.started'})
  if fault=='events-error':events.append({'type':'error','message':'synthetic failure'})
  if fault=='timed-out':process.update(returncode=-15,timed_out=True)
  if fault!='timed-out':events.append({'type':'turn.completed','usage':{'input_tokens':3}})
  if scenario.get('complete_usage') and fault!='timed-out':
   events[-1]['usage']={'input_tokens':3,'cached_input_tokens':1,'output_tokens':2}
  raw='\n'.join(json.dumps(e) for e in events).encode()
  entries['evidence/events.jsonl']=(raw,0o600)
  if fault!='timed-out':entries['evidence/final.txt']=(b'Final synthetic answer',0o600)
  entries['evidence/process.json']=(json.dumps(process).encode(),0o600)
  entries['evidence/filesystem-links.json']=(b'[]',0o600)
  if 'repair_files' in scenario:
   for filename,content in scenario['repair_files'].items():
    if content is None:entries.pop('task/'+filename,None)
    else:entries['task/'+filename]=(content.encode(),0o644)
  else:entries['task/investigation/probe.txt']=(b'observed synthetic artifact',0o644)
  if fault=='drift':
   original=pathlib.Path(scenario['original']);original.chmod(0o600)
   original.write_text('changed')
  with tarfile.open(state/'artifacts.tar','w') as target:
   for filename,(content,mode) in entries.items():
    info=tarfile.TarInfo(filename);info.size=len(content);info.mode=mode
    target.addfile(info,io.BytesIO(content))
print('ok')
"""


@pytest.fixture
def route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    runtime = runtime_at(tmp_path / "runtime")
    runtime.write_text(f"#!{sys.executable}\n" + RUNTIME_BODY)
    image, digest = docker_archive(tmp_path / "image")
    catalog = tmp_path / "models.json"
    catalog.write_text("{}")
    monkeypatch.setattr(runner, "CATALOG_SHA256", file_digest(catalog))
    monkeypatch.setattr(
        runner,
        "load_chatgpt_auth",
        lambda *args, **kwargs: HostAuth("unused", "unused", time.time() + 4000),
    )

    @contextmanager
    def broker(auth: Any, directory: Path, **kwargs: Any) -> Iterator[Any]:
        directory.mkdir(mode=0o700)
        handle = SimpleNamespace(
            socket_path=directory / "inference.sock",
            clean=True,
            receipts=[
                {
                    "event": "request",
                    "outcome": "complete",
                    "upstream_status": 200,
                    "request_id": "synthetic-request",
                    "model": "gpt-6.1-sol",
                    "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
                }
            ],
        )
        try:
            yield handle
        finally:
            directory.rmdir()

    monkeypatch.setattr(runner, "broker_scope", broker)
    return {
        "runtime": runtime,
        "image": image,
        "image_sha256": digest,
        "catalog": catalog,
        "auth_file": tmp_path / "explicit-unused-auth",
    }


def invoke(tmp_path: Path, route: dict[str, Any], fault: str = "") -> dict[str, Any]:
    source = tmp_path / "source"
    source.mkdir()
    (source / "scenario.json").write_text(
        json.dumps({"fault": fault, "original": str(source / "code.py")})
    )
    (source / "code.py").write_text("raise RuntimeError('must never execute on host')\n")
    frozen = tmp_path / "frozen"
    receipt = evidence.snapshot(source, frozen)
    if fault == "drift":
        # Point the synthetic runtime at the immutable original captured file.
        shutil.rmtree(frozen)
        (source / "scenario.json").write_text(
            json.dumps({"fault": fault, "original": str(frozen / "code.py")})
        )
        receipt = evidence.snapshot(source, frozen)
    return runner.run_native(
        frozen, receipt["manifest_sha256"], "Review the change.", tmp_path / "run", **route
    )


def test_complete_capture_exceeds_client_stdout_cap_and_preserves_originals(
    tmp_path: Path, route: dict[str, Any]
) -> None:
    report = invoke(tmp_path, route)
    assert report["status"] == "completed", report.get("error")
    assert report["cleanup_complete"] and report["observed_absent"] and report["observer_positive"]
    assert not (tmp_path / "run/runtime-state/image.tar").exists()
    assert json.loads((tmp_path / "run/image-retention.json").read_text())["status"] == "removed"
    assert not report["scored_admitted"] and report["task_quality"] == "unassessed"
    assert (tmp_path / "run/events.jsonl").stat().st_size > 65536
    assert report["usage"] == {"input_tokens": 3}
    assert report["commands"][0]["exit_code"] == 1
    assert report["input_changes"]["modified_or_removed"] == []
    assert report["input_changes"]["created"] == ["investigation/probe.txt"]
    assert report["original_input_unchanged"]
    assert (tmp_path / "run/final.txt").read_text() == "Final synthetic answer"
    assert not (tmp_path / "run/task").exists()  # Archive was never extracted.
    assert (tmp_path / "run/native-run.json").stat().st_mode & 0o077 == 0
    with pytest.raises(SkillEvalError, match="already exists"):
        runner.run_native(
            tmp_path / "frozen", report["input_digest"], "Again", tmp_path / "run", **route
        )


def test_large_command_output_stays_in_raw_artifacts_with_a_usable_receipt(
    tmp_path: Path, route: dict[str, Any]
) -> None:
    from skilleval.judge import _run_files

    report = invoke(tmp_path, route, "large-output")
    assert report["status"] == "completed", report.get("error")
    receipt = tmp_path / "run/native-run.json"
    assert receipt.stat().st_size < 4 * 1024 * 1024
    raw = (tmp_path / "run/events.jsonl").read_bytes()
    assert len(raw) > 5 * 1024 * 1024
    command = json.loads(raw.splitlines()[1])["item"]
    assert len(command["aggregated_output"]) > 5 * 1024 * 1024
    assert report["commands"][0]["output_bytes"] == len(command["aggregated_output"].encode())
    assert (
        report["commands"][0]["output_sha256"]
        == hashlib.sha256(command["aggregated_output"].encode()).hexdigest()
    )
    assert "aggregated_output" not in report["commands"][0]
    assert (
        report["commands"][0]["command_sha256"]
        == hashlib.sha256(b"synthetic-fixture-command").hexdigest()
    )
    verified, retained, _artifacts = _run_files(tmp_path / "run", mode="review")
    assert verified["status"] == "completed" and retained["events.jsonl"] == raw
    assert retained["native-audit.json"] == (tmp_path / "run/native-audit.json").read_bytes()


def test_execution_and_inventory_metadata_is_bounded_and_full_audit_retained(
    tmp_path: Path,
) -> None:
    parsed = runner.parse_codex_jsonl(json.dumps({"type": "error", "message": "x" * 5_000_000}))
    summary = runner._execution_metadata(parsed)
    assert len(runner._json(summary)) < 32_000
    assert summary["errors_count"] == 1 and summary["errors"][0]["bytes"] > 5_000_000
    inventory = [{"path": f"file-{index}-" + "x" * 500} for index in range(10_000)]
    report = {
        "schema_version": 1,
        "kind": "skilleval-native-run",
        "mode": "review",
        "status": "completed",
        "identity": {"large-inventory": inventory},
        "copied_input": {"sha256": "a" * 64, "files": inventory},
        "input_changes": {
            "created": [item["path"] for item in inventory],
            "modified_or_removed": [],
        },
        "execution": summary,
        "scored_admitted": False,
    }
    runner._publish_receipt(tmp_path, report)
    assert (tmp_path / "native-run.json").stat().st_size < 4 * 1024 * 1024
    assert report["copied_input"]["file_count"] == 10_000
    assert report["input_changes"]["created_count"] == 10_000
    assert report["input_changes"]["created_omitted"] == 10_000 - 64
    audit = json.loads((tmp_path / "native-audit.json").read_bytes())
    assert audit["copied_input"]["files"] == inventory
    assert len(audit["input_changes"]["created"]) == 10_000


def test_pre_vm_skill_failure_still_publishes_partial_receipt(
    tmp_path: Path, route: dict[str, Any]
) -> None:
    route["skill_bundle"] = tmp_path / "missing-skill-bundle"
    route["skill_digest"] = "a" * 64
    report = invoke(tmp_path, route)
    assert report["status"] == "partial" and report["cleanup_complete"]
    assert (tmp_path / "run/native-run.json").exists()
    assert json.loads((tmp_path / "run/native-audit.json").read_bytes())["status"] == "partial"


def test_audit_overflow_retains_private_bytes_and_usable_partial_record(
    tmp_path: Path, route: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from skilleval.judge import _run_files

    monkeypatch.setattr(runner, "MAX_NATIVE_AUDIT_BYTES", 16_384)
    report = invoke(tmp_path, route)
    assert report["status"] == "partial"
    assert report["error"] == "native metadata recording limit exceeded"
    overflow = tmp_path / "run/native-audit-overflow.json"
    assert overflow.stat().st_size > 16_384
    assert hashlib.sha256(overflow.read_bytes()).hexdigest() == report["audit_overflow"]["sha256"]
    assert (tmp_path / "run/native-audit.json").stat().st_size < 16_384
    verified, retained, _artifacts = _run_files(tmp_path / "run", mode="review")
    assert verified["status"] == "partial"
    assert json.loads(retained["native-audit.json"])["metadata_complete"] is False


@pytest.mark.parametrize(
    "fault,status",
    [
        ("events-error", "failed"),
        ("timed-out", "timed-out"),
        ("drift", "partial"),
        ("large-copy", "partial"),
    ],
)
def test_failures_remain_failures_with_evidence_and_machine_cleanup(
    tmp_path: Path, route: dict[str, Any], fault: str, status: str
) -> None:
    if fault == "large-copy":
        route["max_artifact_bytes"] = 1024 * 1024
    report = invoke(tmp_path, route, fault)
    assert report["status"] == status, report.get("error")
    assert report["cleanup_complete"] and report["observed_absent"]
    assert not report["scored_admitted"]
    if fault == "large-copy":
        assert (tmp_path / "run/artifacts.tar").stat().st_size <= 1024 * 1024
    if fault == "drift":
        assert report["original_input_unchanged"] is False
    if fault == "timed-out":
        assert report["process"]["timed_out"]
        assert (tmp_path / "run/events.jsonl").exists()


def test_input_drift_refused_before_vm_create(tmp_path: Path, route: dict[str, Any]) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "code.py").write_text("original")
    frozen = tmp_path / "frozen"
    receipt = evidence.snapshot(source, frozen)
    assert runner.snapshot_digest(frozen) == receipt["manifest_sha256"]
    (frozen / "code.py").chmod(0o600)
    (frozen / "code.py").write_text("tampered")
    with pytest.raises(SkillEvalError):
        runner.run_native(frozen, receipt["manifest_sha256"], "Review", tmp_path / "run", **route)
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("bad", ["../escape", "/absolute", "task/../../escape", "task//alias"])
def test_archive_validation_refuses_unsafe_names(tmp_path: Path, bad: str) -> None:
    archive = tmp_path / "artifacts.tar"
    with tarfile.open(archive, "w") as target:
        info = tarfile.TarInfo(bad)
        info.size = 1
        target.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(SkillEvalError, match="unsafe"):
        runner.validate_artifacts(archive, 1024 * 1024, 10)
    assert not (tmp_path / "escape").exists()


def test_archive_allowed_bytes_and_refused_links_aliases_limits(tmp_path: Path) -> None:
    archive = tmp_path / "artifacts.tar"
    runner._tar(archive, [("task/allowed", b"actual bytes", True)])
    receipt = runner.validate_artifacts(archive, 1024 * 1024, 10)
    assert receipt["entries"][0]["sha256"] == hashlib.sha256(b"actual bytes").hexdigest()
    for kind in ("link", "alias", "count", "bytes"):
        archive.unlink()
        with tarfile.open(archive, "w") as target:
            for name in ("task/file", "task/FILE") if kind != "count" else ("task/a", "task/b"):
                info = tarfile.TarInfo(name)
                if kind == "link":
                    info.type, info.linkname = tarfile.SYMTYPE, "/host/secret"
                else:
                    info.size = 1
                target.addfile(info, io.BytesIO(b"x"))
        with pytest.raises(SkillEvalError):
            runner.validate_artifacts(
                archive, 1 if kind == "bytes" else 1024 * 1024, 1 if kind == "count" else 10
            )


def test_extension_headers_are_rejected_before_tarfile_can_expand_them(tmp_path: Path) -> None:
    archive = tmp_path / "extension.tar"
    info = tarfile.TarInfo("hostile-pax")
    info.type, info.size = tarfile.XHDTYPE, 2**63 - 1
    archive.write_bytes(info.tobuf(format=tarfile.GNU_FORMAT))
    with pytest.raises(SkillEvalError, match=r"header|extension"):
        runner.validate_artifacts(archive, 1024 * 1024, 10)
    with pytest.raises(SkillEvalError, match=r"header|extension"):
        runner._member(archive, "evidence/final.txt", 1024)


@pytest.mark.parametrize("name", [".codex/config.toml", ".agents/skills/ambient/SKILL.md"])
def test_review_refuses_ambient_discovery_before_auth_or_vm(
    tmp_path: Path, route: dict[str, Any], name: str
) -> None:
    source = tmp_path / "source"
    target = source / name
    target.parent.mkdir(parents=True)
    target.write_text("ambient native instructions")
    frozen = tmp_path / "frozen"
    receipt = evidence.snapshot(source, frozen)
    with pytest.raises(SkillEvalError, match=r"ambient|native|skills|configuration"):
        runner.run_native(frozen, receipt["manifest_sha256"], "Review", tmp_path / "run", **route)
    assert not (tmp_path / "run").exists()


def test_guest_archive_preserves_link_text_without_reading_target(tmp_path: Path) -> None:
    root, task = tmp_path / "native", tmp_path / "task"
    root.mkdir()
    task.mkdir()
    for name in ("evidence", "home", "codex-home"):
        (root / name).mkdir()
    outside = tmp_path / "host-private"
    outside.write_text("private bytes must not enter archive")
    (task / "indirect").symlink_to(outside)
    (task / "regular").write_text("allowed bytes")
    result = guest_runner._archive(root, task, 1024 * 1024, 100)
    assert result["status"] == "complete"
    manifest = runner.validate_artifacts(root / "artifacts.tar", 1024 * 1024, 100)
    assert {entry["path"] for entry in manifest["entries"]} == {
        "task/regular",
        "evidence/filesystem-links.json",
    }
    assert b"private bytes" not in (root / "artifacts.tar").read_bytes()
    assert json.loads(
        runner._member(root / "artifacts.tar", "evidence/filesystem-links.json", 4096)
    )[0]["target"] == str(outside)
    assert guest_runner._archive(root, task, 1, 100)["status"] == "limit-exceeded"


def test_actual_native_child_deadline_retains_streamed_output_and_kills_group(
    tmp_path: Path,
) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("explicit input")
    child = tmp_path / "trusted-fixture.py"
    child.write_text(
        "import subprocess,sys,time\n"
        "print(sys.stdin.read(),flush=True)\n"
        "p=subprocess.Popen([sys.executable,'-c',"
        '"import signal,sys,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);'
        "print('descendant-ready',file=sys.stderr,flush=True);"
        "time.sleep(1.5);open('escaped','w').write('bad')\"])\n"
        "print('x'*90000,flush=True)\n"
        "time.sleep(30)\n"
    )
    result = guest_runner._execute_native(
        [sys.executable, str(child)], {"PATH": os.defpath}, prompt, tmp_path, tmp_path, 1
    )
    assert result["timed_out"] is True and result["returncode"] != 0
    assert 1 <= result["agent_elapsed_seconds"] < 8
    assert (tmp_path / "events.jsonl").stat().st_size > 90000
    assert (tmp_path / "events.jsonl").read_text().startswith("explicit input")
    assert "descendant-ready" in (tmp_path / "stderr.log").read_text()
    time.sleep(0.7)
    assert not (tmp_path / "escaped").exists()


def test_provider_failure_never_completes(
    tmp_path: Path, route: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    @contextmanager
    def broker(auth: Any, directory: Path, **kwargs: Any) -> Iterator[Any]:
        directory.mkdir(mode=0o700)
        try:
            yield SimpleNamespace(
                socket_path=directory / "inference.sock",
                clean=True,
                receipts=[{"event": "request", "outcome": "failed", "upstream_status": 500}],
            )
        finally:
            directory.rmdir()

    monkeypatch.setattr(runner, "broker_scope", broker)
    report = invoke(tmp_path, route)
    assert report["status"] == "failed"
    assert report["provider_complete"] is False and report["cleanup_complete"]


def test_cleanup_failure_retains_ticket_and_namespace_until_recovered(
    tmp_path: Path, route: dict[str, Any]
) -> None:
    report = invoke(tmp_path, route, "stop-failure")
    assert report["status"] == "partial" and report["cleanup_complete"] is False
    ticket = json.loads((tmp_path / "run/recovery.json").read_text())
    assert Path(ticket["runtime_home"]).is_dir()
    (tmp_path / "run/runtime-state/scenario.json").write_text("{}")
    from skilleval.lifecycle import recover

    assert recover(tmp_path / "run")["cleanup_complete"]
    assert not Path(ticket["runtime_home"]).exists()


def test_host_broker_observer_keeps_hashes_without_raw_values(
    tmp_path: Path, route: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = b'{"input":"PRIVATE_TASK_VALUE"}'

    @contextmanager
    def broker(auth: Any, directory: Path, **kwargs: Any) -> Iterator[Any]:
        directory.mkdir(mode=0o700)
        kwargs["request_observer"](secret)
        try:
            yield SimpleNamespace(
                socket_path=directory / "inference.sock",
                clean=True,
                receipts=[{"event": "request", "outcome": "complete", "upstream_status": 200}],
            )
        finally:
            directory.rmdir()

    monkeypatch.setattr(runner, "broker_scope", broker)
    report = invoke(tmp_path, route)
    assert report["status"] == "completed"
    assert report["request_observations"] == [
        {
            "index": 1,
            "input_sha256": hashlib.sha256(secret).hexdigest(),
            "input_bytes": len(secret),
            **runner.protocol_shape(secret),
        }
    ]
    assert b"PRIVATE_TASK_VALUE" not in (tmp_path / "run/native-run.json").read_bytes()
    assert report["request_observations"][0]["request_structure"]["input"] == {"type": "str"}


def test_available_skill_is_installed_outside_identical_task_and_prompt(
    tmp_path: Path, route: dict[str, Any]
) -> None:
    from skilleval.treatments import capture_skill

    skill = tmp_path / "skill"
    skill.mkdir()
    body = (
        "---\nname: review-control\ndescription: Inspect a synthetic review carefully.\n---\n"
        "Read the supplied change and report defects supported by source evidence.\n"
    )
    (skill / "SKILL.md").write_text(body)
    license_file = tmp_path / "LICENSE"
    license_file.write_text("Synthetic fixture license, permission granted for test use.\n")
    capture = capture_skill(
        skill,
        license_file,
        tmp_path / "skill-bundle",
        source_url="https://example.invalid/fixture",
        source_revision="synthetic-test-v1",
    )
    route["skill_bundle"] = tmp_path / "skill-bundle"
    route["skill_digest"] = capture["manifest_sha256"]
    report = invoke(tmp_path, route)
    assert report["status"] == "completed", report.get("error")
    assert report["skill_treatment"]["manifest_sha256"] == capture["manifest_sha256"]
    assert report["input_changes"]["modified_or_removed"] == []
    assert report["skill_consultation"]["consultation"] == "unmeasured"
    with tarfile.open(tmp_path / "run/runtime-state/tooling.tar") as archive:
        names = archive.getnames()
        assert "native/codex-home/skills/review-control/SKILL.md" in names
        assert not any(name.startswith("task/.agents/") for name in names)
        member = archive.extractfile("native/prompt.txt")
        assert member is not None and member.read() == b"Review the change."


def test_protocol_shape_retains_types_without_private_values() -> None:
    raw = json.dumps(
        {
            "input": [
                {
                    "type": "reasoning",
                    "content": None,
                    "encrypted_content": "PRIVATE",
                    "PRIVATE_KEY": "PRIVATE",
                }
            ]
        }
    ).encode()
    shape = json.dumps(runner.protocol_shape(raw))
    assert "PRIVATE" not in shape
    assert '"content": "NoneType"' in shape
    assert '"encrypted_content": "str"' in shape
    assert '"unknown_field_count": 1' in shape


def test_guest_preparation_accepts_pristine_existing_image_workdir(tmp_path: Path) -> None:
    archive = tmp_path / "tooling.tar"
    runner._tar(
        archive,
        [
            ("task/source.py", b"unexecuted source bytes", False),
            ("native/runner.py", b"unexecuted controller fixture", False),
        ],
    )
    task, native = tmp_path / "task", tmp_path / "native"
    task.mkdir()  # A prepared image's WORKDIR already exists.
    argv = runner.preparation_argv(archive, task, native)
    result = subprocess.run([sys.executable, *argv[1:]], capture_output=True, check=False)
    assert result.returncode == 0, result.stderr.decode()
    assert (task / "source.py").read_bytes() == b"unexecuted source bytes"
    assert (native / "runner.py").read_bytes() == b"unexecuted controller fixture"


@pytest.mark.parametrize("fault", ["symlink", "nonempty", "file", "native-nonempty"])
def test_guest_preparation_refuses_ambient_state_without_overwriting(
    tmp_path: Path, fault: str
) -> None:
    archive = tmp_path / "tooling.tar"
    runner._tar(
        archive,
        [
            ("task/source.py", b"captured bytes", False),
            ("native/runner.py", b"controller bytes", False),
        ],
    )
    task, native = tmp_path / "task", tmp_path / "native"
    outside = tmp_path / "outside"
    outside.mkdir()
    if fault == "symlink":
        task.symlink_to(outside, target_is_directory=True)
    elif fault == "nonempty":
        task.mkdir()
        (task / "source.py").write_text("ambient bytes")
    elif fault == "file":
        task.write_text("ambient file")
    else:
        native.mkdir()
        (native / "unrelated").write_text("ambient controller state")
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    argv = runner.preparation_argv(archive, task, native)
    result = subprocess.run([sys.executable, *argv[1:]], capture_output=True, check=False)
    assert result.returncode != 0
    assert all(path.read_bytes() == content for path, content in before.items())
    assert list(outside.iterdir()) == []
    if fault != "native-nonempty":
        assert not native.exists()
    else:
        assert not task.exists()


@pytest.mark.parametrize(
    "maximum, expected_status", [(256 * 1024 * 1024, 0), (257 * 1024 * 1024, 2)]
)
def test_native_cli_artifact_budget_reaches_bounded_runner(
    tmp_path, route, capsys, maximum, expected_status
):
    from skilleval import cli

    source = tmp_path / "source"
    source.mkdir()
    (source / "scenario.json").write_text("{}")
    (source / "code.py").write_text("raise RuntimeError('VM-only imported code')\n")
    frozen = evidence.snapshot(source, tmp_path / "frozen")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Review the source.")
    output = tmp_path / "run"
    status = cli.main(
        [
            "native",
            "qualify",
            str(tmp_path / "frozen"),
            "--sha256",
            frozen["manifest_sha256"],
            "--prompt",
            str(prompt),
            "--mode",
            "review",
            "--runtime",
            str(route["runtime"]),
            "--image-archive",
            str(route["image"]),
            "--image-sha256",
            route["image_sha256"],
            "--catalog",
            str(route["catalog"]),
            "--auth-file",
            str(route["auth_file"]),
            "--output",
            str(output),
            "--max-artifact-bytes",
            str(maximum),
            "--max-artifact-files",
            "9000",
        ]
    )
    assert status == expected_status
    captured = capsys.readouterr()
    if status == 0:
        report = json.loads(captured.out)
        assert report["status"] == "completed"
        assert report["artifact_limits"] == {"bytes": maximum, "files": 9000}
        assert report["cleanup_complete"] is True
    else:
        assert "invalid native workload" in captured.err
        assert not output.exists()
