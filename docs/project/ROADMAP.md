---
status: in-progress
updated: 2026-10-27
---

# Roadmap

Build a trustworthy instrument, then use it to study skills. Gates below describe
observable outcomes; completion of a phase does not establish skill effectiveness.
Public GitHub hosting is the intended destination. The
[source register](SOURCES.md) owns research lineage and reuse candidates;
[publication work](BACKLOG.md#open) remains explicit before the first public push.

| Phase | Deliverable | Exit evidence | Current state |
| --- | --- | --- | --- |
| 0 — Foundation | Python package, provisional records, copied evidence, CLI, project boundaries | Offline workflow passes; a historical case can be imported without changing its source | Implemented |
| 1A — Environment | Prepared task image, fresh VM lifecycle, Codex adapter, explicit admission | Live controls on the exact host/runtime/image/tool route prove fidelity, isolation, bounded failure, cleanup, and a native model/tool loop | Bounded controls, admission, native runner and development dispatch implemented; scored scope pending |
| 1B — Grader | Evidence packets, executable outcomes, semantic rubric, revision history, calibration | Frozen positive/negative/ambiguous examples distinguish accurate findings from plausible mistakes; human audit measures judge errors | Native judges, authored controls and separate reliability workflow implemented; independent audit and representative reliability evidence pending |
| 2 — First paired pilot | One real task in vanilla and skill configurations, separate assessment and report | Reproducible paired artifacts; treatment fidelity and invalid attempts visible; qualified environment and grader | Development pair and frozen study controller implemented; scored pilot gated on 1A and 1B |
| 3 — Representative study | Declared task population, protected holdouts, repeated matched trials, uncertainty analysis | Enough independent task evidence for the prespecified decision; cost and failure effects reported | Pending pilot |
| 4 — Expand usefully | Repair/implementation coverage, pi adapter, TypeScript review UI | Each addition meets the same contracts and addresses a demonstrated workflow need | Deferred |

## Current environment and grader slice

Evidence capture, historical imports, review packets, append-only assessments,
Codex event parsing, and candidate offline smolvm controls are implemented.
Source-only workspace export now reconstructs synthetic base/candidate Git
history from captured full-identity text patches. It has reproduced a real
captured review diff offline without executing imported code on the host.

Live smolvm execution passed the captured real task's repository tests using a
hash-bound image built from its locked dependencies. Native guest Git confirmed
the reconstructed commits, clean source tree, object integrity, and byte-identical
review diff; stop/delete cleanup completed. The adapter uses a short isolated
runtime HOME for macOS Unix sockets. The OpenBench-derived gateway and host-only
authentication wrapper have deterministic controls. Both synthetic and real
provider controls passed with native Codex code mode, host-file/network denial
controls, and same-state inventory verification of cleanup. The authenticated
control completed two real provider requests. A narrow gateway adaptation accepts
empty assistant replay metadata; failed diagnostic attempts are retained.
No runtime is admitted for scored campaigns.
Detailed private task provenance and run logs remain outside tracked source.

The lifecycle slice added durable recovery tickets shared by the task and native
controllers, full runtime/source/configuration fingerprints, and explicit
environment admission. Fresh live controls passed on 2026-10-27: task tests and
Git fidelity, synthetic and authenticated native loops, execution timeout,
fresh native workspace/session state, and SIGTERM/SIGKILL controller recovery.
The guest timeout stopped the detached heartbeat in this control; killing the
host controller left guest work alive until explicit VM recovery. Same-state
inventory and a live host observer confirmed cleanup, and repeated recovery
succeeded. Fault-injected local tests additionally cover failed stop/delete and
interrupted temporary-directory removal. These are bounded control results, not
general crash-safety, longer-workload compatibility, or skill-effectiveness claims.

The public authored calibration set covers paraphrases, novel true findings,
duplicates, false positives, missed/represented/absent defects, invalid and clean
reports, and insufficient evidence. Its packet, result, and comparison workflow
preserves missing and unresolved coverage. Structural checks and authored-label
controls do not establish semantic judge accuracy. On 2026-10-27, a separate
GPT-6.1 Sol/high collaboration agent assessed the label-free packet: all twelve
judgments agreed with authored labels, including one unresolved judgment. This
was one development run outside the guest runtime, with no independent human
audit. It exposed a citation-contract mismatch that was corrected without
editing the model result or packet. No scored campaign has run.

The native development route now runs complete reviews and prepares separate
static or investigative judge sessions. Judge packets preserve captured source
bytes and executable flags, withhold labels and structured treatment identity,
and retain reproduction artifacts separately from original citations. Recording
binds the exact prompt, packet, model, policy and collected final output; semantic
accuracy still needs audit. The [native workflow](../system/NATIVE.md) owns the
invocation and evidence contracts.

On 2026-10-27, a first development pair completed on a captured real review task
with identical task, prompt, model, harness and budget inputs. The selected skill
was naturally available in one arm, and a successful complete skill read was
observed there. Both reviewers reported the same defect. The executions overlapped,
so no performance comparison is made. A request-content detector failed to match
the skill despite the observed read; its exposure result remains unmeasured.
This is end-to-end development evidence, not an admitted trial or an estimate of
incremental skill benefit. The failed setup attempt and both completed runs are
retained privately.

The same day, fresh native static and investigative calibration sessions each
agreed with all twelve authored labels: eleven resolved judgments, one unresolved
judgment and no missing probes. Both completed recording and VM cleanup. The
investigative judge chose source inspection alone for these small cases; no
reproduction advantage was established. These observations exercise the native
judgment/recording path, while independent label audit and reliability controls
remain open.

Fresh investigative judges then assessed each real report separately. Both
traced repository contracts and ran bounded reproductions against unchanged
cloned source inside their VMs, retaining scripts, outputs and citations. Both
judged the reported finding supported, while identifying an existing test that
encoded the faulty behavior. Original packet files remained unchanged. Final
environment fidelity, native transport, lifecycle and bounded admission controls
passed on the resulting source revision. None of these results replaces the
pending human audit or admits a scored campaign.

## Next: qualify the grader and scored dispatch

The [development study controller](../system/STUDIES.md) now supplies the frozen
matrix, sequential randomized pairing, explicit budgets, append-only attempts,
local ownership lock, recovery reconciliation and first-attempt reporting policy.
It checks any supplied bounded seal at each dispatch without promoting its scope.
The [separate reliability workflow](../system/RELIABILITY.md) preserves the pending
audit generation while adding repeat, order and perturbation controls. These
implemented workflows leave independent semantic audit and scored admission open.

Development validation on 2026-10-27 completed three fresh investigative judge
sessions, each resolving all fourteen authored probes with matching labels.
Repeated and reversed-probe-order judgments were stable on all fourteen probes;
the twelve within-packet perturbation pairs were stable in each session. A native
`task/` citation-prefix mismatch initially blocked one recording. After verifying
collected originals byte-for-byte, a narrow recorder fix accepted the unchanged
result in a new recording generation; the original failure remains retained.
These small, related controls do not establish independent correctness or general
injection resistance.

The controller also completed a sequential real-task development pair with
first-attempt evidence for both arms. The available arm read the pinned skill,
and its complete body was observed in fourteen of fifteen broker requests.
Catalog presentation remains unmeasured by the stricter metadata detector, and
the earlier unmatched request trace remains unresolved. Task quality stays
unassessed; one pair supplies no dependable effectiveness or efficiency estimate.
Offline task fidelity, authenticated native execution, lifecycle recovery and
bounded admission were renewed on the resulting source revision. All temporary
VM state and per-run image copies were removed; the pending audit stayed frozen.

For 1A, retain longer real-task evidence and consume checked environment
admission at the eventual scored-dispatch boundary. The adapter
accepts pinned local Docker-save archives and verifies reconstructed guest Git
identity; live task, Git, and bounded provider controls passed. CLI help owns
available options. Preserve the captured source and meaningful
review diff, and check the task's supported platform. Retain Colima as an
alternative if the selected execution route cannot satisfy the contract.

The implemented admission seal requires known-positive filesystem/network,
native tool, fresh-state, termination, and cleanup controls. It binds the task,
image, runtime distribution, host, harness, catalog, gateway, configuration, and
control-source identities; changed inputs invalidate admission. Extend those
controls when the workload or authority changes. Keep provider access separate
from task egress. A stopped VM or generated deny setting is insufficient.

For 1B, compare static and investigative native judgments against the frozen
authored set, retaining exact prompts, packet identity, model/effort, tool traces
and reproduction artifacts. The native judge and append-only recorder are
implemented; independent human audit remains pending. Audit labels and
disagreements before scored use. Assess positional bias, treatment
leakage, reference anchoring, prompt injection, and repeated-judge consistency.
Targeted coverage probes supply known hypotheses; add evidence for unaided review
coverage before claiming exhaustive recall. Executable outcomes complement
judgment rather than collapse into an opaque score.

These tracks can progress independently. Development pairs can exercise packaging,
natural skill discovery and the complete review-to-judge path while the gates
remain open. They cannot be reported as admitted scored trials. The scored pilot
starts only when both tracks meet their acceptance criteria; its purpose is to
validate the complete route and estimate variance, not decide that a skill works.

## Then: make the comparison informative

Freeze task selection, skill bytes, model/harness configuration, budgets, primary
outcomes, execution-validity rules, and retry policy before dispatch. Randomize
and interleave matched runs; retain every attempt. Compare skill-absent and
skill-available behavior first. Forced consultation is a distinct diagnostic
treatment, not a replacement for natural skill use. Verify exposure and observed
consultation without counting a file read as successful use.

Report task quality, execution reliability, consultation, latency, token usage,
and cost separately. Do not treat null usage as zero or incomplete runs as clean
reviews. Review quality needs supported findings, false positives, duplicate
handling, and coverage of known opportunities, with space for novel defects.
Freeze assessment-selection rules before analysis, including how revisions and
disagreement affect aggregates.

Choose repeats and sample size from pilot variance and the decision we want to
make; do not set an arbitrary run quota. Estimate paired effects with uncertainty
that respects task/repository clustering. Report inconclusive results as such.
Only after this workflow is useful should pi, broader tasks, or a UI expand it.
