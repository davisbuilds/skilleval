"""Authored processes test evidence transport; imported AgentMonitor runs only in VM.

These tests cannot qualify the embedded production API integration. That requires
executing the application-home lane against the actual task source inside the VM.
"""

import json
import os
import sys

from test_session_environment import worker


def authored_runtime(tmp_path, monkeypatch):
    source = tmp_path / "task"
    fixture = source / "sample-history/codex-home/sessions"
    fixture.mkdir(parents=True)
    (fixture / "plain.jsonl").write_text('{"synthetic":true}\n')
    (source / "frontend").mkdir()
    dependencies = tmp_path / "dependencies"
    (dependencies / ".bin").mkdir(parents=True)
    commands = tmp_path / "bin"
    commands.mkdir()
    node = commands / "node"
    node.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json, os, pathlib, sqlite3, sys
home = pathlib.Path(os.environ['CODEX_HOME'])
assert (home / 'sessions/plain.jsonl').read_text() == '{"synthetic":true}\n'
db = sqlite3.connect(os.environ['AGENTMONITOR_DB_PATH'])
db.execute('CREATE TABLE imported (value TEXT)')
db.execute('INSERT INTO imported VALUES (?)', ('synthetic',))
db.commit()
db.close()
if not os.environ.get('OMIT_SUPPORT'):
    support = home / '.tmp' / str(os.getpid())
    support.mkdir(parents=True)
    (support / 'alias').symlink_to('/nonexistent-synthetic-executable')
if os.environ.get('DAMAGE_FIXTURE'):
    (home / 'sessions/plain.jsonl').unlink()
payload = json.dumps({
    'discovery':['sessions/plain.jsonl'], 'import_files':1, 'imported_events':1,
    'database_created':True,
    'quota':{'status':'error', 'initialized':True, 'expected_timeout':True,
             'suppressed_methods':['account/read','account/rateLimits/read']},
}) + '\n'
sys.stdout.write(payload)
if os.environ.get('OUTPUT_BYTES'):
    remaining = int(os.environ['OUTPUT_BYTES']) - len(payload.encode())
    assert remaining >= 4
    sys.stderr.write('x' * (remaining - 4) + 'TAIL')
"""
    )
    node.chmod(0o755)
    monkeypatch.setenv("PATH", str(commands) + os.pathsep + os.environ["PATH"])
    return source, dependencies


def test_home_control_retains_support_effects_and_preserves_source(tmp_path, monkeypatch, capfd):
    source, dependencies = authored_runtime(tmp_path, monkeypatch)
    before = worker.inventory(source)
    result = worker.run_application_home(source, dependencies)
    assert result["status"] == "passed"
    assert worker.inventory(source) == before
    assert result["captured_fixture_unchanged"] is True
    assert result["captured_source_changes"] == []
    external = result["cases"]["external"]
    control = result["cases"]["source_tree_control"]
    assert external["home"]["outside_source"] is True
    assert control["home"]["outside_source"] is False
    assert external["home"]["generated_support_entries"] == [".tmp"]
    assert external["source_support_entries"] == []
    assert control["source_support_entries"] == [".tmp"]
    assert str(tmp_path) not in json.dumps(result)
    assert "APPLICATION HOME source_tree_control" in capfd.readouterr().err


def test_missing_support_effect_cannot_pass_on_empty_detector(tmp_path, monkeypatch):
    source, dependencies = authored_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("OMIT_SUPPORT", "1")
    result = worker.run_application_home(source, dependencies)
    assert result["status"] == "failed"
    assert result["cases"]["source_tree_control"]["source_support_entries"] == []


def test_changed_fixture_fails_even_when_application_succeeds(tmp_path, monkeypatch):
    source, dependencies = authored_runtime(tmp_path, monkeypatch)
    before = worker.inventory(source)
    monkeypatch.setenv("DAMAGE_FIXTURE", "1")
    result = worker.run_application_home(source, dependencies)
    assert result["status"] == "failed"
    assert result["cases"]["external"]["home"]["fixture_changes"] == ["sessions/plain.jsonl"]
    assert worker.inventory(source) == before


def test_home_log_header_truncation_marks_evidence_incomplete(tmp_path, monkeypatch, capfd):
    source, dependencies = authored_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(worker, "RAW_LIMIT", 1024)
    # Process output fits in the per-case budget. The retained record, including
    # its label, exceeds it: losing the stderr tail must invalidate completeness.
    monkeypatch.setenv("OUTPUT_BYTES", "511")
    result = worker.run_application_home(source, dependencies)
    assert result["status"] == "failed"
    assert all(not row["output_complete"] for row in result["cases"].values())
    retained = capfd.readouterr().err
    assert "TAIL" not in retained
    assert len(retained.encode()) == worker.RAW_LIMIT
