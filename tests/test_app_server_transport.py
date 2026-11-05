"""Exercise controller transport with authored subprocesses, never a model or VM."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from skilleval import app_server, guest_runner

SERVER = r"""
import json, pathlib, subprocess, sys
root = pathlib.Path.cwd()
mode = sys.argv[1]
received = (root / 'server-input.jsonl').open('wb')
emitted = (root / 'server-output.jsonl').open('wb')
def read():
    line = sys.stdin.buffer.readline()
    assert line, 'controller closed input before the request'
    received.write(line); received.flush()
    return json.loads(line)
def raw(data):
    emitted.write(data); emitted.flush()
    sys.stdout.buffer.write(data); sys.stdout.buffer.flush()
def send(record):
    raw((json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n').encode())
request = read()
assert request['method'] == 'initialize'
send({'id': request['id'], 'result': {'userAgent':'codex/0.164.0',
    'codexHome':'/tmp/codex', 'platformFamily':'unix', 'platformOs':'linux'}})
assert read() == {'method':'initialized'}
request = read()
assert request['method'] == 'thread/start'
assert request['params']['approvalPolicy'] == 'never'
assert request['params']['ephemeral'] is True
assert request['params']['cwd'] == str(root)
send({'id':request['id'], 'result':{'thread':{'id':'thread-1', 'ephemeral':True},
    'approvalPolicy':'never', 'sandbox':{'type':'readOnly','networkAccess':False}}})
request = read()
assert request['method'] == 'turn/start'
assert request['params']['input'] == [{'type':'text', 'text':'Ordinary task brief. café'}]
assert request['params']['approvalPolicy'] == 'never'
assert request['params']['sandboxPolicy'] == {'type':'externalSandbox','networkAccess':'restricted'}
turn = {'id':'turn-1', 'status':'inProgress', 'items':[], 'error':None}
reply = {'id':request['id'], 'result':{'turn':turn}}
if mode != 'reply-last':
    send(reply)
send({'method':'thread/settings/updated','params':{'threadId':'thread-1',
    'threadSettings':{'approvalPolicy':'never',
        'sandboxPolicy':{'type':'externalSandbox','networkAccess':'restricted'}}}})
send({'method':'turn/started','params':{'threadId':'thread-1','turn':turn}})
print('server diagnostic', file=sys.stderr, flush=True)
if mode == 'malformed':
    raw(b'not-json\n')
elif mode == 'oversized':
    raw(b'x' * (4 * 1024 * 1024 + 1) + b'\n')
elif mode == 'approval':
    send({'id':99, 'method':'item/commandExecution/requestApproval',
        'params':{'threadId':'thread-1','turnId':'turn-1'}})
elif mode == 'timeout':
    subprocess.Popen([sys.executable, '-c',
        "import signal,time,pathlib,sys; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('descendant-ready',file=sys.stderr,flush=True); "
        "time.sleep(1.5); pathlib.Path('escaped').write_text('bad')"])
    import time
    time.sleep(30)
else:
    message = {'id':'answer-1','type':'agentMessage','phase':'final_answer','text':'Finished. café'}
    send({'method':'item/completed','params':{'threadId':'thread-1',
        'turnId':'turn-1','item':message}})
    send({'method':'turn/completed','params':{'threadId':'thread-1',
        'turn':{'id':'turn-1','status':'completed','items':[message],'error':None}}})
    if mode == 'reply-last':
        send(reply)
sys.stdin.buffer.read()
"""


def execute(tmp_path: Path, mode: str, *, timeout: int = 5) -> dict[str, Any]:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Ordinary task brief. café")
    server = tmp_path / "fake-server.py"
    server.write_text(SERVER)
    return guest_runner._execute_native(
        [sys.executable, str(server), mode],
        {"PATH": os.defpath},
        prompt,
        tmp_path,
        tmp_path,
        timeout,
        protocol=app_server.Session(prompt.read_text(), str(tmp_path)),
    )


@pytest.mark.parametrize("mode", ["success", "reply-last"])
def test_rpc_roundtrip_retains_raw_streams_and_derives_final(tmp_path: Path, mode: str) -> None:
    result = execute(tmp_path, mode)
    assert result["returncode"] == 0, result
    assert result["timed_out"] is False
    assert result["output_limit_reached"] is False
    assert result["protocol"]["complete"] is True
    assert result["protocol"]["permissions_confirmed"] is True
    assert result["protocol"]["error"] is None
    assert (tmp_path / "app-server-rpc.jsonl").read_bytes() == (
        tmp_path / "server-output.jsonl"
    ).read_bytes()
    assert (tmp_path / "app-server-input.jsonl").read_bytes() == (
        tmp_path / "server-input.jsonl"
    ).read_bytes()
    assert (tmp_path / "final.txt").read_text() == "Finished. café"
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [row["type"] for row in events] == [
        "thread.started",
        "turn.started",
        "item.completed",
        "turn.completed",
    ]
    assert events[2]["item"]["type"] == "agent_message"
    assert events[2]["item"]["phase"] == "final_answer"
    assert events[2]["item"]["text"] == "Finished. café"
    assert (tmp_path / "stderr.log").read_text() == "server diagnostic\n"


@pytest.mark.parametrize(
    "mode,error",
    [
        ("malformed", "Expecting value"),
        ("oversized", "limit exceeded"),
        ("approval", "unexpected app-server request"),
    ],
)
def test_invalid_rpc_stops_child_and_preserves_failure_evidence(
    tmp_path: Path, mode: str, error: str
) -> None:
    result = execute(tmp_path, mode)
    assert result["protocol"]["complete"] is False
    assert result["protocol"]["permissions_confirmed"] is True
    assert error in result["protocol"]["error"]
    assert result["timed_out"] is False
    assert result["returncode"] != 0
    assert result["agent_elapsed_seconds"] < 5
    raw = (tmp_path / "app-server-rpc.jsonl").read_bytes()
    assert raw.startswith(b'{"id":1,"result":')
    if mode == "oversized":
        assert len(raw) > 4 * 1024 * 1024
    else:
        assert raw == (tmp_path / "server-output.jsonl").read_bytes()
    assert (tmp_path / "final.txt").read_bytes() == b""
    assert (tmp_path / "app-server-input.jsonl").stat().st_size > 0


def test_rpc_timeout_retains_partial_stream_and_kills_descendants(tmp_path: Path) -> None:
    result = execute(tmp_path, "timeout", timeout=1)
    assert result["timed_out"] is True
    assert result["protocol"]["complete"] is False
    assert result["returncode"] != 0
    assert 1 <= result["agent_elapsed_seconds"] < 8
    assert b'"method":"turn/started"' in (tmp_path / "app-server-rpc.jsonl").read_bytes()
    assert "descendant-ready" in (tmp_path / "stderr.log").read_text()
    assert (tmp_path / "final.txt").read_bytes() == b""
    time.sleep(0.7)
    assert not (tmp_path / "escaped").exists()
