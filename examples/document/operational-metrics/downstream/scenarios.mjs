// Pure authored fault/fixture protocol. Safe to test without loading application code.
export const regressionRoutes = [
  '/api/v2/activity/daily?since=2026-11-07&until=2026-11-07',
  '/api/v2/usage/overview',
];

export function rangeTransitionBinding(driver) {
  if (!driver.range?.length || !driver.range_return?.length) {
    return { applicable: false, reason: 'no paired wide/narrow range interactions declared; semantic review must verify applicability' };
  }
  const { wide_hours: wide, narrow_hours: narrow } = driver.range_windows || {};
  if (!(typeof wide === 'number' && typeof narrow === 'number' &&
        Number.isFinite(wide) && Number.isFinite(narrow) && 0 < narrow && narrow < wide &&
        Number.isFinite(new Date(Date.parse('2026-11-07T12:05:00Z') - wide * 3600_000).getTime()))) {
    return { applicable: false, reason: 'no explicit valid wide/narrow hour bounds declared; semantic review must verify applicability' };
  }
  return { applicable: true, windows: { wide_hours: wide, narrow_hours: narrow } };
}

export function fixtureResponse(metrics, mode, url, ordinal = 1, revision = 0, rangeWindows = null) {
  const failed = (mode === 'initial-error' && ordinal === 1) ||
    (mode === 'refresh-error-recovery' && ordinal === 2) ||
    (mode === 'range-transition' && ordinal === 3);
  if (failed) return { status: 503, body: { error: 'Authored transient metrics read failure' } };
  const updated = revision || ((mode === 'initial-error' || mode === 'fixture') && ordinal > 1) ||
    (mode === 'refresh-error-recovery' && ordinal > 2);
  let rows = structuredClone(metrics);
  if (mode === 'empty') rows = [];
  if (mode === 'range-transition' && ordinal === 2) {
    // This group is within the declared wide scope but outside the narrow one.
    // Keeping it in a completed wide read makes a later scope mix-up observable.
    const { wide_hours: wide, narrow_hours: narrow } = rangeWindows;
    const eventTime = new Date(Date.parse('2026-11-07T12:05:00Z') -
      (narrow + (wide - narrow) / 3) * 3600_000).toISOString();
    rows.push({ metric_name: 'codex.memory.startup',
      attrs: { state: 'skipped_rate_limit', phase: 'older-window' },
      occurrences: 1, total_value: 99, last_seen: eventTime.slice(0, 19).replace('T', ' ') });
  }
  if (mode === 'bounded') {
    const cap = Math.min(500, Math.max(1, Number(new URL(url).searchParams.get('limit')) || 200));
    rows = Array.from({ length: cap }, (_, n) => ({ ...rows[n % rows.length],
      attrs: { ...rows[n % rows.length].attrs, group: n } }));
  }
  if (updated) rows = rows.map(row => ({ ...row, total_value: row.total_value + 17,
    last_seen: '2026-11-07 12:04:00' }));
  return { status: 200, body: { metrics: rows } };
}

// Predeclared retention, independent of whether a state looks successful.
// Every other state retains DOM/ARIA/HTML/network/focus observations.
export function retainScreenshot(mode, label) {
  return label === 'initial' && ['fixture', 'empty', 'narrow-keyboard'].includes(mode);
}
