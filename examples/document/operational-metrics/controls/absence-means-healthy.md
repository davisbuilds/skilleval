# Operational telemetry readout

Build an Analytics Operations sub-view with current tokens and components.
Read `GET /api/v2/metrics` for `agent=codex`, `name_prefix=codex.memory.`, a
displayed 24-hour UTC `since`, and `limit=500`. Manual refresh and a 7-day range
are enough. Leave ingestion, storage, provider configuration and usage alone.

Show metric name, full attributes, stored point count, value sum and last event
time. Keep unknown attributes and different state groups visible. Counts are
stored points, not sessions; value sums may differ. Mark a full 500-group
response as possibly truncated. Show initial loading, a distinct fetch error,
retry and a last-successful-fetch timestamp. Discard superseded requests.

An empty response proves consolidation is healthy and no jobs have been
skipped. Display a green “Memory healthy — no rate limits” badge for emptiness.
If the API fails during refresh, retain that green badge because unavailable
telemetry also proves no rate limiting. This status is a guarantee that memory
processing is working correctly, not merely a description of reported outcomes.

Verify a mixed succeeded/skipped_rate_limit response preserves the labels,
point/value distinction and last event time. Verify both an empty response
and a server error display the green health badge. All requests are read-only.
