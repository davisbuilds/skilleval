# Specification to implementation handoffs

The first workflow pilot measures a specification and its usefulness to a fresh
implementer. The selected skill belongs only in the writing stage. The
implementation stage starts with the same original repository baseline, the
original request and one exact selected specification, with no selected skill or
writer conversation. The common pilot brief explicitly requests one self-contained
spec file. If its requirements depend on other generated documents, record that
writer/handoff defect; do not attribute the resulting missing context solely to
the implementer. References to existing baseline files remain available.
This is a bounded two-stage development workflow, not an
interactive conversation engine or scored admission.

## Freeze an explicit artifact

After collecting a native writer attempt, use `schema spec-handoff-spec` to
select its task, collected result and separately retained digests, plus the
repository-relative `spec_path` and exact `spec_sha256`. Selection is explicit:
the preparer does not search for the newest attempt or choose the best document.
The path must be a safe repository-relative, changed, nonempty UTF-8 `.md` or
`.markdown` file. The task brief defines its location; the operational-metrics
pilot requests `docs/specs/`, but the generic handoff does not impose that layout.
Retain every attempt and explain any retry or selection separately.

```sh
uv run skilleval handoff prepare handoff-spec.json --output /path/to/fresh-handoff
uv run skilleval handoff verify /path/to/fresh-handoff --sha256 HANDOFF_SHA256
```

Preparation re-collects the writer's native evidence and checks exact result
identity. The new `input/` contains the original captured baseline, including its
synthetic Git history, plus only the selected spec. Other writer changes, scratch
files, conversations, labels, acceptance maps and operator lineage are excluded.
The sibling `handoff.json` remains operator-only. Readback repeats the lineage and
input checks. Both preparation and readback execute no imported code, models or
VMs.

Artifact availability is separate from completion: a failed or timed-out writer
can leave a usable artifact, and a finished invocation can leave only questions.
An artifact can be handed onward without being semantically accepted; the status
remains `unassessed`. A question-only final answer without a file cannot satisfy
handoff preparation. A file containing only questions can satisfy the byte
contract and still fail specification assessment. Do not turn either condition
into a positive quality verdict.

## Run a fresh vanilla implementer

Use the returned input identity and derived prompt through the existing native
writable-file route. It asks for implementation, repository conventions,
verification, and honest limitations, then provides the original writing brief
as reference context. It does not ask the implementer to write another spec or
promise live answers. The default task budget remains 30 minutes with a generous
1,024-request emergency backstop for new runs; use the same model, effort, native
deadline and request cap recorded for the writer. Existing frozen writer budgets
remain unchanged.

```sh
uv run skilleval handoff check-implementation /path/to/fresh-handoff \
  --sha256 HANDOFF_SHA256 --run /path/to/implementation-run
```

The check binds the exact input and prompt, compatible stage limits, absence of
the selected skill in both native receipt and audit, and completed cleanup.
It verifies lineage, not correctness. Free-form specification prose is preserved
exactly and can reveal writing style or mention its origin; treatment masking is
not a claim of perfect blinding. Grade the implementation against the original
user need and repository contracts, rather than accepting an incorrect spec as a
new answer key.

## Account for both stages

`schema handoff-report-spec` selects the handoff, the writer study/cell, explicit
implementation attempts and explicit judge runs. `handoff report REPORT_SPEC`
replays existing evidence without dispatch. It uses the existing study report to
retain all attempts in the selected writer cell, then verifies the selected
implementation receipts. Preserve failures and incomplete evidence; do not
replace a primary attempt with a later success or infer missing usage as zero.

The report exposes separate writer, implementation and combined agent ledgers,
with judge overhead separately. Pricing follows [ACCOUNTING](ACCOUNTING.md):
without a compatible declared schedule and complete usage, cost remains unknown.
These are descriptive totals over the selected records, not proof of all external
spend, causal benefit or quality-adjusted efficiency. Repetitions of one task and
multiple criteria are not independent task samples.

## Questions and blockers

The initial pilot is noninteractive. The common brief delegates bounded product
choices equally to both writer arms. Agents should use repository evidence,
choose reasonable defaults and state material limitations. A truthful blocker is
retained as such; it is not automatically a false claim, nor a delivered feature.
If both arms expose a missing requirement, revise the task only in a new evidence
generation. Never answer one arm privately or coach it after seeing its result.
The [backlog](../project/BACKLOG.md) owns a future bounded clarification loop.

## Development verification

On 2026-11-08, real Codex 0.164.0 / GPT-6.1 Sol high writer and fresh vanilla
implementer invocations completed a small authored label-formatting fixture
through the native solver boundary. Exact spec selection, original-baseline
handoff, consumer verification and both VM cleanups succeeded. The control used
180-second stage limits and no selected skill. It verifies the handoff machinery,
not representative task quality, skill benefit or calibrated judgments.

Deterministic regressions additionally exercise writer code-change exclusion,
changed evidence/limits/treatment refusal, uppercase Markdown extensions, retry
selection, missing usage, incompatible pricing and native metadata overflow.
Partial overflow receipts remain visible in reports with unverified binding and
unknown usable consumption; they remain ineligible for verified implementation.
