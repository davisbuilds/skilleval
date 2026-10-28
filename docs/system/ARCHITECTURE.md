# Architecture

The Python package separates typed experimental records, captured
evidence, runtime qualification, harness observations, and separate assessments.
The CLI coordinates them through explicit files. It has no database, persistent
daemon, or dependency on another local repository. An explicit bounded gateway
can broker native Codex inference; ordinary imports and checks do not start it
or read credentials.

```mermaid
flowchart LR
  S[Frozen source export] --> E[Evidence bundle + digest receipt]
  E --> T[Task source only]
  T --> R[Qualified runtime + harness]
  R --> O[Attempt evidence]
  E --> P[Review packet]
  O --> P
  P --> A[Executable checks + semantic assessment]
  A --> H[Human adjudication when needed]
  H --> J[Separate versioned assessments]
  J --> C[Paired analysis]
```

This diagram is the target scored flow. Prepared-image task/Git controls,
synthetic and authenticated native tool controls, packets, assessments, and
authored-label comparisons execute today. An externally invoked development
judge has assessed the authored controls. Bounded environment admission is
implemented; independent judge calibration and paired scored dispatch/analysis
remain incomplete.

## Records

`contracts.py` exposes provisional version-1 JSON schemas. Unknown fields are
rejected. Task identifies evidence and development/holdout population. Experiment
declares tasks, harness/model/effort, variants, repetitions, order seed, timeout,
and primary outcomes. A variant distinguishes absent, available, and forced skill
use and pins skill content when present. These are starting contracts, not a
complete admissible campaign specification.

Attempt separates execution status, evidence identity, observed consultation,
origin, and reported usage. Missing usage is null. Assessment names its subject,
evidence digest, rubric, assessor, model when applicable, per-finding judgments,
limitations, and optional superseded record. Structural validation does not
establish the truth of any of these claims or enforce relationships across a full
campaign; orchestration must add those checks.

## Evidence

Capture produces a fresh directory containing payload and `manifest.json`. Each
manifest entry records relative path, SHA-256, byte count, and executable flag.
Verification rejects extra/missing files, changed bytes/modes, duplicate paths,
unsafe paths, links, and nonregular files. JSON controls reject duplicate keys and
nonfinite numbers. Reads and copies are bounded.

Publication uses an atomic no-replace rename. Captured files are made read-only;
the owner can still modify them. Keep the manifest digest outside the bundle to
detect replacement of both manifest and payload. This is integrity checking, not
a signature, provenance proof, multi-file source snapshot, or storage durability
guarantee across power failure. Freeze the source before importing.

Historical imports preserve selected case files and reports plus the original
full report document under `provenance/`. They explicitly record development
population, prior exposure, and unverified earlier custody. The whole bundle can
contain private material. Only `case/source` is eligible for solver transfer;
oracle, reports, and provenance are grading inputs outside that filesystem.

Private records use fresh destinations and restrictive file permissions. These
are local workflow protections, not isolation from the credential-owning user or
another process with the same authority. No scrubber makes arbitrary source or
traces publishable. Release artifacts require an explicit visibility review.

## Native review reconstruction

`workspace.py` exports only captured task source into a fresh writable directory,
requiring the separately retained manifest digest. For the supported schema-1
review metadata and full-identity UTF-8 patch format, native Git plumbing checks
candidate blobs/modes, reverse-applies the patch, verifies base identities, and
round-trips to the exact candidate tree. Synthetic `base` and `candidate` commits
provide a meaningful native diff; original history is unavailable.

Captured ordinary instructions, tests, ignored files, bytes, and executable modes
are retained. Oracle, reports, and provenance do not enter the workspace. Git
configuration is isolated, and plumbing avoids checkout, hooks, attributes,
filters, and task-program execution. Unsupported paths, patch forms, mode types,
and case aliases fail closed. Offline exact-diff reconstruction has been exercised
on a captured real review. The same review also passed native guest Git checks
and its repository tests on the selected dependency image.

## Runtime and harness

`qualification.py` makes a plan from a verified imported source bundle, pinned
registry OCI digest or local Docker-save archive SHA-256, resources, and explicit
guest command argv. `run` recomputes the plan and rejects drift before launching
anything. It uses a fresh machine identity, isolated HOME/XDG/Docker directories,
an allowlisted process environment, no host
shell, bounded logs, timeouts, and stop/delete cleanup receipts. The runtime HOME
uses a short private temporary path because macOS socket paths include HOME-based
cache directories even when XDG directories are short. It transfers a hash-checked
archive of source files only. Nothing in the imported tree is executed directly
on the host.

The candidate adapter is pinned to the inspected smolvm 1.32.3 CLI. Executable
registry plans are restricted to explicit `docker.io/` references: this version's
pack resolver skips Docker Hub, while other registry references can automatically
import packed-machine policy, including network settings. Ordinary OCI pulls may
contact a public registry from the host; that is distinct from guest network
policy. The offline adapter requests no guest network, mounts, or secrets. The separate
native control mounts only the declared inference socket. The separate
[environment admission workflow](ENVIRONMENT.md) requires evidence for the exact
selected route, including adversarial lifecycle controls.

The adapter follows the pinned upstream
[machine CLI](https://github.com/smol-machines/smolvm/blob/v1.32.3/src/cli/machine.rs),
[pack resolver](https://github.com/smol-machines/smolvm/blob/v1.32.3/src/data/pack_ref.rs),
and [VM lifecycle](https://github.com/smol-machines/smolvm/blob/v1.32.3/src/cli/vm_common.rs).
Reinspect and requalify before accepting another runtime version or image route.
Colima remains an alternative to evaluate, not an implemented fallback.

`offline_passed` records successful explicit offline controls and cleanup.
`partial` covers failures, incomplete cleanup, or a mismatched runtime. Every
report says `admitted: false`. No installed binary, source-level restriction, or
successful subprocess fixture establishes host isolation or scored-run admission.
Live prepared-image execution passed the captured task's tests, native Git
checks, and cleanup. These observations do not grant scored admission. The local
Docker-save route verifies archive identity and bounded
structure without host extraction; `run` rechecks the actual copy supplied to
smolvm. With `--reconstruct-git`, preparation binds the synthetic workspace identity
and transfers source plus its Git objects. Guest checks verify expected commits,
trees, clean worktree, and object integrity. Live controls also compared the
native full-index diff with the captured patch. These observations apply to the
tested inputs. Runtime state is retained for diagnosis/recovery; the CLI does not
automatically retry or resume it.

`harnesses/codex.py` parses captured `codex exec --json` events based on the
[noninteractive event interface](https://learn.chatgpt.com/docs/non-interactive-mode).
It preserves raw/unknown events, per-turn usage observations, item outcomes, and
parse errors. It does not invoke Codex. Turn completion is not task success; failed
tool commands can occur in otherwise completed turns. Malformed evidence makes
stream status unknown. The native workload runner retains these observations
alongside process exit/timeout, broker receipts and cleanup. Usage remains a
reported observation; the parser does not estimate missing tokens or cost.
The runner's separate [accounting layer](ACCOUNTING.md) normalizes one complete
cumulative terminal snapshot, cross-checks broker core totals and records timing
scopes. Study reports apply an optional frozen price scenario and retain unknowns;
they do not infer actual charges or assessed task quality.

Harness compatibility is separate from run identity. Stable Codex patch and
minor releases at or after the inspected baseline within the same major version
may enter qualification automatically. The actual `codex --version` output is
parsed strictly and recorded alongside the baseline and compatibility policy.
Unknown, older, prerelease, and new-major versions require adapter work. Passing
this version gate does not establish protocol compatibility: the native request,
tool-result loop, events, and cleanup must still pass for the exact binary/image.
The model catalog is an independently identified input. Future matched ON/OFF
runs use the same exact harness version; updates start a new qualified execution
identity, preserving earlier receipts and making cross-version analysis explicit.

`gateway.py` is a separate explicit host capability. Its typed wrapper uses a
pinned OpenBench gateway to connect a credential-free guest loopback relay over
one mounted Unix socket to a host broker. The host selects an existing ChatGPT
login; no auth cache or provider key enters the guest. The fixed upstream,
model/effort/profile, request ceilings, receipts, expiry checks, and cleanup are
owned by [GATEWAY.md](GATEWAY.md). This capability does not make the event parser
an execution orchestrator.

`scripts/qualify-native.py` explicitly exercises that route in a fresh VM. Its
default synthetic responder drives the real native code-mode request and
subsequent tool-result request, checks an independent tool effect, and tests
host-file and network denial with working positive controls. Same-environment
machine inventory establishes the known-present control and observes absence
after stop/delete. These synthetic native controls have passed. They establish
the tested protocol and effects, not authenticated provider acceptance.

Explicit selection of an existing host auth file enables the separate bounded
provider control. The complete authenticated native tool loop passed with two
real provider requests, command/file effects, host/network controls, and observed
cleanup. The host validator has one recorded compatibility patch for empty
assistant-text replay metadata; native request bytes remain unchanged. Neither
mode grants scored admission. The lifecycle probe exercises timeouts, detached
guest work, fresh native workspace/session paths, and recovery after SIGTERM or
SIGKILL of a controller. Identity-capturing offline and native routes share its
durable recovery implementation. Admission validates the required receipts and
retained artifacts, then binds them to an explicit snapshot checked with an
externally retained digest. Changes invalidate that admission. Exact invocation
and cleanup receipts remain with private evidence; [ENVIRONMENT.md](ENVIRONMENT.md)
owns the recovery procedure and limits.

## Native development workloads and treatments

`runner.py` coordinates a bounded native review or judge in a fresh VM. Its input
is an explicitly frozen generic snapshot containing only the intended task or
judge packet. The manifest stays on the host; the guest receives payload files.
Imported source and native Codex execute in the VM. The host brokers inference
using an explicitly selected existing login; no host auth cache enters the guest.
The [native workflow](NATIVE.md) owns preparation, invocation and recording.

The host retains the exact prompt, native model/effort and binary identity,
process/event/provider observations, artifact inventory and cleanup evidence.
It rechecks original input and compares original paths with the collected guest
copy. The host snapshot remains canonical even when the guest modifies its copy.
Collected archives are bounded and inspected without host extraction or source
execution. Guest files, process reports and transcripts remain guest-authored;
hashes establish identity and consistency, not semantic authenticity. Workload
completion and successful cleanup do not imply a correct review. Every workload
remains development-only with scored admission false.

`treatments.py` freezes one selected skill with explicit source URL/revision and
an optional supplied root notice. Installation targets only a fresh guest
`CODEX_HOME/skills`, preserving tree bytes and executable flags. Ordinary task
instructions remain; embedded skill and Codex configuration paths are rejected
by the baseline check. Matched preparation keeps task and prompt bytes equal and
varies skill availability separately. That helper does not request invocation;
the [study controller](STUDIES.md) separately freezes the explicit-use prefix for
new ON arms and preserves natural-availability semantics for legacy plans.

Availability alone does not establish native discovery. Conservative detectors
record selected skill metadata or complete body in actual decoded broker requests,
and complete pinned content in successful native read output. These observations
separate catalog exposure from body exposure. A missing match stays unmeasured;
an observed body does not establish semantic application or outcome quality.
Every request also retains bounded, value-free protocol shape and input kind/role
counts. Selected-skill component matches are counted separately by request
surface. Unknown values and omitted samples remain explicit; arbitrary keys and
raw request content are not retained. Components found in different strings do
not establish a complete catalog/body match.

The [study controller](STUDIES.md) adds frozen development pairs, bounded retries
and an explicit first-attempt reporting policy around the native runner. Neither
layer grants scored admission. [Reliability controls](RELIABILITY.md) compare
judge stability separately from authored-label agreement and independent audit.

## Assessment

`grading.py` creates fresh packets, defaulting to no oracle or provenance. Only
selected report fields are copied; report content can still reveal identity or
contain untrusted instructions. The rubric requires source-grounded reasoning,
permits novel defects, rejects lexical reference matching, and allows unresolved
judgments. These instructions alone do not demonstrate prompt-injection resistance.

Recording verifies evidence identity, selected report validity, complete finding
index coverage, citation paths/spans, and same-subject/evidence supersession.
Supported and unsupported findings require citations. Valid report content from a
failed execution can be assessed; invalid/missing reports cannot become clean
reviews. Records append exclusively under unique assessment IDs. Revised records
do not change the source or overwrite prior judgments.

`calibration.py` adds a separate public authored development suite with source,
reports, semantic questions, expected labels, rationales, and citations. Packet
export withholds labels and structured history; the result pins both the captured
suite digest and exact exported packet/rubric digest. Append-only results may be
partial. Comparison keeps each judge run separate and reports full confusion plus
expected, supplied, missing, invalid, and unresolved denominators. A missing
result is unassessed, never a clean review or an invented zero.

The [calibration reference](CALIBRATION.md) owns the semantic classes and procedure.
Targeted known-defect questions include positive and negative controls; they do
not measure unaided discovery or exhaustive recall. The implementation validates
structure and evidence links and compares supplied verdicts with authored labels.
The offline implementation does not call an LLM, establish independent label
truth, verify blinding, select assessment revisions, or aggregate campaign scores. The observed
development judge was manually invoked through an external agent harness outside
the guest runtime; its output was then recorded and compared through these offline
APIs. It agreed
with all twelve authored labels, including one unresolved judgment. That is a
single development observation, not general reliability or independent human
label validation. The roadmap owns its dated scope. Native-route calibration,
declared analysis policies, and human adjudication remain necessary before
scored comparisons.

`judge.py` adds versioned static and investigative native packets for calibration
and newly produced review reports. It withholds authored labels and structured
treatment/history fields while preserving source and report evidence. This is
label withholding, not independent blinding. The investigative rubric lets the
judge choose bounded VM reproductions on writable source clones; the static
rubric forbids execution as a prompt policy while native tools remain available.
The runtime and overall deadline provide the execution boundary; the prompt does
not provide separate command-level enforcement.

Native recording binds the original snapshot, exact exported packet, prompt,
model, declared investigation policy, final text and artifact manifest. Resolved
judgments require original case citations; retained reproduction artifacts can
supplement them through a separate citation field. Review recording requires
every reported finding exactly once. Calibration may remain partial and retains
explicit missing/unresolved opportunities and authored-label confusion counts.
Failed reviewed executions and failed judge executions stay separate from
finding judgments; missing or malformed reports never become empty clean reviews.

Each native result gets a fresh append-only directory containing the packet,
native evidence and an audit record marked `required-pending`. Native model output
cannot attribute itself to a human assessor. Independent human review is still
required before a scored correctness claim; storage validation and authored-label
agreement cannot supply it. [CALIBRATION.md](CALIBRATION.md) owns the verdict and
result semantics.
