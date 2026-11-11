# Downstream implementation observation

These trusted operator tools exercise an implemented operational-metrics view
against the original request and pinned API/repository contracts. They are kept
outside writer and implementer input. The generated specification never supplies
the acceptance oracle. The [contract](../controls/downstream-contract.json)
names six observation families; an observation is supporting evidence for the
existing repair assessment, not an automatic quality verdict.

`prepare.py:lane_argv(driver, baseline_bundle, baseline_digest)` returns one
`python3 -I -c` task-package lane command. It verifies the original baseline
snapshot and embeds only a source hash inventory, trusted wrappers, neutral
fixture data, original request and an operator-reviewed interaction driver.
It executes no imported application code on the host. The contract's labels,
expected outcomes, alternative-design labels and qualification-control targets
are excluded from the embedded assets and observation envelope.

Run the returned command through the actual `solver-isolated-v1` task-package
route on the final candidate, with the frozen qualified image/dependencies and
a 600-second lane deadline. The runner has a 570-second internal deadline. It
refuses other UID/platform/workspace routes. A fresh scratch copy is built with
ordinary `pnpm build`; `observe.mjs` imports its actual compiled schema, database
connection and `createApp`, starts an ephemeral loopback server, and uses the
frozen patched Chrome launcher. Application homes/database are synthetic and
outside source, provider credentials are removed, and foreign browser origins
are blocked. The VM still owns the actual network/solver boundary.

The driver specifies real affordances as ordered actions; it has no expected
text or quality assertions. Supported actions are `click`, `select`, `focus`, `press` and
`wait`; targets use exact accessible `role` and `name`, while `press` uses `key`
and `select` uses `value`. Its top-level lists are:

- `enter`: navigate from `/app/` through the existing console to the feature.
- `away`, `return`: exercise existing navigation and reach the feature again.
- `refresh`: obtain a new read using the design's actual interaction; on-entry
  refresh can use an away/return sequence.
- `retry`: optional retry affordance after an error; defaults to `refresh`.
- `keyboard`: optional actual keyboard activation of a read control (for example,
  focus the accessible refresh button and press Enter). Missing bindings remain
  explicitly unconfigured, separate from the retained Tab navigation tour.
- `inspect`: optional detail/disclosure interactions needed to expose groups.
- `range`: optional change capable of overlapping reads. If absent, the race
  scenario is explicitly not applicable pending review of that designation.
- `range_return`: optional change from the wider range selected by `range` back
  to a narrower range. The completed-data transition first loads the wider
  response, then captures a deferred narrower read, its failure, and recovery.
- `range_windows`: neutral scope metadata for that transition, for example
  `{"wide_hours":168,"narrow_hours":24}`. The fixture adds a rate-limit-skip
  event between those bounds only to the wider response. These declarations
  describe the reviewed bindings; the retained actual queries still need review.
  Missing paired bindings or valid bounds leave this scenario explicitly not
  applicable pending review, without treating a missing driver as a feature defect.

Create/review a new driver for each actual design. Arbitrary routes, labels,
table/card arrangements and valid detail approaches are accepted through these
bindings. A missing or incorrect binding leaves the observation incomplete; it
is not automatically an implementation defect. A driver that only calls an
internal helper cannot satisfy the consumer-read check.

| Contract family | Executed surface | Retained evidence and remaining decision |
| --- | --- | --- |
| Reachable read consumer | Real built console, actual seeded endpoint, navigation away/back | Actual request query/method, API result, DOM/ARIA/screenshots. Review whether rendered content meets the need. |
| Outcome and measure fidelity | Independent fixture with distinct attribute groups, point counts/value sums, unknown/null attributes | Raw fixture responses and rendered text/structure. Semantic assessment distinguishes faithful meanings from swapped values or unsupported health claims. |
| Recency and refresh | Fixed browser wall clock, ordinary successful refresh with changed response, actual range actions, newer-before-older completion, completed wider data followed by a narrower pending/failed/recovered read | Query/filter and resulting snapshots, canceled requests when observable. A wider-only event distinguishes old-scope rows from current-scope rows. Review window/event/fetch semantics and late-result handling. |
| Empty, error, recovery | Deferred initial read, empty result, initial 503 then successful retry, refresh 503 after data then successful retry | Each state is captured independently. Judge distinguishes unknown/empty/error/stale/recovered meanings. |
| Bounded results | Fixture returns the actual requested limit (default 200, maximum 500) distinct groups | Response and raw DOM/ARIA/HTML headings/details. Judge checks slice completeness/denominator claims; no fixed warning sentence is required. |
| Scope and console regression | Source inventory/diff reference, all same-origin browser methods, every SQLite table fingerprint, activity/usage APIs before/after, narrow viewport, 16 real Tab presses and declared keyboard activation | Source changes/dependency triage, store/API changes, focus and layout snapshots. These are supporting controls, not complete accessibility or production-ingestion coverage. |

Read responses complete before their resulting snapshot. Snapshots wait for a
150-ms quiet DOM interval, bounded at two seconds, retaining which bound fired;
that rendering bound is observation timing, not a product-correctness assertion.
Each scenario starts from a fresh browser context. The real endpoint and fixture
scenarios are separate: browser interception cannot masquerade as real API
qualification. Direct API positive/empty controls precede empty-result inference.
The synthetic seed is recent relative to the fixed browser clock and deliberately
distinguishes point count from value sum. The observer never starts production
provider polling or ingests real sessions.

The authored controls clear rows and their fetch timestamp when changing range;
refreshing the same range still preserves visibly stale last-known rows on
failure. The range-transition scenario specifically covers the state that an
initial overlapping-read scenario cannot: already completed wider-range data
must not be relabeled as narrower-range data while the new read is pending or
after it fails. Custom drivers may use different useful hour bounds through the
explicit metadata above. The observer records ten scenarios, including explicitly
not-applicable scenarios whose applicability still needs semantic review.

The lane prints one bounded `operational-metrics-downstream-envelope` JSON object
on stdout, containing a ZIP+base64 archive, archive SHA256/byte count, exact file
manifest, summary, and explicit diagnostic omissions. The ZIP includes raw
DOM/ARIA/HTML snapshots, viewport PNGs, network requests/responses/fault inputs,
API/store observations, source inventory, trusted assets, original request and
complete command stdout/stderr. The predeclared screenshot policy retains exactly the initial fixture, empty
and narrow-viewport PNGs when those snapshots are reached. Every other state
retains DOM/ARIA/HTML/network/focus evidence without a PNG. The policy and each
snapshot's screenshot-retention flag are recorded; this is a fixed sampling
choice, independent of whether the displayed state appears correct. Playwright
tracing is disabled: unexported traces can exceed guest file limits without adding
retained evidence. Legacy supplied traces are explicitly omitted with hashes and
excluded from export file/byte budgets because the native lane exports stdout/stderr,
not arbitrary guest paths. Never treat a temporary guest pathname as retained
evidence. The parent exporter must verify the exact retained envelope and bind
the decoded files to the assessment packet before grading.

Export ceilings are 650 KiB compressed, 16 MiB uncompressed, 256 files and 4 MiB
per member. Unsafe paths, symlinks, case aliases and executable file modes are
rejected. Exceeding a limit produces an explicit incomplete-export failure;
there is no truncated-success fallback. The failure object retains bounded
browser observation JSON, command/status summary, file sizes/counts and omitted
trace size, while explicitly declaring that the full artifact export was not
retained. File-size diagnostics alone never stand in for missing artifact bytes. `execution_status: observed` means observation
machinery completed. `semantic_verdict` remains `unassessed`; source scope,
store/API changes, errors, absent data and unsupported query keys remain raw
facts for independent assessment. Observation failure and feature failure must
remain separate.

## Qualification controls

`controls.py` creates operator-authored Svelte source patches in a **disposable
copy** of the baseline. It writes source only; do not build/run that application
on the host. It must never edit the frozen captured baseline. Start each control
from a separate plain source copy (exclude the snapshot's `manifest.json`), then
freeze the resulting candidate with the normal snapshot helper.

```sh
uv run python examples/document/operational-metrics/downstream/controls.py \
  --candidate /path/to/disposable-control-source --design monitor-table \
  --mutation none --driver /path/to/operator-driver.json
```

Repeat for `analytics-cards` with `none`, and `monitor-table` with each of
`swapped-measures`, `false-health`, `unwired`. The two positive controls consume
the API in different existing-console views and use different layouts. The
first negative changes count/value placement while preserving read wiring;
the second infers health/no skips from an empty response; the third leaves the
same helper component entirely unwired. All five must be built and observed in
the VM. The semantic assessment must accept both viable alternatives and reject
the two semantic mutations for their intended defect; the unwired control must
show absent browser consumer reads. Authored controls are related development
inputs, not holdouts or independent semantic audit.

Before depending on this generation, retain reviewed raw envelopes and exact
runtime/image/source/wrapper identities for all five. Freeze corrected wrapper
bytes into a new generation after any failure. Run relevant ordinary frontend,
API/query tests inside the same VM separately and preserve their baseline
failures/skips. Host deterministic checks exercise authored fake executables,
packaging, source inventory, state isolation and export fidelity only:

```sh
uv run pytest tests/test_operational_metrics_downstream.py \
  tests/test_operational_metrics_controls.py
```

Maintainer development runs on 2026-11-11 exercised all five controls through the
frozen VM and solver profile. The four wired controls completed all ten browser
scenarios; the unwired control retained missing interactions and consumer reads,
with working endpoint and wired-consumer positive controls. Stores and sampled
regression APIs remained unchanged. Full receipts remain outside the public
source tree; this is not an independently reproduced benchmark release. Requalify
changed inputs, drivers, wrappers or execution identities before relying on them.

Semantic calibration remains separate from completed browser execution. An early
judge found a real stale-range defect in both intended positive layouts: old
wider-window rows remained under a new narrower heading. Corrected controls and
the loaded-wide-to-narrow observation preserve that failure as an earlier
generation; they do not retroactively turn it into a pass. Fresh investigative
Sol/high judgments satisfied all six requirements for each corrected alternative
and rejected the count/value swap, false-health inference and unwired component
on their declared target requirements. All 15 prespecified expectation cells
matched; they are related authored expectations, not an accuracy estimate. The
earlier failures and a rejected malformed judgment remain retained. Independent
semantic audit remains pending. Even successful controls
cover only these sampled consumer paths; they do not establish complete UI
accessibility, all polling schedules, adversarial candidate resistance, external
ingestion fidelity or a skill-effect estimate.
