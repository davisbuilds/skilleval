# Read-only memory outcomes in Monitor

Add an Operational metrics section on Monitor, reachable without leaving the
console. Its question is “what has Codex memory processing reported recently?”
This is telemetry inspection, not a memory quality score or proof that every
memory job ran. No ingestion, schema, provider settings or usage changes belong
in this feature.

Use the current Instrument Console components and tokens, a typed API helper and
a page-local reactive store. Start with Codex memory metrics over the previous
24 hours. Show the selected lower bound explicitly; allow 1 hour, 24 hours and
7 days. Fetch on opening and after a range change; a Refresh button refetches
the current query. Indicate when the response was fetched separately from the
most recent reported metric time. Manual refresh is sufficient for this view.

Read `GET /api/v2/metrics` with `name_prefix=codex.memory.`, `agent=codex`,
an absolute UTC `since` derived from the selected window, and `limit=500`.
The endpoint returns `{ metrics: [...] }`. Do not wire unrelated Monitor Agent
or project filters into this section as if the API supported project scope;
label the section as Codex memory across sessions. The backend also supports
`session_id`, but session drill-in is deferred.

Present each returned group as a table row: metric name, full attribute bag,
reported points (`occurrences`), reported value sum (`total_value`), and last
reported time (`last_seen`, interpreted as UTC). Give missing attributes a neutral
“No attributes” label; render unknown keys/states as data. Do not collapse two
different attribute bags merely because both have `state=succeeded`. Highlight
the literal `state=skipped_rate_limit` outcome so skips are easy to find, while
preserving all returned outcomes. Long names/attributes may wrap or scroll;
keyboard users must reach refresh/range controls and the same readable data.

`occurrences` counts stored points, not sessions or guaranteed job executions;
`total_value` sums values and can differ from that count. Do not add sums across
unrelated metric names or invent a success percentage. `last_seen` is the maximum
event time, using client timestamp with created-at fallback, within the query.
An empty window says “No reported Codex memory metrics in this window.” It does
not say memory is disabled, broken or healthy. At 500 groups, warn that the API
cap may omit older groups; the API supplies no complete-population total or
pagination token. Avoid whole-system totals.

Show initial loading, an empty response and an explicit read error distinctly.
On refresh failure retain old rows only with a visible stale/error label and
their last successful fetch time. A later successful refresh clears the error
and replaces rows. A superseded range response must not replace a newer range.

Verify with a browser/API fixture containing succeeded, skipped_rate_limit,
an unknown state and two distinct attribute bags sharing a state. An operator
must see the rate-limit outcome, distinguish 2 reported points from value 9,
and read the latest event time. Check window requests and refresh, empty/error/
recovery states, a 500-group response and reversed request completion. Confirm
the view makes no writes and leaves existing Monitor data and usage totals
unchanged. Run the frontend tests/build and preserve any unverified browser
coverage explicitly. This document requests implementation; no feature code is
part of this specification.
