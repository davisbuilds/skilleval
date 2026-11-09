The console needs a small operational readout of reported memory outcomes.
Place it under Analytics as an Operations sub-view, using the existing shell,
URL and component conventions. A fresh engineer should be able to implement
this as a frontend consumer of the shipped read API. It must never modify OTEL
ingestion/storage, provider configuration, tokens or cost accounting.

On entry show Codex memory activity for the last 7 days, with 1-hour and 1-day
shortcuts and a manual Refresh action. Only the supported time window applies:
make project and unrelated Usage facets visibly inapplicable in this sub-view,
rather than silently implying project-filtered metrics. Preserve the selected
window on the Operations deep link using the console's normal hash conventions.
Use its existing visual tokens, accessible controls and responsive containers.

The API helper calls `GET /api/v2/metrics?name_prefix=codex.memory.&agent=codex`
with a UTC `since` and `limit=500`. The JSON envelope has `metrics` rows, each
with `metric_name`, `attrs`, `occurrences`, `total_value`, `last_seen`. Filters
supported by the API but not exposed in this first view include `session_id`.
No API changes or new persisted settings are necessary.

Lead with most recently reported activity time, the selected query range and
the last successful fetch time. Follow with a list grouped by metric name.
Each name expands to all its attribute groups, showing full attributes, stored
point count and value sum separately. Surface `state=skipped_rate_limit`
prominently beside other outcomes; permit unknown states and null attributes.
Use those terms as reported outcome labels rather than asserting operational
success or correctness of memory content. Do not merge groups that share a state
but differ in another attribute. Do not promise a trend chart: this endpoint
does not return time buckets or individual point history.

Explain near the data that point counts are not job or session counts, and a
value sum need not equal point count. Last-seen uses the latest coalesced client
timestamp/created-at event time, normalized by the existing query; display its
UTC meaning correctly. A response may be bounded to 500 newest groups, without
pagination or a total count. When that many groups arrive, show a possible
truncation notice and qualify the newest time and summaries as returned data.
An empty result means no reports matched this query, not proof of health,
failure or absence of collection. No synthetic “all healthy” state or success
ratio belongs in this first implementation.

Loading must be distinct from empty and failed reads. Initially failed reads
show a retry action without invented zeros. During a refresh keep existing
data visibly labelled as last-known; a failure marks it stale and preserves
the error until a successful retry. Switching ranges starts a new request;
an older response cannot overwrite the current selection. No polling is required.

Acceptance is observable in the console: fixture rows with non-unit values and
distinct state/attribute combinations remain distinguishable; rate-limit skips
are discoverable; last reported time differs from request time; range/refresh
controls produce the corresponding supported read query. Exercise an empty
response, rejection, stale data, retry recovery and reversed response order.
Check 500-group qualification, keyboard access and a narrow viewport. Verify
no operational data enters activity/usage totals and no write endpoint is used.
Run ordinary frontend checks/build, recording which browser checks were actually
performed. Layout refinements and file decomposition are implementer choices.
