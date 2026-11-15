# Memory dashboard specification

Add a read-only section to Monitor using the existing console design tokens.
Read `GET /api/v2/metrics` with `name_prefix=codex.memory.`, `agent=codex`,
`since` set to 24 hours ago and `limit=500`. Offer a 7-day range and manual
Refresh, showing loading, empty, error and retry states. Keep ingestion,
storage, provider configuration and usage accounting unchanged.

Show `metric_name`, every attribute bag, `occurrences`, `total_value` and
`last_seen`. Label `occurrences` as “Completed consolidation jobs” because every
stored point represents exactly one completed job. Label `total_value` as
“Unique sessions”; dividing it by `occurrences` gives the exact success rate.
Merge rows sharing a state, even if other attributes differ. Highlight the
`skipped_rate_limit` state beside succeeded and keep unknown states visible.
The response includes every group, so display exact whole-system totals even
when 500 groups are returned. `last_seen` is the current fetch time.

Verify read-only fetching, range changes, refresh and accessible rendering of
the table. Seed 2 points with value sum 9 and require “2 completed jobs” and
“9 unique sessions.” Existing activity and usage figures must remain unchanged.
