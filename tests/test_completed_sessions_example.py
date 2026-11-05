"""Authored fixture contracts only; AgentMonitor is never imported on the host."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/repair/completed-sessions"


def load_worker():
    spec = importlib.util.spec_from_file_location(
        "completed_sessions_worker", EXAMPLE / "worker.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_probes():
    requests = json.loads((EXAMPLE / "requests.json").read_text())
    expected = json.loads((EXAMPLE / "expected.json").read_text())
    return requests, {row["probe_id"]: row["expected"] for row in expected}


def test_rollouts_retain_a_real_prefix_and_add_distinct_late_messages_and_usage():
    requests, expected = load_probes()
    assert len(requests) == len(expected) == 6
    assert len({row["probe_id"] for row in requests}) == 6
    for probe in requests[:4]:
        request = probe["request"]
        initial, final = request["initial"], request["final"]
        assert final[: len(initial)] == initial
        assert len(final) > len(initial)
        own = [row["payload"] for row in final if row["type"] == "token_usage_record"]
        assert [row["response_id"] for row in own] == ["request-alpha", "request-beta"]
        total = own[-1]["thread_token_usage"]
        for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
            assert sum(row["usage"][key] for row in own) == total[key]
        final_state = expected[probe["probe_id"]]["stages"][-1]["state"]
        direct = expected[probe["probe_id"]]["direct"]
        assert direct["content_matches_fixture"] is True
        assert direct["hash_matches_content"] is True
        assert "content_sha256" not in direct
        assert final_state["totals"][0]["harness"] == 1845
        assert final_state["totals"][0]["amon"] == (total["input_tokens"] + total["output_tokens"])
        assert [row["ordinal"] for row in final_state["messages"]] == [0, 1, 2, 3]
        assert final_state["messages"][0]["content"] == [
            {"type": "text", "text": "Check the ledger; preserve café and Ω."}
        ]
        assert final_state["messages"][1]["content"] == [
            {"type": "text", "text": "First pass complete.\nOne entry remains."}
        ]
        # Cache tokens are a subset of input, not extra usage.
        assert sum(row["tokens_in"] for row in final_state["usage"]) == 1130
        assert sum(row["cache_read_tokens"] for row in final_state["usage"]) == 570


def test_fixtures_distinguish_representation_transition_and_failure_contracts():
    requests, expected = load_probes()
    keyed = {row["probe_id"]: row["request"] for row in requests}
    transition = expected["transition"]
    assert transition["stages"][0]["discovery"]["import"] == [
        keyed["transition"]["stem"] + ".jsonl"
    ]
    assert transition["stages"][1]["discovery"]["import"] == [
        keyed["transition"]["stem"] + ".jsonl.zst"
    ]
    assert transition["transition_identity"] == {
        "messages": {"before_count": 2, "retained_count": 2},
        "events": {"before_count": 3, "retained_count": 3},
    }
    both = expected["both-forms"]
    assert both["stages"][0]["discovery"]["browser"] == [keyed["both-forms"]["stem"] + ".jsonl"]
    assert both["compressed_only"]["state"] == both["stages"][0]["state"]
    assert expected["plain-append"]["stages"][-1]["sync"] == {"total": 1, "errors": 0}
    assert expected["corrupt-compressed"]["read_raised"] is True
    assert expected["corrupt-compressed"]["sync_result"] == "error"
    fallback = keyed["fallback-name"]
    assert all(row["type"] != "session_meta" for row in fallback["final"])
    assert expected["fallback-name"]["event_session_ids"] == [fallback["stem"]]
    for probe in requests[:4]:
        repair = expected[probe["probe_id"]]["project_repair"]
        assert repair["preview"] == repair["applied"]
        assert repair["preview_state"]["events"] == ["aurora-task"]
        assert repair["applied_state"]["events"] == ["aurora"]
        assert repair["again"] == {"events": 0, "sessions": 0, "browsing_sessions": 0}


def test_wrapper_preserves_request_order_and_keeps_diagnostics_outside_json(tmp_path, monkeypatch):
    """A tiny authored executable checks transport, never candidate behavior."""
    worker = load_worker()
    executable = tmp_path / "authored-node"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "request = json.load(sys.stdin)\n"
        "print('authored diagnostic', file=sys.stderr)\n"
        "print(json.dumps({'received': request['sequence']}))\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("SKILLEVAL_NODE", str(executable))
    monkeypatch.setenv("SKILLEVAL_TSX_IMPORT", "authored-loader")
    actual = [worker.observe(tmp_path, {"sequence": n}) for n in (3, 1, 2)]
    assert actual == [{"received": 3}, {"received": 1}, {"received": 2}]
    executable.write_text(f"#!{sys.executable}\nraise SystemExit(7)\n")
    with pytest.raises(RuntimeError, match="process exited 7"):
        worker.observe(tmp_path, {"sequence": 0})


def test_prepare_task_freezes_probes_and_scope_without_executing_captured_code(
    tmp_path, monkeypatch
):
    from skilleval import evidence, repair

    source = tmp_path / "source"
    source.mkdir()
    (source / "source.py").write_text("raise RuntimeError('must never execute on host')\n")
    captured = evidence.snapshot(source, tmp_path / "capture")
    spec = importlib.util.spec_from_file_location(
        "prepare_completed_task", EXAMPLE / "prepare_task.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    example = tmp_path / "authored-example"
    example.mkdir()
    for name in ("worker.py", "requests.json", "expected.json", "prompt.txt"):
        (example / name).write_bytes((EXAMPLE / name).read_bytes())
    monkeypatch.setattr(module, "EXAMPLE", example)
    with pytest.raises(ValueError, match="outside solver input"):
        module.prepare(
            tmp_path / "capture", captured["manifest_sha256"], tmp_path / "capture/refused.json"
        )
    assert not (tmp_path / "capture/refused.json").exists()
    assert not (tmp_path / "capture/refused.worker.py").exists()
    existing_worker = tmp_path / "existing.worker.py"
    existing_worker.write_bytes(b"preserve existing worker")
    with pytest.raises(FileExistsError):
        module.prepare(
            tmp_path / "capture", captured["manifest_sha256"], tmp_path / "existing.json"
        )
    assert existing_worker.read_bytes() == b"preserve existing worker"
    assert not (tmp_path / "existing.json").exists()
    linked_worker = tmp_path / "linked.worker.py"
    linked_worker.symlink_to(tmp_path / "capture/source.py")
    with pytest.raises(ValueError, match="outside solver input"):
        module.prepare(tmp_path / "capture", captured["manifest_sha256"], tmp_path / "linked.json")
    assert not (tmp_path / "linked.json").exists()
    assert (tmp_path / "capture/source.py").read_bytes() == (source / "source.py").read_bytes()
    with pytest.raises(FileNotFoundError):
        module.prepare(
            tmp_path / "capture", captured["manifest_sha256"], tmp_path / "missing/task.json"
        )
    assert not (tmp_path / "missing").exists()
    result = module.prepare(
        tmp_path / "capture", captured["manifest_sha256"], tmp_path / "task.json"
    )
    task = repair.load_task(Path(result["path"]), result["sha256"])
    assert task.observer_resource_profile == "node-v1"
    assert task.editable_directories == (
        "src",
        "tests",
        "scripts",
        "docs",
        "dist",
        "frontend/src",
        "frontend/tests",
        "frontend/dist",
    )
    assert len(task.probes) == 6
    assert task.prompt == (EXAMPLE / "prompt.txt").read_text()
    assert task.source_revision == module.BASELINE
    frozen_worker = tmp_path / "task.worker.py"
    assert Path(task.worker) == frozen_worker
    assert result["worker"] == str(frozen_worker)
    assert result["worker_sha256"] == task.worker_sha256
    original = frozen_worker.read_bytes()
    (example / "worker.py").write_text("changed authored template\n")
    assert frozen_worker.read_bytes() == original
    assert repair.load_task(Path(result["path"]), result["sha256"]) == task
    with pytest.raises(FileExistsError):
        module.prepare(tmp_path / "capture", captured["manifest_sha256"], tmp_path / "task.json")
    assert frozen_worker.read_bytes() == original


@pytest.mark.parametrize("fault", ["wrong-manifest", "missing-capture"])
def test_failed_task_preparation_removes_new_worker_and_allows_same_output_retry(tmp_path, fault):
    from skilleval import evidence, repair
    from skilleval.errors import SkillEvalError

    source = tmp_path / "source"
    source.mkdir()
    (source / "code.py").write_text("raise RuntimeError('must never execute on host')\n")
    capture = tmp_path / "capture"
    captured = evidence.snapshot(source, capture)
    spec = importlib.util.spec_from_file_location(
        "prepare_completed_task_rollback", EXAMPLE / "prepare_task.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = tmp_path / "task.json"
    frozen_worker = tmp_path / "task.worker.py"
    invalid_capture = tmp_path / "missing-capture" if fault == "missing-capture" else capture
    invalid_digest = "0" * 64 if fault == "wrong-manifest" else captured["manifest_sha256"]
    with pytest.raises(SkillEvalError):
        module.prepare(invalid_capture, invalid_digest, output)
    assert not output.exists()
    assert not frozen_worker.exists()
    if fault == "missing-capture":
        assert not invalid_capture.exists()
    result = module.prepare(capture, captured["manifest_sha256"], output)
    task = repair.load_task(output, result["sha256"])
    assert task.worker == str(frozen_worker)


def test_worker_rollback_failure_preserves_original_preparation_error(tmp_path, monkeypatch):
    from skilleval import evidence
    from skilleval.errors import SkillEvalError

    source = tmp_path / "source"
    source.mkdir()
    (source / "code.py").write_text("authored source\n")
    capture = tmp_path / "capture"
    evidence.snapshot(source, capture)
    spec = importlib.util.spec_from_file_location(
        "prepare_completed_task_cleanup_failure", EXAMPLE / "prepare_task.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original_unlink = Path.unlink

    def fail_worker_cleanup(path, *args, **kwargs):
        if path == tmp_path / "task.worker.py":
            raise PermissionError("authored cleanup failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_worker_cleanup)
    with pytest.raises(SkillEvalError, match="digest"):
        module.prepare(capture, "0" * 64, tmp_path / "task.json")
    assert not (tmp_path / "task.json").exists()
