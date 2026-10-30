#!/usr/bin/env python3
"""Opt-in native Codex qualification in a network-off smolvm.

Invoke with uv run python scripts/qualify-native.py --help. The image archive must
contain Python, Git, Codex 0.164.0, and the preserved gateway/notice under
/opt/skilleval/. Default mode uses synthetic auth/events. --auth-file explicitly
selects an existing host ChatGPT login and performs bounded real inference.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import re
import shutil
import socket
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from skilleval._vendor import openbench_gateway as vendor
from skilleval.errors import SkillEvalError
from skilleval.gateway import (
    CODEX_VERSION,
    EFFORT,
    MODEL,
    GatewayLimits,
    broker_scope,
    codex_provider_config,
    load_chatgpt_auth,
)
from skilleval.harnesses.codex import codex_version_identity, parse_codex_jsonl
from skilleval.identity import snapshot_identity
from skilleval.lifecycle import Lifecycle, LifecycleInterruptedError, interrupt_scope
from skilleval.qualification import _image_archive, _invoke

CATALOG_SHA256 = "943ca7fe1d19ed019054158303f0dbbd80b3bef43b3aa425a71aa7cb3fb52c2b"
GUEST_SOCKET = "/run/inference.sock"
RELAY_PORT = 8765
TOOL_COMMAND = (
    'python3 -c "from pathlib import Path; '
    "Path('/workspace/native-tool-ran').write_text('native-ok'); print('NATIVE_TOOL_OK')\""
)
REAL_PROMPT = (
    "Use the local command tool to create /workspace/native-tool-ran containing exactly "
    "native-ok and print NATIVE_TOOL_OK. Then give a final completion message. "
    "This is a small runtime control; no other task work is needed."
)
_FIELD_NAME = re.compile(r"[a-z_][a-z0-9_]{0,63}\Z")
_PROTOCOL_TYPES = {
    "message",
    "reasoning",
    "additional_tools",
    "function_call",
    "custom_tool_call",
    "function_call_output",
    "custom_tool_call_output",
    "compaction",
    "context_compaction",
    "input_text",
    "output_text",
    "input_image",
    "summary_text",
    "reasoning_text",
    "function",
    "custom",
    "namespace",
    "text",
    "grammar",
    "object",
    "array",
    "string",
    "integer",
    "number",
    "boolean",
    "null",
}
_POLICY_REASONS = {
    "nonempty output text metadata is forbidden",
    "unsupported request shape",
    "request violates inference policy",
    "expected text",
    "unsupported content",
    "only text content is permitted",
    "invalid reasoning summary",
    "unsupported reasoning context",
    "pinned request mismatch",
    "pinned effort mismatch",
    "invalid client metadata",
    "unsupported service tier",
    "unsupported include",
    "invalid output limit",
    "hosted tools are forbidden",
    "hosted input and references are forbidden",
    "unknown local tool history",
    "invalid tool choice",
    "unknown local tool",
    "invalid local tool identity",
    "invalid function tool",
    "invalid custom tool",
    "unsupported custom format",
    "invalid local namespace",
    "invalid input item",
}


class ProbeError(Exception):
    """A failed explicit control whose evidence remains in the output directory."""


def mode_settings(authenticated: bool) -> dict[str, Any]:
    return {
        "mode": "authenticated" if authenticated else "synthetic",
        "max_requests": 4 if authenticated else 3,
        "request_timeout_seconds": 90 if authenticated else 30,
        "lifetime_seconds": 300 if authenticated else 240,
        "native_timeout_seconds": 240 if authenticated else 100,
        "prompt": REAL_PROMPT
        if authenticated
        else "Perform the synthetic local command control, then report completion.",
    }


def native_control_summary(raw: str) -> dict[str, Any]:
    parsed = parse_codex_jsonl(raw)
    command_observed = any(
        event.get("type") == "item.completed"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "command_execution"
        and event["item"].get("status") == "completed"
        and type(event["item"].get("exit_code")) is int
        and event["item"]["exit_code"] == 0
        and isinstance(event["item"].get("aggregated_output"), str)
        and "NATIVE_TOOL_OK" in event["item"].get("aggregated_output", "")
        for event in parsed["events"]
    )
    return {
        "execution_status": parsed["execution_status"],
        "successful_command_marker": command_observed,
        "parse_error_count": len(parsed["parse_errors"]),
        "error_count": len(parsed["errors"]),
    }


def rejection_diagnostic(raw: bytes, error: ValueError) -> dict[str, Any]:
    """Only bounded field names, trusted code sites, and known protocol enums."""

    def names(values: Any) -> list[str]:
        return sorted(
            {
                value
                if isinstance(value, str) and _FIELD_NAME.fullmatch(value)
                else "<redacted-field>"
                for value in values
            }
        )[:16]

    def item_shape(value: object) -> dict[str, Any]:
        def metadata_shape(item: dict[str, Any]) -> dict[str, Any]:
            result = {}
            for field in ("annotations", "logprobs"):
                if field not in item:
                    continue
                content = item[field]
                kind = (
                    "array"
                    if isinstance(content, list)
                    else "object"
                    if isinstance(content, dict)
                    else "null"
                    if content is None
                    else "string"
                    if isinstance(content, str)
                    else "boolean"
                    if isinstance(content, bool)
                    else "number"
                )
                result[field] = {"kind": kind}
                if isinstance(content, (list, dict)):
                    result[field]["length"] = len(content)
            return result

        if not isinstance(value, dict):
            return {"kind": "non-object"}
        result: dict[str, Any] = {"keys": names(value)}
        if metadata := metadata_shape(value):
            result["metadata"] = metadata
        kind = value.get("type")
        if isinstance(kind, str) and kind in _PROTOCOL_TYPES:
            result["type"] = kind
        content = value.get("content")
        if isinstance(content, list):
            result["content_items"] = [
                {
                    "keys": names(part),
                    "metadata": metadata_shape(part),
                    **(
                        {"type": part["type"]}
                        if isinstance(part.get("type"), str) and part["type"] in _PROTOCOL_TYPES
                        else {}
                    ),
                }
                for part in content[:4]
                if isinstance(part, dict)
            ]
        return result

    diagnostic: dict[str, Any] = {
        "schema_version": 1,
        "event": "inference_policy_rejected",
        "frames": [],
    }
    failure: BaseException | None = error
    for _ in range(4):
        if failure is None:
            break
        reason = failure.args[0] if len(failure.args) == 1 else None
        if isinstance(reason, str) and reason in _POLICY_REASONS:
            diagnostic["reason"] = reason
        trace = failure.__traceback__
        while trace is not None and len(diagnostic["frames"]) < 8:
            frame = trace.tb_frame
            if frame.f_code.co_filename == vendor.__file__:
                site: dict[str, Any] = {
                    "function": frame.f_code.co_name[:64],
                    "line": trace.tb_lineno,
                }
                if frame.f_code.co_name == "_keys":
                    value = frame.f_locals.get("value")
                    if isinstance(value, dict):
                        site["extra_keys"] = names(
                            set(value) - set(frame.f_locals.get("allowed", ()))
                        )
                        site["missing_keys"] = names(
                            set(frame.f_locals.get("required", ())) - set(value)
                        )
                        site["rejected_item"] = item_shape(value)
                diagnostic["frames"].append(site)
            trace = trace.tb_next
        failure = failure.__context__
    try:
        body = json.loads(raw)
        if isinstance(body, dict):
            diagnostic["root_keys"] = names(body)
            items = body.get("input")
            if isinstance(items, list):
                indices = (
                    list(range(len(items)))
                    if len(items) <= 16
                    else [*range(4), *range(len(items) - 12, len(items))]
                )
                diagnostic["input_items"] = [
                    {"index": index, **item_shape(items[index])} for index in indices
                ]
    except (ValueError, TypeError, RecursionError):
        diagnostic["invalid_json"] = True
    if len(json.dumps(diagnostic).encode()) > 16384:
        diagnostic.pop("input_items", None)
        diagnostic["truncated"] = True
        for site in diagnostic["frames"]:
            site.pop("rejected_item", None)
    return diagnostic


def declared_tools(body: dict[str, Any]) -> list[tuple[str | None, dict[str, Any]]]:
    found: list[tuple[str | None, dict[str, Any]]] = []

    def visit(tools: list[dict[str, Any]], namespace: str | None = None) -> None:
        for tool in tools:
            if tool.get("type") == "namespace":
                visit(
                    tool["tools"],
                    tool["name"] if namespace is None else namespace + "." + tool["name"],
                )
            else:
                found.append((namespace, tool))

    visit(body.get("tools", []))
    for item in body.get("input", []):
        if item.get("type") == "additional_tools":
            visit(item["tools"])
    return found


def tool_item(body: dict[str, Any], command: str = TOOL_COMMAND) -> dict[str, Any]:
    tools = declared_tools(body)
    for namespace, tool in tools:
        if tool.get("name") == "exec" and tool.get("type") == "custom":
            item = {
                "type": "custom_tool_call",
                "id": "ctc_synthetic",
                "call_id": "call_synthetic",
                "name": "exec",
                "status": "completed",
                "input": "const result = await tools.exec_command({cmd: "
                + json.dumps(command)
                + "}); text(result);",
            }
            if namespace is not None:
                item["namespace"] = namespace
            return item
    for desired in ("exec_command", "shell_command", "shell"):
        for namespace, tool in tools:
            if tool.get("name") != desired or tool.get("type") != "function":
                continue
            if desired == "exec_command":
                arguments: dict[str, Any] = {"cmd": command}
            elif desired == "shell_command":
                arguments = {"command": command}
            else:
                arguments = {"command": ["/bin/sh", "-c", command]}
            item = {
                "type": "function_call",
                "id": "fc_synthetic",
                "call_id": "call_synthetic",
                "name": desired,
                "arguments": json.dumps(arguments),
                "status": "completed",
            }
            if namespace is not None:
                item["namespace"] = namespace
            return item
    raise ProbeError("native request did not declare a supported local command tool")


def response_stream(item: dict[str, Any], identifier: str) -> bytes:
    events: list[dict[str, Any]] = [
        {"type": "response.created", "response": {"id": identifier, "object": "response"}},
        {"type": "response.output_item.added", "output_index": 0, "item": item},
    ]
    if item["type"] == "message":
        events.append(
            {
                "type": "response.output_text.delta",
                "output_index": 0,
                "content_index": 0,
                "item_id": item["id"],
                "delta": item["content"][0]["text"],
            }
        )
    events.extend(
        [
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {
                "type": "response.completed",
                "response": {
                    "id": identifier,
                    "object": "response",
                    "status": "completed",
                    "output": [item],
                    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                },
            },
        ]
    )
    return b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)


def has_successful_tool_result(body: dict[str, Any]) -> bool:
    """Require the result of our call, independently of its earlier call text."""
    for item in body.get("input", []):
        if (
            item.get("type") not in {"function_call_output", "custom_tool_call_output"}
            or item.get("call_id") != "call_synthetic"
            or item.get("status") not in (None, "completed")
        ):
            continue
        output = item.get("output")
        if isinstance(output, str):
            text = output
        elif isinstance(output, list):
            text = "\n".join(
                part.get("text", "")
                for part in output
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        else:
            continue
        if "NATIVE_TOOL_OK" in text:
            return True
    return False


class SyntheticStream:
    status = 200

    def __init__(self, body: bytes) -> None:
        self.headers = {"Content-Type": "text/event-stream"}
        self.body = io.BytesIO(body)

    def read(self, size: int) -> bytes:
        return self.body.read(size)

    def close(self) -> None:
        self.body.close()


GUEST_RUNNER = r"""
import http.client,json,os,pathlib,signal,socket,subprocess,sys,time
root=pathlib.Path('/run/qualification')
root.mkdir(exist_ok=True)
work=pathlib.Path('/workspace/synthetic')
work.mkdir(parents=True,exist_ok=True)
home=root/'home'
home.mkdir(exist_ok=True)
codexhome=root/'codex-home'
codexhome.mkdir(exist_ok=True)
(root/'config.toml').replace(codexhome/'config.toml')
allowed=root/'allowed-canary'
allowed.write_text('guest-canary')
controls=[]
targets=json.loads((root/'targets.json').read_text())
mode=json.loads((root/'mode.json').read_text())
def access(target,method):
    try:
        if method=='direct': return pathlib.Path(target).read_text()
        if method=='symlink':
            link=root/'read-link'
            link.unlink(missing_ok=True)
            link.symlink_to(target)
            return link.read_text()
        code='import pathlib,sys; print(pathlib.Path(sys.argv[1]).read_text())'
        p=subprocess.run(['python3','-c',code,target],capture_output=True,timeout=5)
        return p.stdout.decode().strip() if p.returncode==0 else None
    except OSError: return None
for method in ('direct','symlink','subprocess'):
    good=access(str(allowed),method)=='guest-canary'
    denied=access(targets['host_canary'],method) is None
    controls.append({'control':'filesystem_'+method,'positive':good,'denied':denied})
try:
    pathlib.Path(targets['host_canary']).write_text('guest-overwrite')
    denied_write=False
except OSError: denied_write=True
controls.append({'control':'host_write','denied':denied_write})
for host,port in targets['network']:
    try:
        connection=socket.create_connection((host,port),timeout=2)
        connection.close()
        denied=False
    except OSError: denied=True
    controls.append({'control':'network','host':host,'port':port,'denied':denied})
relay=None
try:
    with (root/'relay-stdout.jsonl').open('wb') as out,(root/'relay-stderr.log').open('wb') as err:
        relayargs=['python3','/opt/skilleval/openbench_gateway.py','relay',
            '--socket','/run/inference.sock','--port','8765',
            '--max-body-bytes','4194304','--timeout-seconds',str(mode['request_timeout_seconds'])]
        relay=subprocess.Popen(relayargs,stdout=out,stderr=err,start_new_session=True)
    ready=False
    for _ in range(100):
        try:
            c=http.client.HTTPConnection('127.0.0.1',8765,timeout=1)
            c.request('GET','/responses')
            r=c.getresponse()
            ready=r.status==405
            r.read();c.close()
            if ready: break
        except OSError: pass
        time.sleep(.05)
    controls.append({'control':'relay_loopback','positive':ready})
    (root/'controls.json').write_text(json.dumps(controls))
    if not ready or any(not c.get('positive',True) or not c.get('denied',True) for c in controls):
        raise SystemExit(21)
    env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':str(home),'CODEX_HOME':str(codexhome),'LANG':'C.UTF-8'}
    argv=['codex','exec','--json','--skip-git-repo-check','--color','never',mode['prompt']]
    timed_out=False
    try:
        result=subprocess.run(argv,cwd=work,env=env,capture_output=True,
            timeout=mode['native_timeout_seconds'])
        stdout,stderr,returncode=result.stdout,result.stderr,result.returncode
    except subprocess.TimeoutExpired as error:
        stdout,stderr,returncode=error.stdout or b'',error.stderr or b'',-1
        timed_out=True
    (root/'native-stdout.jsonl').write_bytes(stdout)
    (root/'native-stderr.log').write_bytes(stderr)
    sys.stdout.buffer.write(stdout[:65536])
    sys.stderr.buffer.write(stderr[:65536])
    marker=pathlib.Path('/workspace/native-tool-ran')
    evidence={'returncode':returncode,'stdout_bytes':len(stdout),
        'stderr_bytes':len(stderr),'timed_out':timed_out,
        'native_tool_ran':marker.exists() and marker.read_text()=='native-ok'}
    (root/'native-result.json').write_text(json.dumps(evidence))
    raise SystemExit(0 if returncode==0 and evidence['native_tool_ran'] else 22)
finally:
    if relay is not None and relay.poll() is None:
        os.killpg(relay.pid,signal.SIGTERM)
        try: relay.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(relay.pid,signal.SIGKILL);relay.wait(timeout=5)
"""


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


def machine_names(raw: str) -> set[str]:
    try:
        rows = json.loads(raw)
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("name"), str) for row in rows
        ):
            raise ValueError
        names = {row["name"] for row in rows}
        if len(names) != len(rows):
            raise ValueError
        return names
    except (ValueError, TypeError, KeyError):
        raise ProbeError("machine observer returned an invalid JSON inventory") from None


@interrupt_scope()
def run(
    runtime: Path,
    image: Path,
    image_sha256: str,
    catalog: Path,
    output: Path,
    auth_file: Path | None = None,
) -> dict[str, Any]:
    authenticated = auth_file is not None
    mode = mode_settings(authenticated)
    if digest(catalog) != CATALOG_SHA256:
        raise SkillEvalError("catalog differs from pinned Codex 0.164.0 source")
    image_identity = _image_archive(image, image_sha256)
    run_identity = snapshot_identity(str(runtime))
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    state = output / "runtime-state"
    state.mkdir(mode=0o700)
    runtime_home = Path(
        tempfile.mkdtemp(prefix="skn-", dir="/private/tmp" if sys.platform == "darwin" else "/tmp")
    )
    for name in ("config", "data", "tmp", "docker"):
        (state / name).mkdir(mode=0o700)
    for name in ("cache", "run"):
        (runtime_home / name).mkdir(mode=0o700)
    env = {
        "HOME": str(runtime_home),
        "XDG_CONFIG_HOME": str(state / "config"),
        "XDG_DATA_HOME": str(state / "data"),
        "XDG_CACHE_HOME": str(runtime_home / "cache"),
        "XDG_RUNTIME_DIR": str(runtime_home / "run"),
        "TMPDIR": str(state / "tmp"),
        "DOCKER_CONFIG": str(state / "docker"),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "C.UTF-8",
    }
    machine = "skilleval-native-" + uuid.uuid4().hex
    report: dict[str, Any] = {
        "schema_version": 1,
        "identity": run_identity,
        "status": "partial",
        "admitted": False,
        "scope": "authenticated native provider/tool control"
        if authenticated
        else "synthetic native HTTP/tool loop only",
        "credential_mode": "existing-host-chatgpt-login" if authenticated else "synthetic",
        "machine_name": machine,
        "runtime_home": str(runtime_home),
        "runtime_home_removed": False,
        "runtime_sha256": digest(runtime),
        "probe_sha256": digest(Path(__file__)),
        "gateway_sha256": digest(Path(vendor.__file__).parent.parent / "gateway.py"),
        "gateway_vendor_sha256": digest(Path(vendor.__file__)),
        "image_archive": image_identity,
        "image_sha256": image_sha256,
        "catalog_sha256": digest(catalog),
        "codex_baseline_version": CODEX_VERSION,
        "codex_version": None,
        "model": MODEL,
        "effort": EFFORT,
        "steps": [],
        "cleanup": [],
        "host_controls": {},
        "request_count": None if authenticated else 0,
        "request_count_basis": "broker-request-receipts" if authenticated else "captured-bodies",
        "inference_rejections": [],
    }
    created = False
    session = None
    broker = None
    listener = socket.socket()
    accepted: list[bool] = []
    stopping = threading.Event()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.1)

    def host_listener() -> None:
        while not stopping.is_set():
            try:
                client, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with client:
                accepted.append(True)
                client.sendall(b"HOST_CANARY")

    listener_thread = threading.Thread(target=host_listener, daemon=True)
    listener_thread.start()
    original_validate = vendor.validate_body

    def observe(raw: bytes, config: Any) -> dict[str, Any]:
        if authenticated:
            try:
                return original_validate(raw, config)
            except ValueError as error:
                if len(report["inference_rejections"]) < 4:
                    try:
                        diagnostic = rejection_diagnostic(raw, error)
                        report["inference_rejections"].append(diagnostic)
                        number = len(report["inference_rejections"])
                        (output / f"validation-rejection-{number}.json").write_text(
                            json.dumps(diagnostic, indent=2) + "\n"
                        )
                    except Exception:
                        report["rejection_diagnostic_failed"] = True
                raise
        index = report["request_count"]
        report["request_count"] += 1
        if index < 8:
            (output / f"request-{index + 1}.json").write_bytes(raw)
        return original_validate(raw, config)

    vendor.validate_body = observe
    calls = 0

    def transport(raw: bytes, _headers: dict[str, str], _timeout: float) -> SyntheticStream:
        nonlocal calls
        calls += 1
        body = json.loads(raw)
        if calls == 1:
            item = tool_item(body)
        elif calls == 2:
            if not has_successful_tool_result(body):
                raise ValueError("synthetic second request omitted successful tool result")
            item = {
                "type": "message",
                "id": "msg_synthetic",
                "role": "assistant",
                "phase": "final_answer",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": "Synthetic native qualification complete."}
                ],
            }
        else:
            raise ValueError("unexpected synthetic request")
        return SyntheticStream(response_stream(item, f"resp_synthetic_{calls}"))

    def step(label: str, arguments: list[str], timeout: int = 120, required: bool = True) -> bool:
        result = _invoke([str(runtime), *arguments], state, env, timeout)
        report["steps"].append({"label": label, **result})
        passed = (
            result["returncode"] == 0
            and not result["timed_out"]
            and not result.get("output_truncated")
            and not result.get("pipe_drain_incomplete")
        )
        if required and not passed:
            raise ProbeError(label)
        return passed

    def guest(label: str, command: list[str], timeout: int = 120, required: bool = True) -> bool:
        return step(
            label,
            ["machine", "exec", "--name", machine, "--timeout", f"{timeout}s", "--", *command],
            timeout + 5,
            required,
        )

    try:
        copied_image = state / "image.tar"
        if _image_archive(image, image_sha256, copied_image) != image_identity:
            raise ProbeError("image archive identity changed before copy")
        _image_archive(copied_image, image_sha256)
        step("runtime_version", ["--version"], 10)
        if not re.search(r"(?<![0-9.])1\.32\.3(?![0-9.])", report["steps"][-1]["stdout"]):
            raise ProbeError("runtime version mismatch")
        canary = output / "host-canary"
        marker = uuid.uuid4().hex
        canary.write_text(marker)
        report["host_controls"]["filesystem_positive"] = canary.read_text() == marker
        host_port = listener.getsockname()[1]
        with socket.create_connection(("127.0.0.1", host_port), timeout=3) as connection:
            report["host_controls"]["tcp_positive"] = connection.recv(32) == b"HOST_CANARY"
        with socket.create_connection(("example.com", 443), timeout=10) as connection:
            external_ip = connection.getpeername()[0]
            report["host_controls"]["external_tcp_positive"] = True
            report["host_controls"]["external_peer"] = external_ip
        if auth_file is None:
            payload = (
                base64.urlsafe_b64encode(json.dumps({"exp": time.time() + 3600}).encode())
                .decode()
                .rstrip("=")
            )
            auth_path = output / "synthetic-auth.json"
            auth_path.write_text(
                json.dumps(
                    {
                        "auth_mode": "chatgpt",
                        "tokens": {
                            "access_token": f"synthetic.{payload}.dummy",
                            "account_id": "synthetic-account",
                        },
                    }
                )
            )
        else:
            auth_path = auth_file
        auth = load_chatgpt_auth(auth_path)
        with broker_scope(
            auth,
            runtime_home / "b",
            limits=GatewayLimits(
                max_requests=mode["max_requests"],
                timeout_seconds=mode["request_timeout_seconds"],
                lifetime_seconds=mode["lifetime_seconds"],
            ),
            test_transport=None if authenticated else transport,
        ) as broker:
            session = Lifecycle.register(
                output,
                state,
                runtime_home,
                env,
                machine,
                runtime,
                image_identity,
                {
                    "cpus": 2,
                    "memory_mib": 2048,
                    "guest_network": False,
                    "host_mounts": [],
                    "secrets": [],
                    "socket_mounts": [{"host": str(broker.socket_path), "guest": GUEST_SOCKET}],
                },
                identity=run_identity,
            )
            session.mark_create_attempted()
            created = True
            step(
                "create",
                [
                    "machine",
                    "create",
                    "--name",
                    machine,
                    "--image",
                    str(copied_image),
                    "--cpus",
                    "2",
                    "--mem",
                    "2048",
                    "--mount-socket",
                    f"{broker.socket_path}:{GUEST_SOCKET}",
                    "--",
                    "sleep",
                    "infinity",
                ],
                300,
            )
            step("start", ["machine", "start", "--name", machine])
            step("machine_observer_positive", ["machine", "ls", "--json"])
            report["machine_observer_positive"] = machine in machine_names(
                report["steps"][-1]["stdout"]
            )
            if not report["machine_observer_positive"]:
                raise ProbeError("machine inventory did not detect the known-present machine")
            session.observe("machine_observer_positive")
            guest("codex_version", ["codex", "--version"])
            identity = codex_version_identity(report["steps"][-1]["stdout"])
            report["codex_identity"] = identity
            report["codex_version"] = identity["observed_version"]
            guest(
                "guest_toolchain_hashes",
                [
                    "python3",
                    "-c",
                    "import hashlib,json,pathlib; "
                    "names=['/usr/local/bin/codex','/usr/local/bin/codex-code-mode-host',"
                    "'/opt/skilleval/openbench_gateway.py']; "
                    "print(json.dumps({n:hashlib.sha256(pathlib.Path(n).read_bytes()).hexdigest() "
                    "for n in names}))",
                ],
            )
            entries = {
                "models.json": catalog.read_bytes(),
                "config.toml": codex_provider_config(
                    RELAY_PORT, Path("/run/qualification/models.json")
                ).encode(),
                "runner.py": GUEST_RUNNER.encode(),
                "mode.json": json.dumps(mode).encode(),
                "targets.json": json.dumps(
                    {
                        "host_canary": str(canary),
                        "network": [
                            [external_ip, 443],
                            ["127.0.0.1", host_port],
                            ["100.96.0.1", host_port],
                            ["192.168.127.1", host_port],
                            ["10.0.2.2", host_port],
                        ],
                    }
                ).encode(),
            }
            archive = state / "tooling.tar"
            report["tooling_sha256"] = {
                name: hashlib.sha256(content).hexdigest() for name, content in entries.items()
            }
            report["mode"] = mode
            report["vm_config"] = session.ticket["config"]
            report["broker_limits"] = {
                "max_requests": mode["max_requests"],
                "max_body_bytes": 4194304,
                "timeout_seconds": mode["request_timeout_seconds"],
                "max_concurrency": 1,
                "lifetime_seconds": mode["lifetime_seconds"],
            }
            with tarfile.open(archive, "w") as target:
                for filename, content in entries.items():
                    info = tarfile.TarInfo(filename)
                    info.size, info.mode = len(content), 0o444
                    target.addfile(info, io.BytesIO(content))
            step("copy_tooling", ["machine", "cp", str(archive), machine + ":/run/tooling.tar"])
            guest(
                "prepare_tooling",
                [
                    "python3",
                    "-c",
                    "import pathlib,tarfile; pathlib.Path('/run/qualification').mkdir(); "
                    "tarfile.open('/run/tooling.tar').extractall('/run/qualification',filter='data')",
                ],
            )
            native_ok = guest(
                "native_authenticated_loop" if authenticated else "native_synthetic_loop",
                ["python3", "/run/qualification/runner.py"],
                mode["native_timeout_seconds"] + 15,
                False,
            )
            summary = native_control_summary(report["steps"][-1]["stdout"])
            report["native_control"] = summary
            for filename in (
                "controls.json",
                "native-result.json",
                "relay-stdout.jsonl",
                "relay-stderr.log",
            ):
                guest(
                    "capture_" + filename, ["cat", "/run/qualification/" + filename], required=False
                )
            report["broker_receipts"] = broker.receipts
            if not native_ok:
                raise ProbeError("native control failed")
            if (
                summary["execution_status"] != "completed"
                or not summary["successful_command_marker"]
                or summary["parse_error_count"]
                or summary["error_count"]
            ):
                raise ProbeError("native events did not establish the successful command control")
            if not authenticated and calls != 2:
                raise ProbeError("expected exactly two admitted native requests")
            rows = [row for row in broker.receipts if row.get("event") == "request"]
            if authenticated and (
                not rows
                or any(
                    row.get("outcome") != "complete" or row.get("upstream_status") != 200
                    for row in rows
                )
            ):
                raise ProbeError("broker receipts did not establish successful provider streaming")
        report["broker_clean"] = broker.clean
        report["host_controls"]["filesystem_unchanged"] = canary.read_text() == marker
        report["host_controls"]["no_guest_tcp_connections"] = len(accepted) == 1
        if not all(
            value is True
            for key, value in report["host_controls"].items()
            if key != "external_peer"
        ):
            raise ProbeError("host-observed control failed")
        report["status"] = (
            "authenticated_native_passed" if authenticated else "synthetic_native_passed"
        )
    except (ProbeError, SkillEvalError, LifecycleInterruptedError, OSError) as error:
        report["error"] = (
            str(error)
            if isinstance(error, (ProbeError, SkillEvalError))
            else "host operation failed"
        )
    finally:
        if broker is not None:
            report["broker_receipts"] = broker.receipts
            report["broker_clean"] = broker.clean
            if authenticated:
                report["request_count"] = sum(
                    row.get("event") == "request" for row in broker.receipts
                )
        vendor.validate_body = original_validate
        stopping.set()
        listener.close()
        listener_thread.join(2)
        report["host_listener_stopped"] = not listener_thread.is_alive()
        if session is not None:
            ticket = session.cleanup()
            for key in ("cleanup", "cleanup_complete", "runtime_home_removed", "identity_changed"):
                report[key] = ticket.get(key)
            report["machine_observer_positive"] = ticket["observer_positive"]
            report["machine_observed_absent"] = ticket.get("observed_absent", False)
            report["artifacts"] = {"recovery.json": digest(output / "recovery.json")}
            if (state / "tooling.tar").exists():
                report["artifacts"]["runtime-state/tooling.tar"] = digest(state / "tooling.tar")
        elif created:
            for action in ("stop", "delete"):
                arguments = [str(runtime), "machine", action, "--name", machine]
                if action == "delete":
                    arguments.append("--force")
                report["cleanup"].append({"action": action, **_invoke(arguments, state, env, 30)})
            inventory = _invoke([str(runtime), "machine", "ls", "--json"], state, env, 30)
            report["cleanup"].append({"action": "observe_absent", **inventory})
            try:
                report["machine_observed_absent"] = (
                    inventory["returncode"] == 0
                    and not inventory["timed_out"]
                    and not inventory.get("output_truncated")
                    and not inventory.get("pipe_drain_incomplete")
                    and machine not in machine_names(inventory["stdout"])
                )
            except ProbeError:
                report["machine_observed_absent"] = False
        if session is None:
            report["cleanup_complete"] = bool(report["cleanup"]) and all(
                row["returncode"] == 0 and not row["timed_out"] for row in report["cleanup"]
            )
        if created:
            report["cleanup_complete"] = (
                report["cleanup_complete"]
                and report.get("machine_observer_positive") is True
                and report.get("machine_observed_absent") is True
            )
        if session is None and (not created or report["cleanup_complete"]):
            shutil.rmtree(runtime_home)
            report["runtime_home_removed"] = True
        elif created and not report["cleanup_complete"]:
            report["status"] = "partial"
        if not report["host_listener_stopped"] or report.get("identity_changed"):
            report["status"] = "partial"
        (output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        if session is None and report.get("cleanup_complete"):
            from skilleval.retention import prune_image

            prune_image(output, apply=True)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--image-archive", type=Path, required=True)
    parser.add_argument("--image-sha256", required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--auth-file",
        type=Path,
        help="Explicit real-inference mode using an existing host ChatGPT login (never copied)",
    )
    arguments = parser.parse_args()
    report = run(
        arguments.runtime.resolve(),
        arguments.image_archive.absolute(),
        arguments.image_sha256,
        arguments.catalog.resolve(),
        arguments.output.resolve(),
        arguments.auth_file.absolute() if arguments.auth_file is not None else None,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "admitted": False,
                "report": str(arguments.output / "report.json"),
            }
        )
    )
    return (
        0 if report["status"] in {"synthetic_native_passed", "authenticated_native_passed"} else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
