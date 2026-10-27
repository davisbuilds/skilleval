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
| 1A — Environment | Prepared task image, fresh VM lifecycle, Codex adapter, explicit admission | Live controls on the exact host/runtime/image/tool route prove fidelity, isolation, bounded failure, cleanup, and a native model/tool loop | Prepared task/Git controls and authenticated native tool loop passed; adversarial lifecycle and explicit admission remain |
| 1B — Grader | Evidence packets, executable outcomes, semantic rubric, revision history, calibration | Frozen positive/negative/ambiguous examples distinguish accurate findings from plausible mistakes; human audit measures judge errors | Authored controls and one development LLM judgment run completed; independent label audit and judge reliability checks pending |
| 2 — First paired pilot | One real task in vanilla and skill configurations, separate assessment and report | Reproducible paired artifacts; treatment fidelity and invalid attempts visible; qualified environment and grader | Pending 1A and 1B |
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

## Next: qualify the environment and grader

For 1A, finish adversarial lifecycle controls and the explicit admission authority
on the prepared image, then exercise the longer real-task route. The adapter
accepts pinned local Docker-save archives and verifies reconstructed guest Git
identity; live task, Git, and bounded provider controls passed. CLI help owns
available options. Preserve the captured source and meaningful
review diff, and check the task's supported platform. Retain Colima as an
alternative if the selected execution route cannot satisfy the contract.

Use known-positive controls for host filesystem reachability, allowed and denied
network destinations, credential delivery, stale workspace reuse, termination,
and cleanup. Bind admission evidence to image, archive when applicable, adapter,
runtime, harness, catalog, and relevant configuration identities; changed inputs
invalidate admission. Keep provider access separate from task egress. A stopped
VM or generated deny setting is insufficient.

For 1B, execute the declared native judge route against the frozen authored set
and retain actual instructions, packet digest, model/effort, and results.
Independently audit labels and disagreements. Assess positional bias, treatment
leakage, reference anchoring, prompt injection, and repeated-judge consistency.
Targeted coverage probes supply known hypotheses; add evidence for unaided review
coverage before claiming exhaustive recall. Executable outcomes complement
judgment rather than collapse into an opaque score.

These tracks can progress independently. The first paired pilot starts only when
both meet their acceptance criteria. Its purpose is to validate the whole route
and estimate variance, not to decide that a skill works.

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
