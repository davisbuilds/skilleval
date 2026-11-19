# Claude Code subscription judges

Judges and comparative synthesis can select `--judge claude-opus` instead of the
existing `codex-sol` default. The choice is frozen during preparation and checked
again during recording. It selects Claude Code, `claude-opus-5-5`, and requests
high effort. The provider effort setting is not independently observed.
Existing Codex packet bytes and recordings keep their original meaning. Task
agents and ON/OFF study execution remain on the existing Codex route.

## Authentication and execution

The unmodified native Claude Code client runs on the host and owns its existing
first-party subscription login. Skilleval does not extract, copy, forward or
refresh subscription credentials. API keys, alternate providers and inherited
authentication overrides are excluded; the native auth-status probe must report
a first-party `claude.ai` login. No automatic API-billing fallback is supplied.

The execution profile is `host-claude-subscription-vm-tools-v1`. Restricted mode,
an empty built-in tool set and one explicit MCP server separate the host client
from its investigation tools. The only model-directed command capability is
`mcp__vm__execute`, which launches commands through the existing unprivileged
solver boundary in a fresh, network-off VM. The native terminal tool is also
permitted. The VM has no host mounts or secrets. Selected source is transferred
as inert evidence and is never executed on the host.

This is a different execution profile from guest-native Codex. Claude's trusted
host client is not OS-sandboxed by these launch flags. Its configuration disables
instruction files, attachments, skills, memory, ambient plugins, hooks, connectors
and background work; an empty host working directory avoids repository ancestors.
The client's actual initialization inventory must match the allowed model,
tools and MCP server before the dispatcher admits command work. The small, exact
allowlist of native bundled plugins and agents stays distinct from user-installed
customizations. Native privacy behavior is retained. Managed policy
can override invocation settings, so unreviewed managed sources are refused.
A preflight cannot prove that new remote policy will never arrive later.

The adapter accepts stable same-major Claude Code updates at or above its
2.1.321 baseline. It records the resolved binary digest and actual version;
compatibility acceptance is not fresh protocol qualification. No installation,
upgrade or global-settings edit is hidden in an invocation. Use `--claude-binary`
to select an existing executable explicitly; otherwise the launcher resolves
`claude` from the operator's PATH before building its sanitized environment.

## Use

```sh
uv run skilleval judge prepare-calibration SUITE --judge claude-opus \
  --output PACKET
# Capture PACKET, and save the returned exact prompt as for the Codex route.
uv run skilleval native qualify INPUT --sha256 INPUT_SHA256 --prompt PROMPT \
  --mode judge --judge-policy investigative --judge claude-opus \
  --runtime RUNTIME --image-archive IMAGE --image-sha256 IMAGE_SHA256 \
  --output NATIVE_RUN
```

Review and repair/document assessment preparation expose the same selector.
Citation correction inherits the original judge choice. Claude corrections bind
a deadline; provider-request counts remain unknown. Codex corrections retain
their existing request-budget contract.

For comparative synthesis:

```sh
uv run skilleval synthesis prepare --spec SELECTION.json --judge claude-opus \
  --output PREPARED
uv run skilleval synthesis run PREPARED --sha256 PREPARATION_SHA256 \
  --runtime RUNTIME --image-archive IMAGE --image-sha256 IMAGE_SHA256 \
  --output ATTEMPT
uv run skilleval results serve ATTEMPT/review
```

Claude uses neither `--catalog` nor `--auth-file`; supplying Codex credentials,
gateway budgets or skill treatments to this route is an error. The judge deadline
remains 15 minutes by default. Tool commands have the existing investigative
policy's 30-second bound and report output truncation explicitly. Static policy
permits reading commands but forbids imported-code execution; it remains an
agent instruction, not an enforced read-only shell.

## Evidence and accounting

Retain the native stream, canonical event projection, final text, command intent
and result ledger, each command's namespace/teardown receipt, immutable source
identity, native launch identity, original guest archive and combined evidence.
The final archive adds host-native evidence without overwriting the guest archive;
a guest member cannot claim a reserved host-native evidence name. Failed runs and
missing telemetry remain distinguishable from completed judgments. Completion
requires the native tool calls to match paired executor records. A recorder failure
revokes further command work; cancellation gives the tool server time to reap its
runtime client before the remaining host process group is stopped.

Usage comes from the single terminal native `modelUsage` snapshot for the selected
model. Raw main-loop usage and client estimates remain diagnostics. Total input
includes uncached input, cache reads and cache creation. No independent provider
request ledger exists on this route; native usage does not establish cash spend.
The current price schedule cannot price cache writes, so a run with cache creation
keeps its conditional estimate unknown. Synthesis overhead remains separate from
task-agent and original-grader consumption.

Deterministic checks cover protocol parsing, source/profile replay, host launch
contracts, gate refusal, MCP transport, artifact collision and cleanup behavior.
They do not establish actual Claude authentication, effective tool confinement,
semantic reliability or cross-provider comparability. Qualify the actual native
client and VM route before relying on it; preserve failed qualification attempts.

The explicit control calls the real model and keeps its evidence in a fresh output:

```sh
uv run python scripts/qualify-claude.py --runtime RUNTIME \
  --image-archive IMAGE --image-sha256 IMAGE_SHA256 --output NEW_CONTROL
```

It checks repeated VM tool use, the solver UID, guest-visible versus host-only
markers, hidden controller paths, ordinary sockets versus denied vsock/egress,
native subscription authentication, stream retention and machine cleanup.

## Primary references

- [CLI reference](https://code.claude.com/docs/en/cli-reference): restricted mode,
  built-in tool selection and explicit MCP configuration.
- [Settings](https://code.claude.com/docs/en/settings-reference) and
  [environment](https://code.claude.com/docs/en/env-vars): invocation controls and
  managed-policy exceptions.
- [Authentication](https://code.claude.com/docs/en/authentication) and
  [model configuration](https://code.claude.com/docs/en/model-config): native login,
  explicit model and effort.
- [SDK result contract](https://code.claude.com/docs/en/agent-sdk/typescript#sdkresultmessage)
  and [cost tracking](https://code.claude.com/docs/en/agent-sdk/cost-tracking):
  cumulative usage surfaces and their limits.
