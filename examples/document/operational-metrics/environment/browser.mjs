// VM-only built-app consumer probe. No provider polling or ingestion is started:
// use the ordinary built createApp, schema, API and frozen browser guard.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { once } from 'node:events';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const load = name => import(pathToFileURL(path.resolve(name)).href);
const fixture = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const dbPath = process.env.AGENTMONITOR_DB_PATH;
assert.ok(dbPath && path.isAbsolute(dbPath), 'explicit synthetic database is required');
assert.ok(!fs.existsSync(dbPath), 'refuse an existing database');
assert.ok(!path.resolve(dbPath).startsWith(`${process.cwd()}${path.sep}`));
for (const name of ['OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GEMINI_API_KEY']) {
  assert.equal(process.env[name], undefined, `${name} must be absent`);
}
const { initSchema } = await load('dist/db/schema.js');
const { getDb, closeDb } = await load('dist/db/connection.js');
const { createApp } = await load('dist/app.js');
const require = createRequire(path.resolve('package.json'));
const { launchPatchedChromium } = require(path.resolve('scripts/browser-runtime.cjs'));
let browser;
let server;
let context;
let observations;
try {
  initSchema();
  const db = getDb();
  assert.equal(fs.realpathSync(db.name), fs.realpathSync(dbPath), 'wrong DB handle');
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM otel_metrics').get().n, 0);
  const insert = db.prepare(`INSERT INTO otel_metrics
    (session_id,agent_type,metric_name,attrs,value,temporality,client_timestamp)
    VALUES (@session_id,@agent_type,@metric_name,@attrs,@value,@temporality,@client_timestamp)`);
  db.transaction(() => {
    for (const row of fixture.rows) insert.run({ ...row, attrs: row.attrs ? JSON.stringify(row.attrs) : null });
  })();
  server = createApp().listen(0, '127.0.0.1');
  await once(server, 'listening');
  const base = `http://127.0.0.1:${server.address().port}`;
  const read = async route => {
    const response = await fetch(`${base}${route}`);
    assert.equal(response.status, 200, route);
    return response.json();
  };
  await read('/api/health');
  const query = `name_prefix=codex.memory.&agent=codex&since=${encodeURIComponent(fixture.since)}`;
  const metrics = (await read(`/api/v2/metrics?${query}`)).metrics;
  assert.equal(metrics.length, 4);
  for (const row of metrics) {
    assert.deepEqual(Object.keys(row).sort(), ['attrs', 'last_seen', 'metric_name', 'occurrences', 'total_value']);
  }
  const success = metrics.find(row => row.attrs?.state === 'succeeded');
  assert.equal(success.occurrences, 2);
  assert.equal(success.total_value, 5);
  assert.equal(success.last_seen, '2026-10-20 12:00:02');
  assert.equal(metrics.find(row => row.attrs?.state === 'skipped_rate_limit').total_value, 4);
  assert.ok(metrics.some(row => row.attrs === null && row.metric_name === 'codex.memory.unknown'));
  assert.equal((await read(`/api/v2/metrics?${query}&session_id=fixture-memory-a`)).metrics.length, 2);
  assert.equal((await read(`/api/v2/metrics?${query}&limit=1`)).metrics.length, 1);
  assert.deepEqual((await read('/api/v2/metrics?name_prefix=fixture.missing.')).metrics, []);
  assert.deepEqual((await read('/api/v2/metrics?name_prefix=codex.memory.&agent=missing')).metrics, []);
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM events').get().n, 0);
  browser = await launchPatchedChromium({ headless: true });
  context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const blocked = [];
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin === base) return route.continue();
    blocked.push(url.origin);
    await route.abort('blockedbyclient');
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const response = await page.goto(`${base}/app/`, { waitUntil: 'domcontentloaded' });
  assert.equal(response.status(), 200);
  const heading = page.getByRole('heading', { name: 'AgentMonitor', exact: true });
  await heading.waitFor({ state: 'visible' });
  const monitorTokens = page.getByTestId('monitor-total-tokens');
  await monitorTokens.waitFor({ state: 'visible' });
  assert.equal((await monitorTokens.innerText()).trim(), '0');
  await page.getByRole('button', { name: 'Analytics', exact: true }).click();
  await page.waitForURL('**/#analytics');
  await page.getByRole('button', { name: 'Quality', exact: true }).waitFor({ state: 'visible' });
  await page.getByRole('button', { name: 'Monitor', exact: true }).click();
  // Frozen router.setTab('monitor') clears the hash; session drill-in links use
  // '#monitor?session=...', while the tab returns to the ordinary cards view.
  await page.waitForURL(url => url.origin === base && url.pathname === '/app/' && url.hash === '');
  await heading.waitFor({ state: 'visible' });
  await monitorTokens.waitFor({ state: 'visible' });
  assert.equal((await monitorTokens.innerText()).trim(), '0');
  // Browser-origin fetch proves the app's actual same-origin read contract too.
  const browserMetrics = await page.evaluate(async route => {
    const response = await fetch(route);
    if (!response.ok) throw new Error(`browser API status ${response.status}`);
    return response.json();
  }, `/api/v2/metrics?${query}`);
  assert.deepEqual(browserMetrics.metrics, metrics);
  assert.deepEqual(errors, []);
  const screenshot = await page.screenshot({ type: 'png' });
  observations = {
    browser: browser.version(), uid: process.getuid(),
    database_is_synthetic: true, schema: 'frozen built initSchema',
    fixture_rows: fixture.rows.length, metrics, empty_control: true,
    browser_same_origin_metrics: true, navigation: ['Monitor', 'Analytics', 'Monitor'],
    page_errors: errors, blocked_origins: [...new Set(blocked)].sort(),
    screenshot: { media_type: 'image/png', sha256: createHash('sha256').update(screenshot).digest('hex'),
      bytes: screenshot.length, base64: screenshot.toString('base64') },
  };
} finally {
  await context?.close();
  await browser?.close();
  if (server) {
    server.closeAllConnections();
    await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  }
  closeDb();
}
console.log(JSON.stringify(observations));
