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

// Each authored group represents co-timed points from the synthetic Codex scope.
// No session association is inferred from a metric name or aggregate row.
// Counts and value sums are separate scalars; query projection never treats a
// count as a value or uses last_seen as the independent browser fetch time.
const fixtureTime = value => {
  const raw = String(value);
  const normalized = /(?:Z|[+-]\d\d:\d\d)$/i.test(raw) ? raw : raw.replace(' ', 'T') + 'Z';
  return Math.floor(Date.parse(normalized) / 1000);
};
function prefixMatches(name, prefix) {
  // Match the endpoint's SQLite LIKE prefix, including its ASCII case folding.
  const pattern = [...prefix].map(char => char === '%' ? '.*' : char === '_' ? '.' :
    char.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('');
  return new RegExp('^' + pattern + '.*$', 'i').test(name);
}
export function fixtureResponse(metrics, mode, url, phase = 'initial', { revision = 0, rangeWindows = null } = {}) {
  const failed = (mode === 'initial-error' && phase === 'initial') ||
    (mode === 'refresh-error-recovery' && phase === 'refresh-failed') ||
    (mode === 'range-transition' && phase === 'narrow-failed');
  if (failed) return { status: 503, body: { error: 'Authored transient metrics read failure' } };
  const updated = revision || ['refreshed', 'initial-recovered', 'recovered', 'narrow-recovered'].includes(phase) ||
    (mode === 'fixture' && ['away', 'returned'].includes(phase));
  let rows = structuredClone(metrics);
  if (mode === 'empty') rows = [];
  if (mode === 'range-transition' && phase === 'wide-loaded') {
    const { wide_hours: wide, narrow_hours: narrow } = rangeWindows;
    const eventTime = new Date(Date.parse('2026-11-07T12:05:00Z') -
      (narrow + (wide - narrow) / 3) * 3600_000).toISOString();
    rows.push({ metric_name: 'codex.memory.startup',
      attrs: { state: 'skipped_rate_limit', phase: 'older-window' },
      occurrences: 1, total_value: 99, last_seen: eventTime.slice(0, 19).replace('T', ' ') });
  }
  if (updated) rows = rows.map(row => ({ ...row, total_value: row.total_value + 17,
    last_seen: '2026-11-07 12:04:00' }));
  const query = new URL(url).searchParams;
  rows = rows.filter(row => (!query.get('name_prefix') || prefixMatches(row.metric_name, query.get('name_prefix'))) &&
    (!query.get('agent') || (row.agent_type ?? 'codex') === query.get('agent')) &&
    (!query.get('since') || fixtureTime(row.last_seen) >= fixtureTime(query.get('since'))));
  if (query.get('session_id')) {
    if (rows.some(row => typeof row.session_id !== 'string')) {
      return { supported: false, reason: 'session_id fixture scope requires explicit contributing-session metadata; use separate real-endpoint evidence' };
    }
    rows = rows.filter(row => row.session_id === query.get('session_id'));
  }
  const grouped = new Map();
  for (const row of rows) {
    const key = JSON.stringify([row.metric_name, row.attrs]);
    const previous = grouped.get(key);
    if (!previous) grouped.set(key, { ...row });
    else {
      previous.occurrences += row.occurrences;
      previous.total_value += row.total_value;
      if (fixtureTime(row.last_seen) > fixtureTime(previous.last_seen)) previous.last_seen = row.last_seen;
    }
  }
  rows = [...grouped.values()];
  const requested = Number(query.get('limit'));
  const cap = Number.isFinite(requested) && requested > 0 ? Math.min(Math.trunc(requested), 500) : 200;
  if (mode === 'bounded' && rows.length) {
    rows = Array.from({ length: cap }, (_, n) => ({ ...rows[n % rows.length],
      attrs: { ...rows[n % rows.length].attrs, group: n } }));
  }
  rows.sort((a, b) => fixtureTime(b.last_seen) - fixtureTime(a.last_seen));
  rows = rows.slice(0, cap).map(({ metric_name, attrs, occurrences, total_value, last_seen }) =>
    ({ metric_name, attrs, occurrences, total_value, last_seen }));
  return { status: 200, body: { metrics: rows } };
}

// Action generations describe controlled arrival/release intervals, not a
// claimed application-internal transaction ID. No fixed request count is used.
export class ReadGenerations {
  constructor({ quietMilliseconds = 150, deadlineMilliseconds = 5000,
                now = () => performance.now(), sleep = ms => new Promise(resolve => setTimeout(resolve, ms)) } = {}) {
    this.quietMilliseconds = quietMilliseconds;
    this.deadlineMilliseconds = deadlineMilliseconds;
    this.now = now;
    this.sleep = sleep;
    this.generations = [];
    this.deferredReads = new Map();
    this.active = null;
  }
  begin(phase, { deferred = false, revision = 0 } = {}) {
    const generation = { id: this.generations.length + 1, phase, deferred, revision,
      requests: [], lastActivity: this.now(), sealed: false, lateRequests: 0 };
    this.generations.push(generation);
    this.activate(generation);
    return generation;
  }
  activate(generation) { this.active = generation; }
  record(row) {
    if (!this.active) throw new Error('metrics request has no declared action generation');
    const generation = this.active;
    if (generation.sealed) { row.late_arrival = true; generation.lateRequests++; }
    row.generation = generation.id;
    row.phase = generation.phase;
    generation.requests.push(row);
    generation.lastActivity = this.now();
    return generation;
  }
  complete(row, failure = null) {
    if (failure) row.failure = failure;
    else row.completed = true;
    this.generations.find(item => item.id === row.generation).lastActivity = this.now();
  }
  defer(row, supply) {
    this.deferredReads.set(row, { generation: this.generations.find(item => item.id === row.generation), supply });
  }
  async release(generation, revision = 0) {
    this.activate(generation);
    generation.revision = revision;
    generation.deferred = false;
    const reads = [...this.deferredReads.entries()].filter(([, read]) => read.generation === generation);
    if (!reads.length) throw new Error('requested deferred generation did not occur');
    await Promise.all(reads.map(async ([row, read]) => {
      this.deferredReads.delete(row);
      await read.supply();
    }));
    await this.wait(generation);
  }
  async wait(generation, { pending = false, requireRequests = true } = {}) {
    const end = this.now() + this.deadlineMilliseconds;
    while (this.now() < end) {
      const arrived = !requireRequests || generation.requests.length > 0;
      const terminal = pending || generation.requests.every(row => row.completed || row.failure);
      if (arrived && terminal && this.now() - generation.lastActivity >= this.quietMilliseconds) {
        if (!pending) generation.sealed = true;
        return;
      }
      await this.sleep(25);
    }
    throw new Error(`consumer generation ${generation.phase} did not ${pending ? 'arrive' : 'settle'} within observation deadline`);
  }
  describe() {
    return this.generations.map(({ id, phase, requests, lateRequests, sealed }) =>
      ({ id, phase, request_ordinals: requests.map(row => row.ordinal), late_requests: lateRequests, settled: sealed && lateRequests === 0,
         quiet_milliseconds: this.quietMilliseconds, deadline_milliseconds: this.deadlineMilliseconds }));
  }
}

// Predeclared retention, independent of whether a state looks successful.
// Every other state retains DOM/ARIA/HTML/network/focus observations.
export function retainScreenshot(mode, label) {
  return label === 'initial' && ['fixture', 'empty', 'narrow-keyboard'].includes(mode);
}
