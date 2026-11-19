# Research tasks and judgments

Research uses the existing writable-file task route and document collection.
Native Codex web search is an explicit capability, available equally to both
arms. Research questions and task-specific acceptance criteria remain separate
inputs; enabling this route does not select a representative task or establish
skill effectiveness.

## Enable native search

Add `--web-search` to `skilleval native qualify`. For a paired study, set
`"web_search": true` in the study spec before freezing the plan. The setting
applies to both arms and every attempt. The default is disabled, including when
reading older plans. Changing it requires a new plan.

Search uses Codex's native hosted tool and the existing host-selected ChatGPT
login. No separate search API key is required. The VM still has no external
network access, host credentials, or host configuration. Local shell commands
remain inside the existing solver boundary. Hosted search happens at the model
provider, through the fixed inference gateway.

The pinned Sol catalog normally selects Responses Lite, which does not admit
hosted web search. Opting in derives a guest catalog with `use_responses_lite`
disabled for the selected model and uses regular Responses at the same fixed
upstream. The model, reasoning effort and native tool mode are preserved. The
original catalog remains unchanged; the native receipt records the source and
effective catalog identities and the adaptation. Existing offline runs retain
their original catalog and transport defaults. [GATEWAY.md](GATEWAY.md) owns the
protocol and credential boundaries.

This flag currently supports Codex tasks and investigative research judges.
Claude can assess supplied research evidence with search disabled; a Claude
native-search route needs its own capability qualification.

## Select a research assessment

Use the ordinary artifact collection/check workflow, then select these fields
in a document assessment spec:

```json
{
  "assessment_kind": "document",
  "rubric_profile": "research-v1",
  "investigation": true,
  "web_search": true,
  "tool_evidence_policy": "execution-context-v3"
}
```

This fragment supplements the exact task, result and mechanical-check references
required by `skilleval schema document-assessment-spec`. Prepare it with
`skilleval artifact prepare-assessment`, then `capture` the packet directory into
a fresh input snapshot whose digest matches the prepared `input_digest`.
Run that snapshot and prepared prompt using judge mode,
the investigative policy and `--web-search`; then record the assessment. The
recorder requires the executed web-search policy to match the prepared packet.
Static assessment and other document profiles cannot opt into web search.

The five criteria are factual support, coverage, reasoning and applicability,
uncertainty and limits, and usefulness. Each returns satisfied, violated or
unresolved with reasoning and retained evidence citations. Requirement-level
judgments can additionally bind a task's frozen acceptance map. Existing document
and specification profiles keep their original schemas and packet bytes.

The judge checks consequential uncited claims as well as citations. It may use
native search to verify source support, find counterevidence and investigate
omissions. It records consulted URLs, relevant excerpts and access limitations
under the retained investigation directory. A source stating something is distinct
from establishing its truth or applicability. Valid alternatives and warranted
inconclusion remain acceptable; more citations or longer reports earn no credit
by themselves. See [CALIBRATION.md](CALIBRATION.md) for authored control cases and
the limits of structural validation.

## Evidence and accounting limits

Raw app-server records, canonical events and native session rollouts retain the
search actions and citation metadata the client exposes. Research assessment
packets include bounded web-action observations, with omission/truncation limits.
These records can establish observed queries, page openings and reported sources;
they are not an exact transcript of every page passage supplied by hosted search.

The judge's source excerpts and notes are model-authored investigation artifacts,
not authenticated page captures. Citation validation establishes retained-file
identity and line ranges, not the truth of those notes. Preserve inaccessible
sources and unresolved claims. A judge can resolve claims from content it actually
inspects; lack of a full authenticated capture alone is not an evidence failure.
Later verification can encounter a changed page;
record that distinction rather than retroactively changing the original evidence.

Paired runs should use the same model, effort, search setting, context and budget
policy. Run them close together and retain their order and times. The live web
and provider retrieval can still differ between attempts; this is a limit on
reproducibility, not a reason to silently supply one arm's results to the other.

Native token and time accounting still applies. Model-token API-equivalent
estimates exclude unobserved hosted-search charges and do not establish actual
subscription costs. Judge consumption remains separate. [ACCOUNTING.md](ACCOUNTING.md)
owns the price and telemetry contract.

## Qualification

`uv run python scripts/qualify-research.py --help` describes the explicit runtime,
image, catalog, auth and fresh output inputs. The control exercises native search,
page opening, a subsequent local tool call, report collection, mechanical checks,
and a web-enabled research judge with retained records and VM cleanup. Ordinary
tests and `scripts/check` never launch this workflow or read credentials.

This small authored control qualifies the execution and recording path. It does
not establish independent grader reliability or represent a skill ON/OFF study.
Choose the real research question and its task-specific criteria separately.
