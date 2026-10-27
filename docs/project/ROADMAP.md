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
| 1A — Environment | Prepared OCI task image, fresh VM lifecycle, Codex adapter, explicit admission | Live controls on the exact host/runtime/image/tool route prove fidelity, isolation, bounded failure, cleanup, and a native model/tool loop | Candidate offline adapter only; no admission |
| 1B — Grader | Evidence packets, executable outcomes, semantic rubric, revision history, calibration | Frozen positive/negative/ambiguous examples distinguish accurate findings from plausible mistakes; human audit measures judge errors | Packet and assessment storage implemented; calibration pending |
| 2 — First paired pilot | One real task in vanilla and skill configurations, separate assessment and report | Reproducible paired artifacts; treatment fidelity and invalid attempts visible; qualified environment and grader | Pending 1A and 1B |
| 3 — Representative study | Declared task population, protected holdouts, repeated matched trials, uncertainty analysis | Enough independent task evidence for the prespecified decision; cost and failure effects reported | Pending pilot |
| 4 — Expand usefully | Repair/implementation coverage, pi adapter, TypeScript review UI | Each addition meets the same contracts and addresses a demonstrated workflow need | Deferred |

## Immediate slice

Implemented: package and local checks; schema/validation commands; copied evidence
with digest receipts and strict inventories; one-case historical review import;
review packet and append-only assessment commands; a Codex JSONL parser; and a
version-pinned candidate offline smolvm adapter. A private historical case was
imported and prepared locally as development evidence. Private data is not part of
this repository.

Runtime tests exercise real subprocess fixtures, not real VMs. Grader tests
exercise structural contracts, not semantic judge accuracy. No model was invoked
and no scored campaign was run in this slice.

## Next: qualify the environment and grader

For 1A, build a reproducible image with preinstalled task dependencies. Preserve
real Git base/candidate history, ownership, modes, tool versions, and working
directory semantics. Qualify smolvm on the intended host; retain Colima as an
alternative if the actual route cannot satisfy the contract. A stopped VM or a
generated deny setting is insufficient: use known-positive controls for host
filesystem reachability, allowed and denied network destinations, credential
delivery, stale workspace reuse, termination, and cleanup. Bind the receipt to
image, adapter, runtime, harness, and relevant configuration identities; changed
inputs invalidate admission. Keep provider access separate from task egress.

For 1B, use historical review material to build calibration cases that include
paraphrased correct findings, novel correct findings, duplicates, false positives,
missed known defects, invalid reports, and uncertainty. Ground labels with
reproductions or source evidence and independently audit a sample. Add an actual
LLM judge only after its input, output, model configuration, and privacy route are
defined. Preserve disagreements and assess positional bias, treatment leakage,
reference anchoring, and repeated-judge consistency. Executable outcome evidence
should complement judgment rather than be collapsed into a single opaque score.

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
