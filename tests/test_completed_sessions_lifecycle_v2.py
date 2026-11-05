"""Offline authored-control checks; never import or execute AgentMonitor."""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from skilleval import evidence, repair
from skilleval.errors import SkillEvalError

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/repair/completed-sessions"
HERE = EXAMPLE / "lifecycle-v2"


def load_module(name):
    spec = importlib.util.spec_from_file_location(f"lifecycle_v2_{name}", HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def probes():
    requests = json.loads((HERE / "requests.json").read_text())
    expected = json.loads((HERE / "expected.json").read_text())
    return (
        {row["probe_id"]: row["request"] for row in requests},
        {row["probe_id"]: row["expected"] for row in expected},
    )


def test_authored_handover_oracle_requires_restoration_after_a_strict_prefix():
    requests, expected = probes()
    request = requests["cached-sibling-handover"]
    fixture = request["fixture"]
    full = fixture["full"]
    prefix = full[: request["prefix_lines"]]
    assert len(prefix) == 6 < len(full)
    own = [row["payload"]["usage"] for row in full if row["type"] == "token_usage_record"]
    assert len(own) == 2
    assert sum(row["input_tokens"] + row["output_tokens"] for row in own) == 1220
    assert own[0]["input_tokens"] + own[0]["output_tokens"] == 840
    final = expected["cached-sibling-handover"]
    for stage, records in (
        ("initial", full),
        ("shortened", prefix),
        ("restored", full),
        ("repeated", full),
    ):
        state = final[stage]
        assert state["browser_ids"] == [fixture["stem"]]
        authored_messages = [row["payload"] for row in records if row["type"] == "response_item"]
        assert len(state["messages"]) == len(authored_messages)
        for ordinal, (actual, authored) in enumerate(
            zip(state["messages"], authored_messages, strict=True)
        ):
            assert actual == {
                "ordinal": ordinal,
                "role": authored["role"],
                "content": [{"type": "text", "text": authored["content"][0]["text"]}],
            }
        authored_usage = [row["payload"] for row in records if row["type"] == "token_usage_record"]
        assert len(state["usage"]) == len(authored_usage)
        for actual, authored in zip(state["usage"], authored_usage, strict=True):
            tokens = authored["usage"]
            assert actual == {
                "tokens_in": tokens["input_tokens"] - tokens["cached_input_tokens"],
                "cache_read_tokens": tokens["cached_input_tokens"],
                "tokens_out": tokens["output_tokens"],
                "response_id": authored["response_id"],
            }
    assert final["initial"] != final["shortened"]
    assert final["initial"] == final["restored"] == final["repeated"]
    assert final["archive_unchanged"] is True


def test_reader_control_has_distinct_checkable_sessions_and_no_pinned_limit_order():
    requests, expected = probes()
    request = requests["reader-limit"]
    assert request["archive"]["session_id"] != request["plain"]["session_id"]
    assert request["archive"]["stem"] != request["plain"]["stem"]
    for name in ("archive", "plain"):
        records = request[name]["full"]
        last_usage = [row["payload"] for row in records if row["type"] == "token_usage_record"][-1]
        assert (
            last_usage["thread_token_usage"]
            == records[-2 if name == "archive" else -1]["payload"]["info"]["total_token_usage"]
        )
    for limit in (1, 2):
        assert expected["reader-limit"][f"limit{limit}"] == {
            "session_count": limit,
            "checked_count": limit,
            "distinct_sessions": limit,
            "values_match_fixture": True,
        }


def test_host_execution_is_refused_before_starting_any_application_process(tmp_path, monkeypatch):
    worker = load_module("worker")
    monkeypatch.setattr(worker.sys, "platform", "darwin")
    with pytest.raises(RuntimeError, match="Linux VM repair-check UID 65534"):
        worker.observe(tmp_path, {})
    monkeypatch.setattr(worker.sys, "platform", "linux")
    monkeypatch.setattr(worker.os, "getuid", lambda: 0)
    with pytest.raises(RuntimeError, match="Linux VM repair-check UID 65534"):
        worker.observe(tmp_path, {})


def test_authored_transport_preserves_order_errors_and_separate_diagnostics(
    tmp_path, monkeypatch, capsys
):
    worker = load_module("worker")
    # Bypass only the Python platform guard for a synthetic Python executable;
    # no Node/application import is executed by this transport test.
    monkeypatch.setattr(worker, "require_guest", lambda: None)
    executable = tmp_path / "authored-transport"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "row = json.load(sys.stdin)\n"
        "print('authored diagnostic', file=sys.stderr)\n"
        "print(json.dumps({'received': row['number']}))\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("SKILLEVAL_NODE", str(executable))
    monkeypatch.setenv("SKILLEVAL_TSX_IMPORT", "authored-loader")
    assert [worker.observe(tmp_path, {"number": n}) for n in (2, 1)] == [
        {"received": 2},
        {"received": 1},
    ]
    assert capsys.readouterr().err == "authored diagnostic\nauthored diagnostic\n"
    executable.write_text(f"#!{sys.executable}\nraise SystemExit(7)\n")
    with pytest.raises(RuntimeError, match="process exited 7"):
        worker.observe(tmp_path, {})


def test_embedded_node_adapter_parses_without_executing_imports():
    node = shutil.which("node")
    if node is None:
        pytest.skip("existing Node executable is not available for syntax-only check")
    result = subprocess.run(
        [node, "--input-type=module", "--check"],
        input=load_module("worker").NODE_ADAPTER,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_preparation_freezes_new_identity_without_changing_legacy_or_executing_source(tmp_path):
    module = load_module("prepare")
    source = tmp_path / "source"
    source.mkdir()
    (source / "unsafe.py").write_text("raise RuntimeError('must never execute')\n")
    captured = evidence.snapshot(source, tmp_path / "capture")
    legacy_before = {
        name: (EXAMPLE / name).read_bytes()
        for name in ("worker.py", "requests.json", "expected.json", "prompt.txt")
    }
    result = module.prepare(
        tmp_path / "capture", captured["manifest_sha256"], tmp_path / "lifecycle-task.json"
    )
    task = repair.load_task(Path(result["path"]), result["sha256"])
    assert task.task_id == "completed-sessions-lifecycle-v2"
    assert [row.probe_id for row in task.probes] == ["cached-sibling-handover", "reader-limit"]
    assert task.prompt == legacy_before["prompt.txt"].decode()
    assert task.observer_resource_profile == "node-v1"
    assert task.worker_sha256 == result["worker_sha256"]
    assert Path(task.worker).read_bytes() == (HERE / "worker.py").read_bytes()
    assert Path(task.worker).stat().st_mode & 0o777 == 0o444
    for name, raw in legacy_before.items():
        assert (EXAMPLE / name).read_bytes() == raw
    # An existing retained candidate can enter this generation as a fresh
    # authored control; this creates no new native attempt or app execution.
    control = repair.collect_control(
        Path(result["path"]),
        result["sha256"],
        tmp_path / "capture",
        captured["manifest_sha256"],
        tmp_path / "new-control",
    )
    assert control["status"] == "collected"
    with pytest.raises(FileExistsError):
        module.prepare(
            tmp_path / "capture", captured["manifest_sha256"], tmp_path / "lifecycle-task.json"
        )


def test_failed_preparation_rejects_overlap_and_rolls_back_fresh_worker(tmp_path):
    module = load_module("prepare")
    source = tmp_path / "source"
    source.mkdir()
    (source / "safe.txt").write_text("authored inert fixture\n")
    capture = tmp_path / "capture"
    captured = evidence.snapshot(source, capture)
    with pytest.raises(ValueError, match="outside solver input"):
        module.prepare(capture, captured["manifest_sha256"], capture / "overlap.json")
    output = tmp_path / "task.json"
    with pytest.raises(SkillEvalError):
        module.prepare(capture, "0" * 64, output)
    assert not output.exists()
    assert not output.with_suffix(".worker.py").exists()
    assert module.prepare(capture, captured["manifest_sha256"], output)["status"] == "prepared"
