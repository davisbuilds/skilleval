# Agent-assisted synthesis

Synthesis is a separate comparative investigation over retained task evidence.
The agent reads the original request, delivered artifacts, selected judgments and
contrary evidence, then explains quality and consumption tradeoffs and identifies
material residual questions. It can disagree with an earlier interpretation by
citing new evidence. Original verdicts remain unchanged.

The first adapter covers specification-to-implementation handoffs. It reuses
existing handoff accounting and artifact-assessment replay, the native Codex
judge route, and the [results view](RESULTS.md). This is an explicit operator
workflow; serving a page or finishing an experiment does not launch synthesis.

## Select evidence

`skilleval schema synthesis-selection` owns the selection contract. Select:

- The original request and hash-pinned UTF-8 sources: artifacts, relevant source
  context, validation, observations, controls and traces. Each has an ID, label
  and role. Full selected files remain searchable; they are not reduced to the
  excerpts in a previous synthesis.
- Per-arm handoff-report specifications and selected immutable assessment
  records. Preparation replays their existing validators and binds assessments
  to the selected writer or implementation; a valid unrelated judgment is not
  enough. These records supply execution status and canonical consumption.
- The selection policy, known omissions and limitations. This first version
  uses explicit source selection; it does not automatically discover everything
  relevant in a repository or transcript archive.
- An optional frozen price schedule for the synthesis invocation. Its usage,
  time and conditional API-equivalent estimate remain separate from task agents
  and original graders. No schedule or incomplete usage means unknown cost.

The [selection template](../../examples/results/synthesis/selection.template.json)
shows the structure; replace its paths and digests with actual retained inputs.
Use original evidence as the primary material. Label prior synthesis and later
corroboration as such; neither becomes an original observation. Include declared
omissions when selecting a derived source-only patch. Prior operator answers can
be selected as pinned `prior-response` sources, with their original question and
packet context; that is a new generation, not an edit to the previous judgment.

## Prepare, run and inspect

```sh
uv run skilleval synthesis prepare --spec SELECTION.json --output PREPARED
uv run skilleval synthesis inspect PREPARED --sha256 PREPARATION_SHA256
uv run skilleval synthesis run PREPARED --sha256 PREPARATION_SHA256 \
  --runtime NATIVE_RUNTIME --image-archive IMAGE_ARCHIVE \
  --image-sha256 IMAGE_SHA256 --catalog MODEL_CATALOG \
  --auth-file AUTH_FILE --output ATTEMPT
uv run skilleval results serve ATTEMPT/review
```

Use the `sha256` returned by preparation; it identifies `preparation.json`.
Every output directory is fresh. Preparation freezes selected source bytes,
validated accounting/assessment projections, the agent prompt and output schema.
It performs no model or VM work. Run verifies that generation before dispatch,
invokes a fresh native Codex session, and records a valid result for the browser.
There is no implicit retry or automatic experiment dispatch.

The current native route uses GPT-6.1 Sol/high with the judge's 15-minute default
deadline and existing request backstop. Runtime, image, catalog and auth paths
are explicit. Reuse applicable native qualification; synthesis does not need the
application's entire test suite rerun merely to read retained results. Keep
runtime/image/controller sources stable through cleanup.

The guest has the existing isolated native context and network boundary.
Selected files are supplied under inert source-ID names, not as harness
instructions. Evidence is untrusted data. The static judge policy permits source
reading and prohibits executing imported code; that prohibition is a declared
agent policy, not a new sandbox enforcement mechanism. Host inference still sends
selected content through the configured provider route.

## Judgment and deterministic facts

The agent authors the conclusion, quality observations, citations, investigation
notes and exception categories. The recorder supplies native author/model identity
and arm execution/usage/cost/time fields from validated records. Missing canonical
totals remain unknown even if a known subtotal or raw diagnostic exists. A
successful requirement subtable does not replace an unresolved overall verdict.
ON/OFF labels are visible because this is comparative synthesis, not blinded
primary grading.

The recorder binds output to the exact prepared packet and native input/prompt,
checks cited source identities and ranges, then creates the results projection.
It does not authenticate the correctness of reasoning or independently establish
grader reliability. The result retains original execution evidence and separates
synthesis overhead. Any valid output from an incomplete synthesis invocation is
explicitly partial; an absent or invalid result cannot become a completed review.

Recording can be retried offline after a recording defect is fixed, without
rerunning the model or changing earlier evidence:

```sh
uv run skilleval synthesis record PREPARED --sha256 PREPARATION_SHA256 \
  --run ATTEMPT/native --output NEW_REVIEW
```

Human answers use the existing append-only results interface. They neither
authorize further execution nor silently regrade an artifact. Agent follow-up
and qualified unknowns stay distinct from questions needing owner intent.

## Verification boundary

The ordinary gate exercises selection/replay, cross-arm rejection, malformed
citations, changed preparation/native identities, partial execution, accounting
unknowns and immutable recording with synthetic storage fixtures. Those fixtures
do not prove semantic judgment or real VM execution. A real native control must
also verify the actual prepared input, model/effort, tool access, final recording,
usage and cleanup. Preserve failed runs and state any remaining live limitation.
