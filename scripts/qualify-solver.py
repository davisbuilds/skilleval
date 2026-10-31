#!/usr/bin/env python3
"""Opt-in OFF/ON native solver boundary controls, without credentials or inference.

Uses the production runner and real Codex tools with synthetic Responses. The
explicit control commands are not representative task presentation or model
behavior. Existing evidence is never rewritten; each invocation needs fresh output.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import shlex
import subprocess
import sys
import uuid
from itertools import pairwise
from pathlib import Path
from typing import Any

from skilleval import context_guard, evidence, runner
from skilleval.errors import SkillEvalError
from skilleval.gateway import GatewayLimits
from skilleval.identity import file_digest
from skilleval.treatments import capture_skill

ARMS = ("absent", "available")
SKILL_NAME = "workspace-helper"
SKILL_PATH = runner.GUEST_ROOT + "/codex-home/skills/" + SKILL_NAME + "/SKILL.md"
RESOURCE_PATH = str(Path(SKILL_PATH).parent / "references/example.txt")
DENIED = (
    "/var/tmp/native/runner.py",
    "/var/tmp/native/app_server.py",
    "/var/tmp/native/context_guard.py",
    "/var/tmp/native/settings.json",
    "/var/tmp/native/prompt.txt",
    "/var/tmp/native/solver_boundary.py",
    "/var/tmp/native/boundary.json",
    "/var/tmp/skilleval-tooling.tar",
    "/opt/skilleval/openbench_gateway.py",
)
PROC_SURFACES = (
    "/proc/smolvm-fsnotify",
    "/proc/cmdline",
    "/proc/mounts",
    "/proc/self/mountinfo",
    "/proc/self/net/unix",
)
READ_METHODS = ("direct", "symlink", "subprocess")
PROBE_FILE = "task/solver-controls.json"
HEARTBEAT_FILE = "task/solver-heartbeat.json"
PROBE_MARKER = "SOLVER_BOUNDARY_TOOL_RESULT="
PROMPT = "Change the value to 'after', run the unit tests, and report the result."
ROOT_PING_MARKER = "ROOT_VSOCK_PING="

# Pinned smolvm 1.32.3 protocol: only the unit Ping request is permitted here.
# CIDs 1 (local) and 3 (guest), port 6000, were independently reached in the
# retained pre-filter control. Repeat both in the same VM before native launch.
ROOT_PING = r"""
import json,os,socket,struct,time
payload=b'{"method":"ping"}'
frame=struct.pack('>I',len(payload))+payload
report={'uid':os.getuid(),'request_frame_hex':frame.hex(),'targets':[]}
for cid in (1,3):
    row={'cid':cid,'port':6000,'pong':False}
    connection=None
    try:
        connection=socket.socket(socket.AF_VSOCK,socket.SOCK_STREAM)
        connection.settimeout(1)
        connection.connect((cid,6000))
        connection.sendall(frame)
        deadline=time.monotonic()+2
        def exact(size):
            received=b''
            while len(received)<size:
                remaining=deadline-time.monotonic()
                if remaining<=0: raise TimeoutError('bounded Ping deadline')
                connection.settimeout(remaining)
                part=connection.recv(size-len(received))
                if not part: raise EOFError('incomplete Ping response')
                received+=part
            return received
        header=exact(4)
        size=struct.unpack('>I',header)[0]
        if size>65536: raise ValueError('oversized Ping response')
        response=exact(size)
        row['response_frame_hex']=(header+response).hex()
        row['response']=json.loads(response)
        row['pong']=row['response'].get('status')=='pong' and row['response'].get('version')==1
    except (OSError,EOFError,ValueError) as error:
        row.update(error_type=type(error).__name__,error=str(error))
        if isinstance(error,OSError): row['errno']=error.errno
    finally:
        if connection is not None: connection.close()
    report['targets'].append(row)
print('ROOT_VSOCK_PING='+json.dumps(report,sort_keys=True),flush=True)
"""

# This program is supplied as a synthetic tool call, never run by ordinary tests
# or imported as task code on the host. Its raw observations survive in both the
# native command result and the collected task artifact.
PROBE = r'''
import ctypes,errno,hashlib,http.client,json,os,pathlib,pty,select
import signal,socket,stat,subprocess,sys,time
spec=json.loads(sys.argv[1])
work=pathlib.Path('/workspace/task')
def read(path):
    try:
        with open(path,'rb') as stream: data=stream.read(4096)
        return {'opened':True,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
                'text':data[:256].decode('utf-8',errors='replace')}
    except OSError as error:
        return {'opened':False,'errno':error.errno}
reader="""import errno,hashlib,json,sys
try:
 with open(sys.argv[1],'rb') as f: data=f.read(4096)
 print(json.dumps({'opened':True,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
                   'text':data[:256].decode('utf-8',errors='replace')}))
except OSError as e: print(json.dumps({'opened':False,'errno':e.errno}))
"""
def indirect(path,method):
    if method=='direct': return read(path)
    if method=='symlink':
        link=pathlib.Path('/tmp/solver-link')
        link.unlink(missing_ok=True)
        link.symlink_to(path)
        result=read(link)
        link.unlink()
        return result
    child=subprocess.run([sys.executable,'-c',reader,path],capture_output=True,
                         text=True,timeout=5,check=True)
    return json.loads(child.stdout)
positive=str(work/'public-note.txt')
observed={'schema_version':1}
observed['positive']={method:indirect(positive,method) for method in spec['methods']}
observed['denied']={path:{method:indirect(path,method) for method in spec['methods']}
                    for path in spec['denied']}
observed['instructions']=read(work/'AGENTS.md')
observed['source_before']=read(work/'value.py')
(work/'value.py').write_text('VALUE = "after"\n')
def command(argv):
    result=subprocess.run(argv,cwd=work,capture_output=True,text=True,timeout=10)
    return {'argv':argv,'returncode':result.returncode,
            'stdout':result.stdout[:4096],'stderr':result.stderr[:4096]}
observed['tests']=command([sys.executable,'-m','unittest','-q'])
observed['history']=command(['git','show','HEAD^:history.txt'])
observed['diff']=command(['git','diff','--','value.py'])
observed['source_after']=read(work/'value.py')
master=slave=None
try:
    master,slave=pty.openpty()
    os.write(slave,b'pty-positive')
    ready=bool(select.select([master],[],[],1)[0])
    observed['pty']={'opened':True,'slave':os.ttyname(slave),
                     'received':os.read(master,64).decode() if ready else None}
except OSError as error:
    observed['pty']={'opened':False,'errno':error.errno}
finally:
    for descriptor in (master,slave):
        if descriptor is not None: os.close(descriptor)
try:
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as listener:
        listener.settimeout(1)
        listener.bind(('127.0.0.1',0))
        listener.listen(1)
        with socket.create_connection(listener.getsockname(),timeout=1) as client:
            with listener.accept()[0] as server:
                server.settimeout(1)
                client.sendall(b'local-client')
                received=server.recv(64).decode()
                server.sendall(b'local-server')
                observed['local_tcp']={'address':list(listener.getsockname()),
                    'server_received':received,'client_received':client.recv(64).decode()}
except OSError as error: observed['local_tcp']={'errno':error.errno}
observed['skill_resource']=read(spec['resource'])
observed['identity']={'uid':os.getuid(),'gid':os.getgid(),'groups':os.getgroups()}
observed['hostname']=os.uname().nodename
status=pathlib.Path('/proc/self/status').read_text()
observed['status']={key:value.strip() for key,value in
                    (line.split(':',1) for line in status.splitlines() if ':' in line)
                    if key.startswith('Cap') or key in
                    ('NoNewPrivs','NSpid','Seccomp','Seccomp_filters')}
libc=ctypes.CDLL(None,use_errno=True)
observed['vsock']={}
for name in ('socket','socketpair'):
    descriptors=[]
    try:
        descriptors=([socket.socket(socket.AF_VSOCK,socket.SOCK_STREAM)] if name=='socket'
                     else list(socket.socketpair(socket.AF_VSOCK,socket.SOCK_STREAM)))
        observed['vsock'][name]={'created':True}
    except OSError as error: observed['vsock'][name]={'created':False,'errno':error.errno}
    finally:
        for descriptor in descriptors: descriptor.close()
try:
    left,right=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
    with left,right:
        left.settimeout(1); right.settimeout(1)
        left.sendall(b'unix-positive')
        observed['unix_socketpair']={'received':right.recv(64).decode()}
except OSError as error: observed['unix_socketpair']={'errno':error.errno}
parameters=ctypes.create_string_buffer(256)
observed['io_uring']={}
for number,arguments in (
    (425,(ctypes.c_uint(1),ctypes.byref(parameters))),
    (426,(ctypes.c_int(-1),ctypes.c_uint(0),ctypes.c_uint(0),ctypes.c_uint(0),
          ctypes.c_void_p(),ctypes.c_size_t(0))),
    (427,(ctypes.c_int(-1),ctypes.c_uint(0),ctypes.c_void_p(),ctypes.c_uint(0))),
):
    ctypes.set_errno(0)
    result=libc.syscall(ctypes.c_long(number),*arguments)
    observed['io_uring'][str(number)]={'returncode':result,'errno':ctypes.get_errno()}
    if number==425 and result>=0: os.close(result)
attempts={}
for name,flag in [('user',0x10000000),('mount',0x00020000),('pid',0x20000000)]:
    program=('import ctypes,json; c=ctypes.CDLL(None,use_errno=True); '
             'r=c.unshare('+str(flag)+'); print(json.dumps({"returncode":r,'
             '"errno":ctypes.get_errno()}))')
    result=command([sys.executable,'-c',program])
    attempts[name]=json.loads(result['stdout']) if result['returncode']==0 else result
observed['namespace_attempts']=attempts
program=('import json,os; result={};\ntry: os.setuid(0); result["regained"]=True\n'
         'except OSError as e: result={"regained":False,"errno":e.errno}\n'
         'result["uid"]=os.getuid(); print(json.dumps(result))')
observed['uid_regain']=json.loads(command([sys.executable,'-c',program])['stdout'])
sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
sock.settimeout(1)
try:
    sock.connect('/run/inference.sock')
    observed['inference_socket']={'connected':True}
except OSError as error:
    observed['inference_socket']={'connected':False,'errno':error.errno}
finally: sock.close()
observed['inference_socket']['visible']=os.path.lexists('/run/inference.sock')
connection=http.client.HTTPConnection('127.0.0.1',8765,timeout=1)
try:
    connection.request('GET','/responses')
    response=connection.getresponse()
    observed['gateway']={'status':response.status,'server':response.getheader('Server')}
    response.read(4096)
except OSError as error: observed['gateway']={'errno':error.errno}
finally: connection.close()
proc_reader="""import json,sys
try:
 with open(sys.argv[1],'rb') as stream: data=stream.read(8193)
 print(json.dumps({'opened':True,'bytes':len(data),'truncated':len(data)>8192,
                   'text':data[:8192].decode('utf-8',errors='replace')}))
except OSError as error: print(json.dumps({'opened':False,'errno':error.errno}))
"""
observed['proc_surfaces']={}
for name in spec['proc_surfaces']:
    try:
        result=subprocess.run([sys.executable,'-c',proc_reader,name],capture_output=True,
                              text=True,timeout=1,check=True)
        observed['proc_surfaces'][name]=json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        observed['proc_surfaces'][name]={'timed_out':True}
observed['proc_self_positive']=read('/proc/self/root'+positive)
processes=[]
for entry in sorted(pathlib.Path('/proc').iterdir()):
    if not entry.name.isdecimal(): continue
    row={'pid':int(entry.name),'cmdline':read(entry/'cmdline')}
    try:
        row['root']=os.readlink(entry/'root')
    except OSError as error: row['root_errno']=error.errno
    row['root_denied']=read(str(entry/'root')+spec['denied'][0])
    row['fds']=[]
    try:
        descriptors=sorted((entry/'fd').iterdir())
    except OSError as error:
        row['fd_errno']=error.errno
        descriptors=[]
    for descriptor in descriptors:
        try:
            target=os.readlink(descriptor)
            info=descriptor.stat()
            fd={'fd':descriptor.name,'target':target,'directory':stat.S_ISDIR(info.st_mode)}
            if fd['directory']:
                fd['escape']=read(str(descriptor)+spec['denied'][0])
            row['fds'].append(fd)
        except OSError: pass
    processes.append(row)
observed['processes']=processes
observed['mountinfo']=pathlib.Path('/proc/self/mountinfo').read_text()[:8192]
heartbeat=work/'solver-heartbeat.json'
child_program=r"""import json,os,pathlib,signal,sys,time
signal.signal(signal.SIGTERM,signal.SIG_IGN)
target=pathlib.Path(sys.argv[1]); temporary=target.with_suffix('.tmp'); count=0
while True:
 count+=1
 temporary.write_text(json.dumps({'pid':os.getpid(),'count':count}))
 os.replace(temporary,target)
 time.sleep(0.1)
"""
child=subprocess.Popen([sys.executable,'-c',child_program,str(heartbeat)],
                       stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True)
deadline=time.monotonic()+3
samples=[]
while time.monotonic()<deadline:
    try:
        sample=json.loads(heartbeat.read_text())
        if not samples or sample['count']>samples[-1]['count']: samples.append(sample)
        if len(samples)>=3: break
    except (OSError,ValueError): pass
    time.sleep(0.05)
observed['detached']={'pid':child.pid,'samples':samples,'alive':child.poll() is None}
encoded=json.dumps(observed,sort_keys=True)
(work/'solver-controls.json').write_text(encoded+'\n')
print('SOLVER_BOUNDARY_TOOL_RESULT='+encoded,flush=True)
'''


def _responder() -> Any:
    spec = importlib.util.spec_from_file_location(
        "native_solver_fixture", Path(__file__).with_name("qualify-native.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def worker(spec_path: Path) -> int:
    spec = json.loads(spec_path.read_text())
    responder = _responder()
    commands = [
        "python3 -c "
        + shlex.quote(PROBE)
        + " "
        + shlex.quote(
            json.dumps(
                {
                    "denied": DENIED,
                    "methods": READ_METHODS,
                    "resource": RESOURCE_PATH,
                    "proc_surfaces": PROC_SURFACES,
                }
            )
        ),
        "cat " + (SKILL_PATH if spec["arm"] == "available" else "/workspace/task/AGENTS.md"),
    ]
    calls = 0

    def transport(raw: bytes, _headers: dict[str, str], _timeout: float) -> Any:
        nonlocal calls
        calls += 1
        if calls <= len(commands):
            item = responder.tool_item(json.loads(raw), commands[calls - 1])
            # Each tool result has a unique call identity, including the code-mode route.
            item["id"] += "_" + str(calls)
            item["call_id"] += "_" + str(calls)
            if item["type"] == "custom_tool_call":
                item["input"] = item["input"].replace(
                    "{cmd: ", "{max_output_tokens: 20000, cmd: ", 1
                )
            elif item["name"] == "exec_command":
                arguments = json.loads(item["arguments"])
                arguments["max_output_tokens"] = 20000
                item["arguments"] = json.dumps(arguments)
        elif calls == len(commands) + 1:
            item = {
                "type": "message",
                "id": "msg_solver_complete",
                "role": "assistant",
                "phase": "final_answer",
                "status": "completed",
                "content": [{"type": "output_text", "text": "The change and tests are complete."}],
            }
        else:
            raise ValueError("unexpected synthetic solver request")
        return responder.SyntheticStream(responder.response_stream(item, f"resp_solver_{calls}"))

    selected = spec["arm"] == "available"
    output = Path(spec["output"])
    lifecycle = runner.Lifecycle

    class DiagnosticLifecycle(lifecycle):
        def guest(self, label: str, argv: list[str], timeout: int = 30) -> dict[str, Any]:
            result = super().guest(label, argv, timeout)
            if label == "prepare_task" and result.get("returncode") == 0:
                positive = super().guest("root_vsock_ping", ["python3", "-I", "-c", ROOT_PING], 10)
                (output / "root-vsock.json").write_text(json.dumps(positive, indent=2) + "\n")
            return result

    runner.Lifecycle = DiagnosticLifecycle
    try:
        report = runner.run_native(
            Path(spec["input"]),
            spec["input_digest"],
            spec["prompt"],
            output,
            runtime=Path(spec["runtime"]),
            image=Path(spec["image"]),
            image_sha256=spec["image_sha256"],
            catalog=Path(spec["catalog"]),
            auth_file=None,
            mode="repair",
            synthetic_transport=transport,
            native_timeout_seconds=90,
            limits=GatewayLimits(max_requests=4, timeout_seconds=20, lifetime_seconds=180),
            skill_bundle=Path(spec["skill"]) if selected else None,
            skill_digest=spec["skill_digest"] if selected else None,
        )
    finally:
        runner.Lifecycle = lifecycle
    return 0 if report.get("cleanup_complete") and report.get("runtime_home_removed") else 1


def _successful_outputs(events: list[dict[str, Any]]) -> list[str]:
    return [
        item["aggregated_output"]
        for event in events
        if event.get("type") == "item.completed"
        and isinstance(item := event.get("item"), dict)
        and item.get("type") == "command_execution"
        and item.get("status") == "completed"
        and item.get("exit_code") == 0
        and isinstance(item.get("aggregated_output"), str)
    ]


def _texts(body: dict[str, Any]) -> list[str]:
    return [
        part["text"]
        for item in body.get("input", [])
        for part in item.get("content", [])
        if item.get("type") == "message"
        and isinstance(part, dict)
        and isinstance(part.get("text"), str)
    ]


def _prompt(path: Path) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None


def assess_arm(
    directory: Path, selected: bool, markers: dict[str, str], skill_body: str
) -> tuple[dict[str, bool], dict[str, Any]]:
    """Read retained raw tool events and validated archive bytes, never final prose."""
    audit = json.loads((directory / "native-audit.json").read_text())
    archive = directory / "artifacts.tar"
    manifest = runner.validate_artifacts(
        archive, runner.MAX_ARTIFACT_BYTES, runner.MAX_ARTIFACT_FILES
    )
    requests = [json.loads(p.read_bytes()) for p in sorted(directory.glob("request-*.json"))]
    raw_probe = runner._member(archive, PROBE_FILE, 1024 * 1024)
    probe = json.loads(raw_probe)
    heartbeat = json.loads(runner._member(archive, HEARTBEAT_FILE, 4096))
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    outputs = _successful_outputs(events)
    tool_probes = []
    for output in outputs:
        for line in output.splitlines():
            if line.startswith(PROBE_MARKER):
                tool_probes.append(json.loads(line.removeprefix(PROBE_MARKER)))
    controls: dict[str, bool] = {
        "cleanup": all(
            audit.get(key) is True
            for key in (
                "cleanup_complete",
                "runtime_home_removed",
                "observed_absent",
                "broker_clean",
            )
        ),
        "synthetic_native_completed": audit.get("kind") == "skilleval-native-context-control"
        and audit.get("status") == "completed"
        and len(requests) == 3,
        "tool_artifact_agreement": tool_probes == [probe],
        "positive_reads": all(
            probe.get("positive", {}).get(method, {}).get("opened") is True
            and probe["positive"][method].get("text") == markers["public"]
            for method in READ_METHODS
        ),
        "task_instructions": probe.get("instructions", {}).get("opened") is True
        and markers["task"] in probe["instructions"].get("text", ""),
        "task_source_read_edit": probe.get("source_before", {}).get("text") == 'VALUE = "before"\n'
        and probe.get("source_after", {}).get("text") == 'VALUE = "after"\n'
        and runner._member(archive, "task/value.py", 4096) == b'VALUE = "after"\n',
        "ordinary_tests": probe.get("tests", {}).get("returncode") == 0
        and "Ran 1 test" in probe["tests"].get("stderr", ""),
        "required_git_history": probe.get("history", {}).get("returncode") == 0
        and probe["history"].get("stdout") == markers["history"],
        "meaningful_git_diff": probe.get("diff", {}).get("returncode") == 0
        and '-VALUE = "before"' in probe["diff"].get("stdout", "")
        and '+VALUE = "after"' in probe["diff"].get("stdout", ""),
        "pty_available": probe.get("pty", {}).get("opened") is True
        and probe["pty"].get("received") == "pty-positive"
        and probe["pty"].get("slave", "").startswith("/dev/pts/"),
        "localhost_tcp_available": probe.get("local_tcp", {}).get("server_received")
        == "local-client"
        and probe["local_tcp"].get("client_received") == "local-server"
        and probe["local_tcp"].get("address", [None])[0] == "127.0.0.1",
    }
    denied = probe.get("denied", {})
    controls["denied_file_matrix"] = set(denied) == set(DENIED) and all(
        set(denied[path]) == set(READ_METHODS)
        and all(
            denied[path][method].get("opened") is False
            and denied[path][method].get("errno") in {1, 2, 13, 20}
            for method in READ_METHODS
        )
        for path in DENIED
    )
    controls["inference_socket_hidden"] = (
        probe.get("inference_socket", {}).get("connected") is False
        and probe["inference_socket"].get("visible") is False
        and probe["inference_socket"].get("errno") in {1, 2, 13, 20}
    )
    controls["neutral_gateway_header"] = (
        probe.get("gateway", {}).get("status") == 405
        and isinstance(probe["gateway"].get("server"), str)
        and probe["gateway"]["server"].startswith("LocalGateway")
        and "OpenBench" not in probe["gateway"]["server"]
    )
    proc_surfaces = probe.get("proc_surfaces", {})
    controls["bounded_proc_positive"] = all(
        proc_surfaces.get(path, {}).get("opened") is True
        and proc_surfaces[path].get("bytes", 0) > 0
        and proc_surfaces[path].get("truncated") is False
        and not any(denied in proc_surfaces[path].get("text", "") for denied in DENIED)
        for path in PROC_SURFACES
        if path != "/proc/smolvm-fsnotify"
    )
    custom_proc = proc_surfaces.get("/proc/smolvm-fsnotify", {})
    controls["custom_proc_hidden_or_empty"] = (
        custom_proc.get("opened") is False and custom_proc.get("errno") in {1, 2, 13, 20}
    ) or (
        custom_proc.get("opened") is True
        and custom_proc.get("bytes") == 0
        and custom_proc.get("text") == ""
        and custom_proc.get("truncated") is False
    )
    metadata = {"hostname": probe.get("hostname", "")} | {
        name: row.get("text", "") for name, row in proc_surfaces.items()
    }
    metadata_findings = {
        name: re.findall(r"(?:skilleval|openbench)[^\s,\x00]*", text, re.I)[:32]
        for name, text in metadata.items()
        if isinstance(text, str) and re.search(r"skilleval|openbench", text, re.I)
    }
    controls["neutral_system_metadata"] = (
        isinstance(probe.get("hostname"), str) and bool(probe["hostname"]) and not metadata_findings
    )
    controls["unprivileged_identity"] = probe.get("identity") == {
        "uid": 61000,
        "gid": 61000,
        "groups": [],
    }
    status = probe.get("status", {})
    controls["capabilities_removed"] = all(
        isinstance(status.get(key), str) and re.fullmatch("0+", status[key]) is not None
        for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
    )
    controls["no_new_privileges"] = status.get("NoNewPrivs") == "1"
    seccomp_filters = status.get("Seccomp_filters")
    controls["seccomp_filter_active"] = (
        status.get("Seccomp") == "2"
        and isinstance(seccomp_filters, str)
        and seccomp_filters.isdecimal()
        and int(seccomp_filters) >= 1
    )
    controls["vsock_socket_creation_denied"] = set(probe.get("vsock", {})) == {
        "socket",
        "socketpair",
    } and all(
        row.get("created") is False and row.get("errno") == 1 for row in probe["vsock"].values()
    )
    controls["io_uring_denied"] = set(probe.get("io_uring", {})) == {"425", "426", "427"} and all(
        row.get("returncode") == -1 and row.get("errno") == 1 for row in probe["io_uring"].values()
    )
    controls["unix_socketpair_available"] = (
        probe.get("unix_socketpair", {}).get("received") == "unix-positive"
    )
    root_raw = json.loads((directory / "root-vsock.json").read_text())
    root_ping = [
        json.loads(line.removeprefix(ROOT_PING_MARKER))
        for line in root_raw.get("stdout", "").splitlines()
        if line.startswith(ROOT_PING_MARKER)
    ]
    controls["root_vsock_positive"] = (
        root_raw.get("returncode") == 0
        and root_raw.get("timed_out") is False
        and root_raw.get("output_truncated") is False
        and not root_raw.get("pipe_drain_incomplete")
        and len(root_ping) == 1
        and root_ping[0].get("uid") == 0
        and root_ping[0].get("request_frame_hex") == "000000117b226d6574686f64223a2270696e67227d"
        and [
            (row.get("cid"), row.get("port"), row.get("pong"))
            for row in root_ping[0].get("targets", [])
        ]
        == [(1, 6000, True), (3, 6000, True)]
    )
    controls["uid_regain_denied"] = probe.get("uid_regain") == {
        "regained": False,
        "errno": 1,
        "uid": 61000,
    }
    attempts = probe.get("namespace_attempts", {})
    controls["nested_namespaces_denied"] = set(attempts) == {"user", "mount", "pid"} and all(
        row.get("returncode") == -1 and row.get("errno") in {1, 13} for row in attempts.values()
    )
    processes = probe.get("processes", [])
    leader = [row for row in processes if row.get("pid") == 1]
    controls["fresh_proc"] = (
        probe.get("proc_self_positive", {}).get("text") == markers["public"]
        and len(leader) == 1
        and leader[0].get("cmdline", {}).get("opened") is True
        and "codex" in leader[0]["cmdline"].get("text", "")
        and all(
            row.get("root_denied", {}).get("opened") is False
            and not any(path in row.get("cmdline", {}).get("text", "") for path in DENIED)
            for row in processes
        )
    )
    descriptors = [fd for row in processes for fd in row.get("fds", [])]
    controls["no_controller_descriptors"] = bool(descriptors) and all(
        not any(path in descriptor.get("target", "") for path in DENIED)
        and "/var/tmp/native/evidence/" not in descriptor.get("target", "")
        and not (
            descriptor.get("directory") and descriptor.get("escape", {}).get("opened") is not False
        )
        for descriptor in descriptors
    )
    boundary = audit.get("process", {}).get("boundary", {})
    controls["trusted_boundary_receipt"] = all(
        boundary.get(key) == value
        for key, value in {
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
        }.items()
    ) and all(
        isinstance(boundary.get(key), (int, float))
        and not isinstance(boundary[key], bool)
        and math.isfinite(boundary[key])
        and boundary[key] >= 0
        for key in ("native_elapsed_seconds", "launcher_elapsed_seconds")
    )
    samples = probe.get("detached", {}).get("samples", [])
    controls["detached_child_positive_and_reaped"] = (
        probe.get("detached", {}).get("alive") is True
        and len(samples) >= 3
        and all(sample.get("pid") == probe["detached"].get("pid") for sample in samples)
        and all(a["count"] < b["count"] for a, b in pairwise(samples))
        and heartbeat.get("pid") == probe["detached"]["pid"]
        and heartbeat.get("count", -1) >= samples[-1]["count"]
        and boundary.get("namespace_reaped") is True
    )
    first = requests[0] if requests else {}
    texts = _texts(first)
    controls["task_instruction_in_actual_request"] = any(
        text.startswith("# AGENTS.md instructions for ") and markers["task"] in text
        for text in texts
    )
    controls["network_message_restricted"] = any(
        re.search(r"network access is (?:restricted|disabled)", text, re.I) for text in texts
    ) and not any(re.search(r"network access is enabled", text, re.I) for text in texts)
    context = context_guard.request_context(json.dumps(first).encode()) if requests else {}
    catalog = [item for row in context.get("skill_catalogs", []) for item in row.get("skills", [])]
    controls["selected_skill_catalog"] = [
        item for item in catalog if item["name"] == SKILL_NAME
    ] == ([{"name": SKILL_NAME, "path": SKILL_PATH}] if selected else [])
    controls["complete_skill_read"] = (
        any(skill_body in output for output in outputs)
        and audit.get("skill_consultation", {}).get("consultation") == "observed"
        and any(
            row.get("skill_body_observed") is True for row in audit.get("request_observations", [])
        )
        if selected
        else all(skill_body not in output for output in outputs)
    )
    resource = probe.get("skill_resource", {})
    controls["selected_resource"] = (
        resource.get("opened") is True and resource.get("text") == markers["resource"]
        if selected
        else resource.get("opened") is False and resource.get("errno") in {1, 2, 13, 20}
    )
    details = {
        "audit_sha256": file_digest(directory / "native-audit.json"),
        "artifacts_sha256": manifest["sha256"],
        "request_sha256": [file_digest(path) for path in sorted(directory.glob("request-*.json"))],
        "probe": probe,
        "collected_heartbeat": heartbeat,
        "boundary": boundary,
        "permission_messages": [
            match.group(0)
            for text in texts
            for match in re.finditer(r"Network access is [^.\n]+[.]?", text, re.I)
        ],
        "metadata_findings": metadata_findings,
        "root_vsock": root_ping,
        "root_vsock_sha256": file_digest(directory / "root-vsock.json"),
    }
    return controls, {"audit": audit, "context": context, "details": details}


def _conclusion(controls: dict[str, bool]) -> dict[str, Any]:
    permissions_consistent = all(
        controls.get(arm + ":network_message_restricted") is True for arm in ARMS
    )
    presentation_consistent = all(
        controls.get(arm + ":neutral_system_metadata") is True for arm in ARMS
    )
    boundary_controls = [
        value
        for key, value in controls.items()
        if not key.endswith((":network_message_restricted", ":neutral_system_metadata"))
    ]
    boundary_passed = bool(boundary_controls) and all(boundary_controls)
    return {
        "boundary_passed": boundary_passed,
        "permissions_consistent": permissions_consistent,
        "presentation_consistent": presentation_consistent,
        "status": "passed"
        if boundary_passed and permissions_consistent and presentation_consistent
        else ("partial" if boundary_passed else "failed"),
        "limitations": (
            []
            if permissions_consistent
            else [
                "Native permission text does not establish restricted networking. The pinned Codex "
                "CLI danger-full-access profile announces enabled networking despite the enclosing "
                "network-off VM. Filesystem/process boundary controls are reported separately; "
                "this does not qualify capability-description consistency."
            ]
        )
        + (
            []
            if presentation_consistent
            else [
                "Solver-visible system metadata contains an evaluation identity or is missing. "
                "Per-arm metadata_findings retain detected strings. Filesystem/process access "
                "controls do not establish neutral task presentation."
            ]
        ),
    }


def assess(root: Path, markers: dict[str, str], skill_body: str) -> dict[str, Any]:
    controls: dict[str, bool] = {}
    runs: dict[str, Any] = {}
    loaded: dict[str, Any] = {}
    for arm in ARMS:
        try:
            arm_controls, loaded[arm] = assess_arm(
                root / arm, arm == "available", markers, skill_body
            )
            controls.update({arm + ":" + key: value for key, value in arm_controls.items()})
            runs[arm] = loaded[arm]["details"]
        except (OSError, ValueError, KeyError, TypeError, IndexError, SkillEvalError) as error:
            controls[arm + ":complete_evidence"] = False
            runs[arm] = {"error": type(error).__name__}
    if len(loaded) == 2:
        baseline, selected = (loaded[arm] for arm in ARMS)
        controls["same_task_and_harness"] = all(
            baseline["audit"].get(key) == selected["audit"].get(key) and key in baseline["audit"]
            for key in (
                "identity",
                "input_digest",
                "image",
                "catalog_sha256",
                "native_config_sha256",
                "codex_identity",
            )
        )
        controls["same_tools"] = bool(baseline["context"].get("tools")) and baseline["context"].get(
            "tools"
        ) == selected["context"].get("tools")
        controls["same_top_level_instructions"] = baseline["context"].get(
            "top_level_instructions"
        ) == selected["context"].get("top_level_instructions")

        def shared_messages(context: dict[str, Any]) -> list[tuple[str, str]]:
            catalogs = {row["sha256"] for row in context.get("skill_catalogs", [])}
            return [
                (row["role"], row["sha256"])
                for row in context.get("messages", [])
                if row["role"] in {"system", "developer"} and row["sha256"] not in catalogs
            ]

        controls["same_shared_instructions"] = shared_messages(
            baseline["context"]
        ) == shared_messages(selected["context"])

        def builtins(context: dict[str, Any]) -> list[dict[str, str]]:
            return [
                item
                for row in context.get("skill_catalogs", [])
                for item in row["skills"]
                if item["name"] != SKILL_NAME
            ]

        controls["same_advertised_builtins"] = builtins(baseline["context"]) == builtins(
            selected["context"]
        )
        controls["same_builtin_bytes"] = [
            row
            for row in baseline["audit"].get("context", {}).get("skills_after", [])
            if row["path"].startswith(".system/")
        ] == [
            row
            for row in selected["audit"].get("context", {}).get("skills_after", [])
            if row["path"].startswith(".system/")
        ]
        controls["invocation_only_prompt_delta"] = (
            _prompt(root / "absent/prompt.txt") == PROMPT
            and _prompt(root / "available/prompt.txt")
            == f"Use ${SKILL_NAME} for this task.\n\n" + PROMPT
        )
    return {
        "schema_version": 1,
        "kind": "skilleval-solver-qualification",
        **_conclusion(controls),
        "admitted": False,
        "scope": (
            "synthetic native tool access and namespace lifecycle; no model or task quality claim"
        ),
        "controls": controls,
        "runs": runs,
    }


def _git(source: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=Task Author", "-c", "user.email=task@example.invalid", *arguments],
        cwd=source,
        check=True,
        capture_output=True,
        env={"PATH": os.defpath, "HOME": str(source.parent), "GIT_CONFIG_NOSYSTEM": "1"},
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    output = args.output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    markers = {name: uuid.uuid4().hex + "\n" for name in ("task", "public", "history", "resource")}
    source = output / "source"
    source.mkdir()
    (source / "history.txt").write_text(markers["history"])
    _git(source, "init", "--initial-branch=main")
    _git(source, "add", "history.txt")
    _git(source, "commit", "-m", "Add original project note")
    (source / "history.txt").unlink()
    (source / "AGENTS.md").write_text(
        "Run the existing unit tests after edits.\n" + markers["task"]
    )
    (source / "public-note.txt").write_text(markers["public"])
    (source / "value.py").write_text('VALUE = "before"\n')
    (source / "test_value.py").write_text(
        "import unittest\nfrom value import VALUE\n\n"
        "class ValueTest(unittest.TestCase):\n"
        "    def test_requested_value(self):\n        self.assertEqual(VALUE, 'after')\n"
    )
    _git(source, "add", "history.txt", "AGENTS.md", "public-note.txt", "value.py", "test_value.py")
    _git(source, "commit", "-m", "Request the updated value")
    task = output / "task"
    task_receipt = evidence.snapshot(source, task)
    selected = output / "selected"
    (selected / "references").mkdir(parents=True)
    skill_body = (
        f"---\nname: {SKILL_NAME}\ndescription: Help make and check a workspace change.\n---\n"
        "Read references/example.txt, make the requested edit, and run the existing tests.\n"
    )
    (selected / "SKILL.md").write_text(skill_body)
    (selected / "references/example.txt").write_text(markers["resource"])
    skill = output / "skill"
    skill_receipt = capture_skill(
        selected, None, skill, source_url="synthetic:solver-control", source_revision="1"
    )
    (output / "inputs.json").write_text(json.dumps({"markers": markers, "skill_body": skill_body}))
    workers = {}
    for arm in ARMS:
        spec = {
            "arm": arm,
            "output": str(output / arm),
            "runtime": str(args.runtime.resolve()),
            "image": str(args.image_archive.resolve()),
            "image_sha256": args.image_sha256,
            "catalog": str(args.catalog.resolve()),
            "skill": str(skill),
            "skill_digest": skill_receipt["manifest_sha256"],
            "input": str(task),
            "input_digest": task_receipt["manifest_sha256"],
            "prompt": (f"Use ${SKILL_NAME} for this task.\n\n" if arm == "available" else "")
            + PROMPT,
        }
        spec_path = output / (arm + ".json")
        spec_path.write_text(json.dumps(spec, indent=2) + "\n")
        try:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--worker", str(spec_path)],
                check=False,
            )
            workers[arm] = {"returncode": result.returncode}
        except OSError as error:
            workers[arm] = {"error": type(error).__name__}
        print(json.dumps({"arm": arm, **workers[arm]}), flush=True)
        if workers[arm].get("returncode") != 0:
            # Do not create another VM when the previous worker cannot establish cleanup.
            break
    report = assess(output, markers, skill_body)
    report["workers"] = workers
    report["controls"]["worker_cleanup"] = len(workers) == 2 and all(
        row.get("returncode") == 0 for row in workers.values()
    )
    report["probe_sha256"] = file_digest(Path(__file__))
    report.update(_conclusion(report["controls"]))
    (output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        return worker(Path(sys.argv[2]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--image-archive", type=Path, required=True)
    parser.add_argument("--image-sha256", required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    report = run(parser.parse_args())
    print(
        json.dumps(
            {
                "status": report["status"],
                "failed": [key for key, passed in report["controls"].items() if not passed],
            }
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
