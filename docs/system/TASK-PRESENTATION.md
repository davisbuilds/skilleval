# Task presentation and solver visibility

Give the task agent an ordinary work request and the context needed to do it.
Keep study arms, scores, rewards, hidden checks, reference solutions and evaluation
machinery outside its accessible context. ON adds only the selected skill's native
invocation; OFF receives the shared brief. Do not tell either arm it is under
evaluation, ask it to maximize a score, or instruct it to ignore an evaluator.
Do not add a false claim that it is not being evaluated.

This is separate from [operator-state isolation](NATIVE.md#operator-state-and-effective-context).
A clean home and correct skill discovery do not hide a controller running with
the same guest privileges. It is also separate from masking treatment information
from the semantic judge. The judge necessarily receives assessment instructions;
those instructions must never become solver input.

## Solver boundary

`solver-isolated-v1` keeps the trusted controller and inference relay outside the
native process's filesystem and PID namespaces. A Linux launcher builds an
allowlisted chroot and fresh `/proc`, sets a neutral hostname, then runs Codex as
UID/GID 61000 with no supplementary groups, no capabilities and `no_new_privs`.
An inherited seccomp filter denies AF_VSOCK creation and all io_uring entry
points, closing the guest management transport and its alternate socket-creation
path. It preserves ordinary Unix and local TCP sockets. Only native 64-bit
little-endian aarch64/x86_64 ABIs are supported; other ABIs fail closed.
Codex replaces namespace PID 1. The supervisor waits for its teardown before
collecting files, including when native code starts detached child processes.
Unconfirmed teardown prevents collection and completion; outer VM recovery
remains necessary after a controller interruption.

The solver receives writable task files, fresh home/Codex state and temporary
storage; read-only standard runtime/tool trees and the model catalog; minimal
devices, private PTYs and shared memory; and the loopback inference endpoint.
Selected skill bytes and resources retain their native paths. Controller files,
staging archives, `/opt`, management sockets and the original guest root are not
mounted into this view. Stdin/stdout/stderr are pipes. The native final answer is
written into solver home and copied to controller evidence only after teardown.
The relay's HTTP header uses a neutral service name without modifying the
vendored implementation.

This is an image-specific allowlist, not a general image sanitizer. The exposed
runtime trees must contain only legitimate tools/dependencies; baking evaluation
files into `/usr` would expose them. New images and required task capabilities
need corresponding controls. The profile does not defend against guest kernel
exploits. Keep the VM's separate host boundary and host-only credentials.
io_uring-dependent tools are outside this profile; validate fallback behavior
before using a task that depends on them. This is a narrow transport deny policy,
not a complete syscall allowlist. The qualified inference relay is the only
intended bridge to host services.

VM identifiers use neutral names because the guest kernel exposes them in its
command line and overlay metadata. Earlier identifiers and recovery tickets
remain readable. Neutral naming complements enforced access separation; it does
not replace it. Runtime metadata can still reveal that a Linux VM is in use.

Run the production-route controls without credentials or real model inference:

```sh
uv run python scripts/qualify-solver.py \
  --runtime NATIVE_RUNTIME --image-archive IMAGE_ARCHIVE \
  --image-sha256 IMAGE_SHA256 --catalog MODEL_CATALOG --output FRESH_OUTPUT
```

The synthetic OFF/ON controls compare actual tool outputs and collected files:
source edits/tests/history, skill/resource reads, PTYs/local sockets, privilege
state, controller/path/proc/descriptor denials, a known-readable sentinel and a
detached heartbeat. A same-run root Ping demonstrates a working management
listener while native tools must fail to create AF_VSOCK sockets. Local TCP,
Unix socket pairs and PTYs must still work. They retain native requests, original
failures and cleanup.
`boundary_passed` is separate from `permissions_consistent`; a mismatch yields
`partial`, not full qualification. Neither field measures skill benefit.

On **2026-10-30**, the aarch64 smolvm 1.32.3 / Codex 0.164.0 route passed
78 of 80 checks across fresh OFF/ON guests: access boundaries, ordinary tools,
matched context and inspected system metadata passed. The two failed checks
were the network-permission message, one per arm. The same-run root controls
received management Pongs while native socket creation returned `EPERM`.
An earlier unfiltered native control received those Pongs as UID 61000; its
evidence remains retained and is not qualified by the fix.

A separate native deadline control observed a running detached heartbeat,
terminated namespace PID 1, and verified the heartbeat stopped before collection.
The 56 operator-context checks passed again on the new route. Public task-package
validation also matched two lanes through the same profile, including three
ordinary tests and fresh homes. All nine guests in these final controls cleaned
up with unchanged execution identity. No credential or model inference was used.
These are bounded controls, not representative task coverage or an x86_64 runtime
qualification. Exact source/image/runtime receipts remain required for reuse.

## Native permission-message limitation

The current Codex CLI profile announces network access as enabled while the
enclosing VM disables general egress. Local TCP/Unix sockets remain useful for
normal task tools. Codex 0.164.0's
[external-sandbox policy is programmatic-only](https://github.com/openai/codex/blob/6aaac694803004a63c6f393d153c96d22521a6b9/codex-rs/config/src/config_requirements.rs#L1474).
Its managed sandbox requires additional namespaces; its deprecated legacy network
seccomp route also denies local connections. Do not change those task capabilities
just to alter wording, or cover a conflicting permission message with prompt prose.
A native adapter that can declare external enforcement needs separate protocol,
tool and accounting qualification. This mismatch remains visible in the control
report; it is not itself an evaluation announcement.

## Earlier audit

Source and native-route inspection on **2026-10-30** found the following. The
native probe used Codex 0.164.0 and smolvm 1.32.3 with synthetic Responses through
the production runner. It used no credential or model inference. Original
requests, tool output, native audit and cleanup evidence were retained privately.

| Surface | Observation and disposition |
| --- | --- |
| Shared prompt and ON/OFF rendering | `study._invocations` passes the shared brief unchanged to OFF and adds only `Use $NAME for this task.` to explicit ON. The native runner adds no evaluation preamble. Caller-supplied prose still needs inspection. |
| Repair and document example briefs | Both said the evaluator collects file bytes; the repair also mentioned a claimed score. Removed those disclosures from the templates. Existing frozen inputs and results retain their original bytes. |
| Native provider configuration | The readable profile named `qualification-broker` / `skilleval qualification`. New profiles use `local-gateway` / `Local gateway`, retaining the custom provider's protocol behavior. |
| Skill notice packaging | New captures place the supplied root notice at `.source-notices/ROOT_LICENSE`. Old `.skilleval-notices/ROOT_LICENSE` captures remain loadable without mutation; neutral presentation requires a new capture. Upstream skill files and license text remain unchanged. |
| First native request | Inspected the native instructions, tool catalog, skill catalog, task instructions and a normal brief on the patched route. No evaluation announcement was found in that request. Matches for “evaluates” in the JavaScript tool description described executing code. This is one inspected input, not a general absence detector. |
| Guest files and processes | The earlier root route exposed the controller, context guard, settings, tooling archive and `/opt/skilleval/openbench_gateway.py`, plus the controller command line through `/proc`. The new profile enforces a separate view; old attempts keep their original exposure. |
| Declared versus effective capabilities | The network permission-message mismatch remains as described above. |
| Historical review exporter | **Still exposes preparation context.** It preserves `.openbench-review.json` and constructs Git commits authored by `Captured review`. This is a development importer, not a neutral presentation route. Do not rewrite its pinned history or silently remove files. |
| Task snapshots and skill trees | The host capture manifest is excluded from native task transfer. Payload files, ordinary guidance, Git objects and the selected skill tree are copied as supplied; there is no general semantic filter for evaluation cues. A source-only snapshot is not automatically a suitable solver view. |
| Qualification and judge fixtures | Synthetic context controls and the legacy calibration demo intentionally disclose their control purpose. Judge packets intentionally contain rubrics. Neither is a representative solver task. |

The probe successfully read the intended task source and the control files, then
cleaned up its VM and runtime home without identity drift. It demonstrates the
access gap, not that a real model noticed or acted on it. That root route was
not qualified to conceal evaluation infrastructure. The earlier 56 context
controls concern operator-state exclusion and matched skill exposure; they do not
establish this property. Neutral wording alone does not close the gap.

## Acceptance before new comparisons

Keep the controller, evaluator sources, staging archives, study records and grader
artifacts outside the solver's readable namespace. Use a dedicated unprivileged
solver identity and an enforced filesystem/process boundary, or place the
controller outside the solver container. A separate directory, a permission mode
while both processes are root, or a prompt asking the agent not to look is
insufficient. Preserve the inference transport and ordinary task capabilities;
renaming an evaluation path does not establish separation.

Verify on the actual native route, including subprocesses and indirect reads:

- The solver can read task source, ordinary instructions, required history,
  selected skill resources and dependency/tool files, and can edit/test the task.
- It cannot read controller/evaluator files, original staging archives, hidden
  labels or alternate solutions through direct paths, symlinks, `/proc`, process
  arguments, inherited descriptors, mounts or image layers accessible in the guest.
- Both arms receive the same ordinary context and capabilities. Record UID,
  effective permissions, image/configuration identity and actual native requests.
- Known-positive sentinels show the detector sees an intentionally exposed file;
  access refusals must occur while the allowed work still succeeds. Keep model
  self-reports separate from tool-access evidence.

Review each task's brief, filenames, ordinary guidance, full accessible Git
history, fixtures, dependency metadata, skill resources and expected tool errors
before capture. Keep the grader, its tests and historical repair outside the
solver view. Existing project tests, genuine product requirements and an upstream
skill's own examples remain legitimate context; do not remove every occurrence
of “test”, “evaluation” or “benchmark”. If faithful upstream material itself
discloses evaluation, record the limitation or select another task/skill instead
of silently editing the treatment.

No setup can prove what the model infers about its situation. The acceptance
claim is narrower: inspected task presentation contains no evaluation disclosure,
and tested access paths cannot reach evaluation-only context. Record remaining
inference cues such as offline tools or constrained interaction; do not call them
proof of awareness or its absence. Changed profiles and packages need new evidence
generations. Existing attempts are not retroactively blinded.
