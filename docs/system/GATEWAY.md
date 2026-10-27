# Native qualification gateway

`skilleval.gateway` supplies an explicit, bounded host inference capability for
Codex CLI with adapter baseline **0.164.0**, **gpt-6.1-sol**, **high** reasoning.
Stable same-major upgrades at or above that baseline may run qualification; the
actual version is recorded and native protocol controls must pass again. Older,
new-major, prerelease, and unknown versions require adapter review. This route uses an
existing ChatGPT login. It has no Platform API-key mode, login flow, refresh
operation, or arbitrary upstream setting. Importing the module never reads
credentials or starts a service. Ordinary deterministic checks use synthetic
credentials and streams only.

The native Codex model/tool loop runs inside the VM. Guest networking stays off;
the only new host capability is a mounted Unix socket:

```text
native guest Codex -> guest 127.0.0.1 relay -> mounted Unix socket
  -> host broker -> https://chatgpt.com/backend-api/codex/responses
```

The host broker owns credentials and opens no TCP listener. The guest relay owns
no credentials and binds only IPv4 loopback. smolvm 1.32.3's `--mount-socket`
uses vsock independently of guest TCP networking. Socket reachability, host
isolation, and the actual native loop still require explicit live qualification.
Passing a gateway test or preparing a profile does not admit scored execution.

## Supported host boundary

The trusted controller explicitly selects an existing native ChatGPT auth file
and a fresh, absolute private directory. It must keep that auth file, the broker
process, receipts, hidden grading inputs, and all other host paths outside guest
mounts. A short path avoids macOS's Unix-socket pathname limit.

```python
from pathlib import Path
from skilleval.gateway import GatewayLimits, broker_scope, load_chatgpt_auth

# This explicit operation reads the selected host login. Never print/serialize auth.
auth = load_chatgpt_auth(selected_host_auth_path)
limits = GatewayLimits(max_requests=4, timeout_seconds=60, lifetime_seconds=180)
with broker_scope(auth, Path("/tmp/fresh-qualification"), limits=limits) as broker:
    # Mount only broker.socket_path at /run/inference.sock in the network-off VM.
    # Run and supervise the VM; persist broker.receipts as private metadata.
    pass
assert broker.clean is True
```

`broker_scope` creates the directory with mode `0700`, refuses pre-existing
directories, and defaults to `inference.sock` within it. An explicit `socket_path`
must share that directory; its encoded pathname may not exceed 100 bytes.
The context stops services, cancels upstream connections, removes its socket and
directory, and raises a safe error if cleanup is incomplete. `GatewayHandle`
contains socket/port metadata, copied receipts, and cleanup status; it contains
no auth object. External receipt-sink failure revokes the service.

An optional trusted `request_observer` receives actual guest request bytes before
forwarding, without host authentication headers. The native development runner
uses it to retain request hashes and skill exposure observations; it does not
persist raw request bodies. An observer failure refuses the request and revokes
later forwarding. An observed request is an attempted input, so correlate it with
the successful provider receipt before claiming upstream exposure.

`load_chatgpt_auth` accepts native `chatgpt` or legacy unspecified auth mode with
token/account fields. It rejects missing, symlinked, oversized, malformed,
duplicate-key, API-key-mode, or expired input with a value-free error. It opens
with `O_NONBLOCK` before checking for a regular file, so a FIFO cannot stall
startup. The final path is opened without following a symlink, reads are bounded
to 128 KiB, and changes during the read are refused. JWT payload decoding checks
the finite numeric `exp` claim only; it does not authenticate a
JWT signature. The fixed HTTPS upstream authenticates the credential. The broker
requires enough remaining validity for its request timeout plus 30 seconds at
startup and checks again before each request. It never refreshes or writes the
login cache. Authentication expiry and upstream 401 fail closed.

`GatewayLimits` bounds admitted requests (maximum 32), concurrency (maximum 2),
body bytes (maximum 8 MiB), request deadline (maximum 300 seconds), and total
service lifetime (maximum 900 seconds). Defaults are smaller. These are request
and time bounds, not a token or monetary quota. The upstream implementation also
caps response bytes at 64 MiB. Original task/trial timeouts and external cleanup
supervision remain necessary.

## Guest setup

Transfer the preserved credential-free vendor module and required license as
tooling, not task source. Run its guest relay against the one mounted socket:

```sh
python3 /run/openbench_gateway.py relay \
  --socket /run/inference.sock --port 8765 \
  --max-body-bytes 4194304 --timeout-seconds 60
```

The vendor module's `broker` CLI is an upstream interface, **not** the supported
host authentication entry point: it lacks the typed wrapper's expiry checks and
qualification ceilings. Use `broker_scope` on the host.

`codex_provider_config(8765, Path("/run/models.json"))` returns guest TOML with
the exact model/effort, an explicit catalog path, a distinct custom provider,
HTTP Responses transport, no authentication requirement, disabled hosted search,
disabled output-token replay, disabled concurrent reasoning summaries, disabled
request compression, and disabled apps. It includes full tool execution inside
the separately qualified VM. Write the profile and pinned native catalog as
read-only tooling in a fresh guest `CODEX_HOME`; supply no host auth cache,
provider key, plugins, MCP settings, or ambient host configuration.

The provider display name is `skilleval qualification`. In pinned Codex,
[`is_openai()` and request construction](https://github.com/openai/codex/blob/6aaac694803004a63c6f393d153c96d22521a6b9/codex-rs/core/src/client.rs#L1048)
use the exact display name `OpenAI` to retain private message metadata and
encrypted non-reasoning replay fields. The custom display name suppresses those
fields, keeping requests inside the strict broker allowlist. Reasoning replay
remains native. This configuration difference is part of the qualification;
captured requests must verify its effective behavior.

The controller must check the actual guest binary version and catalog digest.
The exact native catalog's Sol entry uses Responses Lite and
`tool_mode=code_mode_only`, matching the gateway's fixed upstream header and
requiring the exact release's `codex-code-mode-host` companion inside the image.
Preserve that native tool mode. Select plain text output and keep qualification below
compaction limits. The broker admits only `/responses`; model discovery and
compaction endpoints are not available. Guest auth is absent, so native
account/plan discovery and workspace routing are absent too. The supplied
account header is resolved only on the host. This is a specific qualified route,
not a claim of equivalence with every interactive Codex feature.

## Request and receipt contract

The broker validates pinned model/effort, streaming and `store=false`, explicit
local input, local function/custom tools and namespaced history. It refuses
hosted tools, remote references, external schemas, arbitrary routes, unsupported
framing, compression, upgrades, and unknown fields. It reconstructs upstream
headers, so guest credentials or destination headers have no authority. HTTPS
host/path and certificate validation are fixed. Redirects and non-SSE responses
are rejected without exposing upstream errors. Stream cancellation, deadline,
request limits, and receipt failure fail closed.

The host validator has one explicit adaptation from the pinned upstream source:
`output_text` may retain `annotations: []` and `logprobs: []`. The native DTO
[deliberately preserves those empty arrays](https://github.com/openai/codex/blob/6aaac694803004a63c6f393d153c96d22521a6b9/codex-rs/protocol/src/models.rs#L898).
They remain forbidden on `input_text`; nonempty, null, and unknown metadata
remain rejected. Validation preserves the actual request bytes and history.
No additional reasoning-field compatibility is implied.

The longer [native runner](NATIVE.md) retains bounded field/type-only diagnostics
for rejected histories. These diagnostics contain no prompt or encrypted values;
they sample only part of the history and can leave a later unsupported shape
unobserved. A rejection is a protocol failure, never a task-quality judgment.

Receipts contain bounded known metadata fields, the actual upstream peer's
validated numeric IP/port when observed, and nonnegative token usage when
observed. They exclude authorization, account identity, request/response bodies,
prompts, model text, raw exceptions, and upstream error bodies. Missing usage is
an empty observation, not proof of zero. Keep native raw traces private under the
ordinary evidence policy. `test_transport` is an explicitly named in-process
synthetic test seam; no URL, configuration file, or CLI option can redirect the
production transport.

The in-memory receipt log holds at most 256 records with scalar strings capped
at 256 characters. Exhausting that log fails closed and revokes the broker,
including when rejected requests consume no inference budget. Receipt loss or
overflow leaves cleanup status incomplete; callers must retain that partial
result rather than treating the final native turn as sufficient evidence.

Before live model use, exercise the actual native first request and subsequent
tool-result request against a synthetic responder. Narrow compatibility changes
must preserve the allowlist. Then prove both allowed inference/tool execution
and denied host/network/hidden-input operations through the real VM route.
Deterministic tests cover real local UDS/TCP and policy effects; they do not prove
public egress, provider acceptance, runtime isolation, or semantic judge quality.

`scripts/qualify-native.py` is a separate explicit control. It requires
an image archive and its SHA-256, verifies and privately copies the Docker-save
archive through the same inspected import boundary, pins the exact catalog hash,
and starts a fresh network-off VM with only the inference socket mounted. It
records the first and subsequent real native requests, supplies synthetic SSE
tool/final events, checks a native tool effect, exercises host/guest filesystem
and network controls with known positives, and preserves stop/delete receipts.
The default mode never selects a real credential or performs a model call. Its
`synthetic_native_passed` result is not authenticated provider admission. Private
synthetic raw requests and original failed controls remain under the explicit
ignored output directory.

Adding `--auth-file PATH` explicitly selects an existing host ChatGPT login and
performs real inference through the fixed broker. That flag is the only real
authentication selector; there is no API-key or automatic credential lookup.
The auth file is never copied or mounted. This mode sends only a small public
runtime-control prompt asking the model to use its local command tool, create
`/workspace/native-tool-ran` containing `native-ok`, print `NATIVE_TOOL_OK`, and
finish. No private case content is involved.

Authenticated limits are four requests, 90 seconds per request, 300 seconds of
broker lifetime, and 240 seconds of native execution. The VM, disabled network,
mounted socket, guest configuration, and canary controls are shared with the
synthetic route. Raw broker request capture is disabled in authenticated mode;
only private native JSON events, metadata receipts, and bounded field/type-only
rejection diagnostics are retained. Diagnostics include known protocol enums and
collection lengths, never prompt, header, encrypted, or other string values. A passing
result requires completed native events, an observed command exit code of zero
with the marker in its output, the independent guest file effect, successful
provider stream receipts, the host/guest controls, and observed cleanup. It uses
the distinct `authenticated_native_passed` status and still reports
`admitted: false`: it does not establish task quality, judge calibration, or
campaign admission.

The native control captures the complete local runtime/host and control-source
identity before and after execution, hashes its rendered tooling, and registers
a durable recovery ticket before creating the VM. Its cleanup shares the
[environment lifecycle](ENVIRONMENT.md). That evidence can contribute to the
separate bounded environment admission seal; it cannot independently admit a
scored study. Keep the selected auth file outside the guest and retained tooling.

## Run the explicit control

Use the [pinned native catalog](https://github.com/openai/codex/blob/6aaac694803004a63c6f393d153c96d22521a6b9/codex-rs/models-manager/models.json)
with SHA-256 `943ca7fe1d19ed019054158303f0dbbd80b3bef43b3aa425a71aa7cb3fb52c2b`
and a [prepared image](../../environments/python-review/README.md). Start with
synthetic events:

```sh
uv run python scripts/qualify-native.py \
  --runtime /path/to/smolvm --image-archive /path/to/task-image.tar \
  --image-sha256 ARCHIVE_SHA256 --catalog /path/to/models.json \
  --output .local/native-synthetic
```

Adding `--auth-file /path/to/existing/auth.json` explicitly performs the bounded
real model control; choose a new output directory for each attempt. Retain failed
attempts. These operations are separate from `scripts/check` and campaign
admission. The roadmap records current observed outcomes and remaining gates.

## Provenance and maintenance

The gateway implementation was copied from
[OpenBench commit 39fec903f1e73bd3e8e3b15cacefbcce051bd1a6](https://github.com/davisbuilds/openbench/blob/39fec903f1e73bd3e8e3b15cacefbcce051bd1a6/obench/sandbox_gateway.py).
The original upstream SHA-256 is
`ed02f42cc52a22cfd16d49e3d639a27ce66de0b3bc3a9f41758c646bb26aed48`.
The adapted host-validator SHA-256 is
`5159253383750db2583a51ea6789ff9e7cb897dfb7c73e2629293c84bd5ef5a0`.
The adjacent `openbench_gateway.patch` and `openbench_gateway.provenance.json`
record the complete narrow adaptation and its source. Reports separately hash
the host vendor module and the guest relay module: an image can retain the
original guest relay because that component does not validate request bodies.
The adjacent `src/skilleval/_vendor/OPENBENCH_LICENSE` preserves Matthew Lam's
2026 MIT notice. The wrapper imports no sibling repository. Selected real-socket
test scenarios were adapted from the same revision's
[gateway tests](https://github.com/davisbuilds/openbench/blob/39fec903f1e73bd3e8e3b15cacefbcce051bd1a6/obench/tests/test_sandbox_gateway.py).

Keep unrelated vendor bytes unformatted and isolate lint/type exclusions to that
module. Keep the explicit adaptation patch/provenance current; the typed wrapper
owns authentication safety checks and lifecycle policy.
Reinspect upstream changes, preserve notices, update the hash test, and renew
qualification whenever implementation, model/catalog, runtime, or configuration
changes.
