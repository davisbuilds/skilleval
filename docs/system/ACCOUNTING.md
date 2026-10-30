# Usage, cost and time accounting

This reference owns the implemented development accounting contract. The
[study controller](STUDIES.md) freezes treatment, budgets and optional prices;
native receipts retain the observations. A development pair on 2026-10-28
completed on Codex 0.164.0 with matching native/provider core totals and collected
agent/stage timings. The full pinned ON skill body appeared in the first request
and all eleven observed requests. This verifies the capture/integration path;
it does not establish provider billing accuracy, task correctness or skill benefit.

## Native usage and independent reconciliation

`usage_accounting` normalizes one fresh Codex thread/turn from a single cumulative
usage snapshot in canonical events. The legacy `codex-exec-json` surface uses its
terminal `turn.completed.usage` snapshot. The `codex-app-server-jsonrpc-v2` surface
uses the latest matched-thread `thread/tokenUsage/updated.params.tokenUsage.total`
before the matched `turn/completed` notification. The app-server adapter places
that snapshot in the canonical `turn.completed.usage` field; it does not sum
updates or use `tokenUsage.last`. Raw RPC observations remain separate from the
derived canonical events. Missing native usage remains unknown.
Full start evidence, ordered events, an admitted observed adapter version and
consistent nonnegative integer counts are required. Parse errors, native failure
events, missing terminal evidence, multiple turns/threads or unreviewed fields
prevent a complete observation. Missing telemetry stays unknown; an all-zero
native snapshot needs matching provider observations because zero can be a native
missing-telemetry fallback.

For legacy exec, accounting provenance records the observed harness identity and the
[reviewed Codex JSONL source revision](https://github.com/openai/codex/blob/1c0c45bfc372fb7d7d581508fdef5eef8235eab7/codex-rs/exec/src/event_processor_with_jsonl_output.rs#L118-L128).
That source reference does not prove equivalence to the observed binary. Input
includes cached input: `uncached_input_tokens = input_tokens - cached_input_tokens`;
`total_tokens = input_tokens + output_tokens`. Optional reasoning tokens remain a
subset of output and are not added again. Absent cache-write or reasoning detail
remains null rather than zero.

The normalizer's optional `surface` argument defaults to `codex-exec-json`,
preserving legacy provenance and behavior. Unknown surfaces are rejected. The
app-server surface records `app-server-v2-to-canonical-v1` as its adapter and
the [reviewed app-server notification source](https://github.com/openai/codex/blob/6aaac694803004a63c6f393d153c96d22521a6b9/codex-rs/app-server/src/bespoke_event_handling.rs#L1602-L1615).
It retains the observed Codex identity and leaves binary equivalence unverified.
The caller supplies canonical events and adapter parse errors; choosing a surface
does not itself validate a raw RPC transcript or establish adapter correctness.

The native runner supplies the host broker's provider-request receipts as an
independent core-total cross-check. Reconciliation checks request identity,
model, completion, usage fields and arithmetic before comparing summed provider
input/output/total with the terminal native counts. When supplied, incomplete
provider evidence prevents complete accounting; a mismatch or invalid evidence
invalidates it. Without provider observations, nonzero native usage can remain a
complete native-only observation; the runner supplies its broker ledger.
The normalizer can validate supplied rows, not independently prove that its
caller supplied an exhaustive ledger or a fresh thread.

Provider cached-token detail is cross-checked only when every request supplies
it. If absent, matching core totals can still produce complete usage, while
`cached_input_status` remains `unknown`: the cache partition is native-reported,
not independently verified. A complete usage status therefore does not establish
independent cache calibration. Legacy raw `usage` remains available as original
observations and is not an additional quantity to sum with normalized usage.

## Conditional API-equivalent cost

`price_schedule` is optional and frozen with the study. It records the exact model,
USD input/cached-input/output rates, public source URL, retrieval date and scope.
Validation checks its structure, not the source's truth or applicability. The
[2026-10-28 standard scenario](../../examples/pricing/gpt-6.1-sol-standard-2026-10-28.json)
records a dated official API pricing source and a per-request short-context bound.
It is an explicitly chosen comparison scenario, not a current-price promise.

With complete matching-model usage, the estimate is:

```text
(uncached input × input rate + cached input × cached rate + output × output rate)
/ 1,000,000
```

The result is `estimated_api_equivalent_usd`, represented as a decimal string.
`cash_cost_usd` remains null. No provider charge is guessed, and subscription
execution does not establish an API bill. Tool, regional, infrastructure,
subscription and cache-write charges are excluded; unobserved service-tier or
context-rate changes are outside the scenario. Nonzero observed cache writes
withhold an estimate pending a supported price contract. Missing cache-write
detail does not establish that no such charge exists.

If the schedule has a per-request input limit, an estimate requires matching
provider core totals and observed request sizes within that bound. Session totals
are not used to choose a request tier. The estimator does not infer actual service
tier or verify cache discounts when provider cache details are absent. Preserve
those limits alongside any estimate. Missing schedules or incomplete usage leave
cost unknown; unsupported scenarios are not silently repriced.

## Time scopes

The guest monotonic timer measures the native subprocess lifetime, including
termination, as `agent_seconds`. The host accepts it only when finite,
nonnegative and consistent with the enclosing native step. It remains a collected
guest observation, not an independently authenticated stopwatch.

Host monotonic stages separately record setup, the native step, collection,
cleanup and total time through VM cleanup. Total excludes final receipt
publication. The native step includes more than the agent timer; do not add
`agent_seconds` to it or sum overlapping scopes. The study's legacy
`elapsed_seconds` wraps the controller invocation and remains a distinct measure.
Missing/crash-interrupted stages stay unknown. Judge runs have their own receipts;
their consumption is not folded into task-agent usage.

## Paired differences and operational spend

Paired descriptive differences are ON minus OFF. Only first attempts with clean
completed execution and equal non-null harness identities are eligible. Token
and timing fields require complete observations on both sides; cost additionally
requires identical schedules. No retry replaces a failed first attempt.

The operational ledger retains all recorded attempts, including failures,
unfinished executions and retries. Per-field known subtotals carry known/unknown
attempt counts; a full total is null if any attempt is unknown. Cost is grouped
by exact schedule and never pooled across schedules. Unstarted cells and missing
telemetry do not become zero-cost observations. Earlier receipts without the new
fields remain missing observations, not reconstructed measurements.

Study output places paired observations under `pairs[].efficiency` and operational
spend under `efficiency_ledger`. `pair_coverage` distinguishes planned pairs,
matching harness pairs and pairs with known total-token, cost or agent-time
differences. Those denominators describe measurement availability, not task success.

Task quality remains unassessed and uncertainty is not estimated. These summaries
support inspection of development consumption, not a quality-adjusted efficiency
claim, a statistical comparison or a skill-effect estimate. Independent cache
calibration, broader tier/charge support, representative quality evidence and
cluster-aware uncertainty remain future work in the
[backlog](../project/BACKLOG.md).
