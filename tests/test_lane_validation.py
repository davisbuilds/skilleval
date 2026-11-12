"""Authored runtime orchestration; no real VM, imported application, or isolation proof."""

import json
import shutil
import sys
from pathlib import Path

import pytest
from test_lifecycle import runtime_at
from test_repair import task as task
from test_runner import route as route
from test_task_package import package_spec

from skilleval import repair, task_environment
from skilleval.errors import SkillEvalError
from skilleval.identity import file_digest

RUNTIME = r"""
import json,os,pathlib,shutil,sys,tarfile
args=sys.argv[1:]
state=pathlib.Path(os.environ['XDG_CONFIG_HOME']).parent
machines=state/'machines.json'
names=json.loads(machines.read_text()) if machines.exists() else []
if args==['--version']: print('smolvm 1.32.3');raise SystemExit
with (state/'calls.jsonl').open('a') as log:log.write(json.dumps(args)+'\n')
with pathlib.Path(AUDIT_FILE).open('a') as log:
 log.write(json.dumps({'argv':args,'state':str(state)})+'\n')
name=args[args.index('--name')+1] if '--name' in args else None
if args[:2]==['machine','create']:
 names.append(name);machines.write_text(json.dumps(names))
 (state/'lanes.json').write_text('[]')
elif args[:2]==['machine','ls']:
 print(json.dumps([{'name':n} for n in names]));raise SystemExit
elif args[:2]==['machine','stop']:
 if (state/'fail-stop').exists():raise SystemExit(7)
elif args[:2]==['machine','delete']:
 names.remove(name);machines.write_text(json.dumps(names))
elif args[:2]==['machine','cp']:
 if ':/' in args[2]:
  shutil.copyfile(state/pathlib.PurePosixPath(args[2]).name,args[3])
 else:shutil.copyfile(args[2],state/'validation.tar')
elif args[:2]==['machine','exec']:
 command=args[args.index('--')+1:]
 if len(command)>2 and command[2]=='/var/tmp/native/validation.py':
  spec=json.loads(command[3])
  assert spec['profile']=='native-root-v1', 'no synthetic solver-boundary proof'
  lanes=json.loads((state/'lanes.json').read_text())
  if spec['argv'][1]=='supervisor-failure':raise SystemExit(9)
  if spec['argv'][1]=='cleanup-failure':(state/'fail-stop').write_text('authored failure')
  if spec['argv'][1]=='input-drift':
   target=pathlib.Path(spec['argv'][2]);target.chmod(0o600);target.write_text('drift')
  process={'argv':spec['argv'],'returncode':0,'timed_out':False,
   'output_limit_reached':False,'stdout':json.dumps({'prior_lanes':len(lanes)}),'stderr':'',
   'profile':{'uid':0,'gid':0,'groups':[],'cwd':'/workspace/task'},'source_changes':[]}
  (state/pathlib.Path(spec['report_path']).name).write_text(json.dumps(process))
  lanes.append(spec['argv']);(state/'lanes.json').write_text(json.dumps(lanes))
 else:
  with tarfile.open(state/'validation.tar') as source:
   assert source.extractfile('task/code.py').read().startswith(b'raise RuntimeError')
print('ok')
"""


@pytest.fixture
def validation_setup(task, route, tmp_path):
    runtime = runtime_at(tmp_path / "validation-runtime")
    runtime.write_text(
        f"#!{sys.executable}\nAUDIT_FILE={str(tmp_path / 'validation-calls.jsonl')!r}\n" + RUNTIME
    )
    spec = package_spec(task, route)
    first = (
        spec.validation[0]
        .lanes[0]
        .model_copy(
            update={
                "lane_id": "first",
                "argv": ("authored-lane", "first"),
                "parser": "json",
                "expected": {"prior_lanes": 0},
            }
        )
    )
    second = first.model_copy(update={"lane_id": "second", "argv": ("authored-lane", "second")})
    case = spec.validation[0].model_copy(update={"lanes": (first, second)})
    spec = spec.model_copy(update={"validation": (case,)})
    package = tmp_path / "package.json"
    digest = repair._publish(
        package,
        {
            "schema_version": 1,
            "kind": "skilleval-task-package",
            "spec": spec.model_dump(mode="json"),
        },
    )
    return spec, package, digest, runtime


def independent_package(setup, destination, *, argv=None):
    spec, _, _, runtime = setup
    case = spec.validation[0]
    if argv is not None:
        case = case.model_copy(
            update={
                "lanes": (
                    case.lanes[0],
                    case.lanes[1].model_copy(update={"argv": argv}),
                )
            }
        )
    spec = spec.model_copy(
        update={
            "environment": spec.environment.model_copy(update={"validation_isolation": "lane-v1"}),
            "validation": (case,),
        }
    )
    digest = repair._publish(
        destination,
        {
            "schema_version": 1,
            "kind": "skilleval-task-package",
            "spec": spec.model_dump(mode="json"),
        },
    )
    return spec, destination, digest, runtime


def creations(runtime):
    audit = runtime.parent.parent / "validation-calls.jsonl"
    rows = [json.loads(line) for line in audit.read_text().splitlines()]
    return [row for row in rows if row["argv"][:2] == ["machine", "create"]]


def test_shared_case_still_executes_lanes_in_one_lifecycle(validation_setup, tmp_path):
    _, package, digest, runtime = validation_setup
    output = tmp_path / "shared-case"
    report = task_environment.validate_environment(package, digest, output, runtime=runtime)
    assert report["schema_version"] == 2
    case = report["cases"][0]
    assert case["cleanup_complete"] is True
    assert case["status"] == "unexpected"
    assert [lane["observed"] for lane in case["lanes"]] == [
        {"prior_lanes": 0},
        {"prior_lanes": 1},
    ]
    assert (output / "base/validation.json").is_file()


def test_shared_case_engine_rejects_independent_policy_before_machine_creation(
    validation_setup, tmp_path
):
    _, package, digest, runtime = independent_package(
        validation_setup, tmp_path / "independent.json"
    )
    output = tmp_path / "wrong-engine"
    with pytest.raises(SkillEvalError, match="requires validation_isolation case-v1"):
        task_environment.validate_environment(package, digest, output, runtime=runtime)
    assert not output.exists()
    assert not (tmp_path / "validation-calls.jsonl").exists()


def test_single_lane_case_keeps_original_package_binding(validation_setup, tmp_path):
    spec, package, digest, runtime = validation_setup
    case = spec.validation[0]
    row = task_environment._run_case(
        spec,
        case.model_copy(update={"lanes": (case.lanes[1],)}),
        tmp_path / "single-lane",
        package=package,
        digest=digest,
        runtime=runtime,
    )
    assert row["status"] == "matched"
    assert row["cleanup_complete"] is True
    assert [lane["lane_id"] for lane in row["lanes"]] == ["second"]
    assert row["lanes"][0]["observed"] == {"prior_lanes": 0}


def test_single_lane_rechecks_entire_original_input(validation_setup, task, tmp_path):
    spec, package, digest, runtime = validation_setup
    case = spec.validation[0]
    lane = case.lanes[0].model_copy(
        update={"argv": ("authored-lane", "input-drift", str(task["base"] / "README.md"))}
    )
    row = task_environment._run_case(
        spec,
        case.model_copy(update={"lanes": (lane,)}),
        tmp_path / "drift",
        package=package,
        digest=digest,
        runtime=runtime,
    )
    assert row["status"] == "incomplete"
    assert row["error"]
    assert row["cleanup_complete"] is True
    assert row["lanes"][0]["observed"] == {"prior_lanes": 0}


def test_failed_supervisor_retains_incomplete_row_and_cleanup(validation_setup, tmp_path):
    spec, package, digest, runtime = validation_setup
    case = spec.validation[0]
    lane = case.lanes[0].model_copy(update={"argv": ("authored-lane", "supervisor-failure")})
    directory = tmp_path / "failed-lane"
    row = task_environment._run_case(
        spec,
        case.model_copy(update={"lanes": (lane,)}),
        directory,
        package=package,
        digest=digest,
        runtime=runtime,
    )
    assert row["status"] == "incomplete"
    assert row["lanes"] == []
    assert row["cleanup_complete"] is True
    assert json.loads((directory / "validation.json").read_text()) == row


def remove_runtime_home(directory):
    """Clean only a synthetic retained HOME after exercising failed cleanup."""
    ticket = json.loads((directory / "recovery.json").read_text())
    shutil.rmtree(ticket["runtime_home"])


def test_cleanup_failure_retains_ticket_and_prevents_later_case(validation_setup, tmp_path):
    spec, package, _, runtime = validation_setup
    case = spec.validation[0]
    failed = case.lanes[0].model_copy(update={"argv": ("authored-lane", "cleanup-failure")})
    spec = spec.model_copy(
        update={
            "validation": (
                case.model_copy(update={"lanes": (failed,)}),
                case.model_copy(update={"case_id": "later", "role": "reference"}),
            )
        }
    )
    package = package.with_name("cleanup-package.json")
    digest = repair._publish(
        package,
        {
            "schema_version": 1,
            "kind": "skilleval-task-package",
            "spec": spec.model_dump(mode="json"),
        },
    )
    output = tmp_path / "failed-cleanup"
    try:
        report = task_environment.validate_environment(package, digest, output, runtime=runtime)
        assert report["status"] == "incomplete"
        assert len(report["cases"]) == 1
        assert report["cases"][0]["cleanup_complete"] is False
        assert not (output / "later").exists()
        ticket = json.loads((output / "base/recovery.json").read_text())
        assert ticket["cleanup_complete"] is False
        assert ticket["create_attempted"] is True
    finally:
        remove_runtime_home(output / "base")


def test_independent_lanes_each_create_fresh_lifecycle(validation_setup, tmp_path):
    from skilleval import lane_validation

    _, package, digest, runtime = independent_package(
        validation_setup, tmp_path / "independent.json"
    )
    output = tmp_path / "independent"
    report = lane_validation.validate_environment(package, digest, output, runtime=runtime)
    assert report["schema_version"] == 3
    assert report["status"] == "matched"
    lanes = report["cases"][0]["lanes"]
    assert [lane["action"] for lane in lanes] == ["execute", "execute"]
    assert [lane["comparison"]["observed"] for lane in lanes] == [
        {"prior_lanes": 0},
        {"prior_lanes": 0},
    ]
    assert len(creations(runtime)) == 2
    assert len({row["state"] for row in creations(runtime)}) == 2
    for lane in ("first", "second"):
        assert (output / "executions/base" / lane / "lane-execution.json").is_file()


def test_all_reuse_keeps_receipts_and_launches_no_machine(validation_setup, tmp_path):
    from skilleval import lane_validation

    _, package, digest, runtime = independent_package(
        validation_setup, tmp_path / "independent.json"
    )
    original = tmp_path / "original"
    first = lane_validation.validate_environment(package, digest, original, runtime=runtime)
    sources = [lane["source"] for lane in first["cases"][0]["lanes"]]
    original_bytes = {source["receipt"]: open_receipt(source) for source in sources}
    output = tmp_path / "reused"
    report = lane_validation.validate_environment(
        package,
        digest,
        output,
        runtime=runtime,
        reuse=original / "task-validation.json",
        reuse_sha256=file_digest(original / "task-validation.json"),
    )
    assert report["status"] == "matched"
    assert [lane["action"] for lane in report["cases"][0]["lanes"]] == ["reuse", "reuse"]
    assert [lane["source"] for lane in report["cases"][0]["lanes"]] == sources
    assert len(creations(runtime)) == 2
    assert not (output / "executions").exists()
    assert {source["receipt"]: open_receipt(source) for source in sources} == original_bytes


def open_receipt(source):
    return Path(source["receipt"]).read_bytes()


def test_only_changed_lane_executes_after_pinned_reuse(validation_setup, tmp_path):
    from skilleval import lane_validation

    _, package, digest, runtime = independent_package(
        validation_setup, tmp_path / "independent.json"
    )
    original = tmp_path / "original"
    first = lane_validation.validate_environment(package, digest, original, runtime=runtime)
    _, changed, changed_digest, _ = independent_package(
        validation_setup,
        tmp_path / "changed.json",
        argv=("authored-lane", "changed-second"),
    )
    output = tmp_path / "changed"
    report = lane_validation.validate_environment(
        changed,
        changed_digest,
        output,
        runtime=runtime,
        reuse=original / "task-validation.json",
        reuse_sha256=file_digest(original / "task-validation.json"),
    )
    assert report["status"] == "matched"
    lanes = report["cases"][0]["lanes"]
    assert [lane["action"] for lane in lanes] == ["reuse", "execute"]
    assert lanes[0]["source"] == first["cases"][0]["lanes"][0]["source"]
    assert len(creations(runtime)) == 3
    assert not (output / "executions/base/first").exists()
    assert lanes[1]["comparison"]["observed"] == {"prior_lanes": 0}


@pytest.mark.parametrize("failure", ["supervisor-failure", "cleanup-failure"])
def test_incomplete_lane_keeps_envelope_and_stops_on_failed_cleanup(
    validation_setup, tmp_path, failure
):
    from skilleval import lane_validation

    spec, package, digest, runtime = independent_package(
        validation_setup,
        tmp_path / "failed.json",
        argv=("authored-lane", failure),
    )
    # Put failure first so failed cleanup must prevent the second launch.
    case = spec.validation[0]
    spec = spec.model_copy(
        update={"validation": (case.model_copy(update={"lanes": tuple(reversed(case.lanes))}),)}
    )
    package = package.with_name("failure-first.json")
    digest = repair._publish(
        package,
        {
            "schema_version": 1,
            "kind": "skilleval-task-package",
            "spec": spec.model_dump(mode="json"),
        },
    )
    output = tmp_path / "failed"
    try:
        report = lane_validation.validate_environment(package, digest, output, runtime=runtime)
        assert report["status"] == "incomplete"
        assert (output / "executions/base/second/lane-execution.json").is_file()
        if failure == "cleanup-failure":
            assert len(creations(runtime)) == 1
            assert not (output / "executions/base/first").exists()
        else:
            assert len(creations(runtime)) == 2
            assert (output / "executions/base/first/lane-execution.json").is_file()
    finally:
        if failure == "cleanup-failure":
            remove_runtime_home(output / "executions/base/second")


def test_preparation_failure_keeps_incomplete_envelope_without_invented_identity(
    validation_setup, tmp_path, monkeypatch
):
    from skilleval import lane_readiness, lane_validation

    _, package, digest, runtime = independent_package(
        validation_setup, tmp_path / "independent.json"
    )
    original_plan = lane_readiness.plan_validation

    def remove_runtime_after_planning(*args, **kwargs):
        planned = original_plan(*args, **kwargs)
        runtime.unlink()
        return planned

    monkeypatch.setattr(lane_readiness, "plan_validation", remove_runtime_after_planning)
    output = tmp_path / "preparation-failure"
    report = lane_validation.validate_environment(package, digest, output, runtime=runtime)
    assert report["status"] == "incomplete"
    receipt = output / "executions/base/first/lane-execution.json"
    assert receipt.is_file()
    row = json.loads(receipt.read_text())["case"]
    assert row["status"] == "incomplete"
    assert row["cleanup_complete"] is False
    assert "identity" not in row and "image" not in row
    assert not (output / "executions/base/first/recovery.json").exists()
    assert not (output / "executions/base/second").exists()


def test_input_drift_retains_execution_envelope_without_publishing_aggregate(
    validation_setup, task, tmp_path
):
    from skilleval import lane_validation

    _, package, digest, runtime = independent_package(
        validation_setup,
        tmp_path / "independent.json",
        argv=("authored-lane", "input-drift", str(task["base"] / "README.md")),
    )
    output = tmp_path / "input-drift"
    with pytest.raises(SkillEvalError):
        lane_validation.validate_environment(package, digest, output, runtime=runtime)
    assert (output / "executions/base/second/lane-execution.json").is_file()
    assert not (output / "task-validation.json").exists()
