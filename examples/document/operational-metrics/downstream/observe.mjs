// Trusted operator observer. Execute only through the isolated guest runner.
// Candidate code is imported only after the guest identity/state checks.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { once } from 'node:events';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { regressionRoutes, fixtureResponse, retainScreenshot, rangeTransitionBinding, ReadGenerations } from './scenarios.mjs';

assert.equal(process.platform, 'linux', 'guest Linux required');
assert.equal(process.getuid(), 61000, 'solver UID required');
const [contractPath, driverPath, fixturePath, output] = process.argv.slice(2);
assert.ok(output && path.isAbsolute(output) && !fs.existsSync(output), 'fresh absolute output required');
const dbPath = process.env.AGENTMONITOR_DB_PATH;
assert.ok(dbPath && path.isAbsolute(dbPath) && !fs.existsSync(dbPath));
assert.ok(!path.resolve(dbPath).startsWith(`${process.cwd()}${path.sep}`));
for (const key of Object.keys(process.env)) {
  assert.ok(!key.endsWith('API_KEY') && !key.endsWith('ACCESS_TOKEN') && !key.endsWith('AUTH_TOKEN'));
}
const contract = JSON.parse(fs.readFileSync(contractPath));
const driver = JSON.parse(fs.readFileSync(driverPath));
const rangeTransition = rangeTransitionBinding(driver);
const fixture = JSON.parse(fs.readFileSync(fixturePath));
const digest = data => createHash('sha256').update(data).digest('hex');
const load = name => import(pathToFileURL(path.resolve(name)).href);
fs.mkdirSync(output, { recursive: true });
const write = (name, value) => fs.writeFileSync(path.join(output, name), JSON.stringify(value, null, 2));
const { initSchema } = await load('dist/db/schema.js');
const { getDb, closeDb } = await load('dist/db/connection.js');
const { createApp } = await load('dist/app.js');
const require = createRequire(path.resolve('package.json'));
const { launchPatchedChromium } = require(path.resolve('scripts/browser-runtime.cjs'));
let browser, server, context;
const observation = { schema_version: 1, kind: 'operational-metrics-consumer-observation',
  semantic_verdict: 'unassessed', uid: process.getuid(), browser: null,
  browser_clock_utc: '2026-11-07T12:05:00Z',
  screenshot_policy: 'initial-fixture-empty-narrow-only-v1',
  read_generation_policy: 'action-release-arrival-quiet-v1',
  fixture_projection: { agent: 'synthetic Codex scope', point_times: 'co-timed contributions at each authored group last_seen',
    session_scope: 'unsupported without explicit contributing-session metadata; real endpoint evidence is separate' },
  fixture_contract_sha256: digest(fs.readFileSync(contractPath)), driver_sha256: digest(fs.readFileSync(driverPath)),
  scenarios: [], page_errors: [], blocked_origins: [], api: {}, store: {}, errors: [] };
let network = [], scenarioName = '', sequence = 0;
const scopes = new Set(['name_prefix', 'agent', 'since', 'session_id', 'limit']);
const limit = 256 * 1024;
function locator(page, target) {
  assert.ok(target && typeof target.role === 'string' && typeof target.name === 'string');
  return page.getByRole(target.role, { name: target.name, exact: true });
}
async function action(page, step) {
  if (step.action === 'click') await locator(page, step.target).click({ timeout: 5000 });
  else if (step.action === 'select') await locator(page, step.target).selectOption(step.value, { timeout: 5000 });
  else if (step.action === 'focus') await locator(page, step.target).focus({ timeout: 5000 });
  else if (step.action === 'press') await page.keyboard.press(step.key);
  else if (step.action === 'wait') await locator(page, step.target).waitFor({ state: 'visible', timeout: 5000 });
  else throw new Error(`unsupported driver action ${step.action}`);
}
async function actions(page, steps = []) { for (const step of steps) await action(page, step); }
async function snapshot(page, label) {
  // Wait for a quiet rendering interval, bounded independently of labels/text.
  // Keep the bound in evidence; this is not an assertion of product correctness.
  const settling = await page.evaluate(() => new Promise(resolve => {
    let quiet;
    const finish = reason => { clearTimeout(quiet); clearTimeout(maximum); observer.disconnect(); resolve(reason); };
    const observer = new MutationObserver(() => { clearTimeout(quiet); quiet = setTimeout(() => finish('quiet-150ms'), 150); });
    const maximum = setTimeout(() => finish('maximum-2000ms'), 2000);
    observer.observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
    quiet = setTimeout(() => finish('quiet-150ms'), 150);
  }));
  const prefix = `${String(sequence++).padStart(3, '0')}-${scenarioName}-${label}`;
  const text = await page.locator('body').innerText();
  const aria = await page.locator('body').ariaSnapshot();
  const html = await page.locator('body').innerHTML();
  assert.ok(Buffer.byteLength(text) < limit && Buffer.byteLength(aria) < limit && Buffer.byteLength(html) < 2 * 1024 * 1024,
    'snapshot exceeds evidence budget');
  fs.writeFileSync(path.join(output, `${prefix}.txt`), text);
  fs.writeFileSync(path.join(output, `${prefix}.aria.txt`), aria);
  fs.writeFileSync(path.join(output, `${prefix}.html`), html);
  const screenshotRetained = retainScreenshot(scenarioName, label);
  if (screenshotRetained) {
    await page.screenshot({ path: path.join(output, `${prefix}.png`), fullPage: false });
  }
  const state = await page.evaluate(() => ({ url: location.href, viewport: { width: innerWidth, height: innerHeight },
    scrollWidth: document.documentElement.scrollWidth,
    focused: { tag: document.activeElement?.tagName, text: document.activeElement?.textContent?.slice(0, 300),
      label: document.activeElement?.getAttribute('aria-label') } }));
  return { label, prefix, settling, screenshot_retained: screenshotRetained,
    screenshot_policy: 'initial-fixture-empty-narrow-only-v1', ...state };
}
function store(db) {
  const tables = db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").all();
  return tables.map(({ name }) => {
    const rows = db.prepare(`SELECT * FROM "${name.replaceAll('"', '""')}"`).all();
    // Sort complete row serializations so incidental query order is immaterial.
    const data = rows.map(row => JSON.stringify(row)).sort();
    return { table: name, rows: data.length, sha256: digest(JSON.stringify(data)) };
  });
}
try {
  initSchema();
  const db = getDb();
  assert.equal(fs.realpathSync(db.name), fs.realpathSync(dbPath));
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM otel_metrics').get().n, 0);
  const insert = db.prepare(`INSERT INTO otel_metrics
    (session_id,agent_type,metric_name,attrs,value,temporality,client_timestamp)
    VALUES (@session_id,@agent_type,@metric_name,@attrs,@value,@temporality,@client_timestamp)`);
  db.transaction(() => { for (const row of fixture.rows) insert.run({ ...row, attrs: row.attrs ? JSON.stringify(row.attrs) : null }); })();
  server = createApp().listen(0, '127.0.0.1');
  await once(server, 'listening');
  const base = `http://127.0.0.1:${server.address().port}`;
  const read = async route => {
    const response = await fetch(`${base}${route}`);
    const body = await response.json();
    return { route, status: response.status, body };
  };
  observation.api.before = await Promise.all(regressionRoutes.map(read));
  assert.ok(observation.api.before.every(row => row.status === 200), 'regression API control not ready');
  observation.api.positive = await read(`/api/v2/metrics?name_prefix=codex.memory.&agent=codex&since=${encodeURIComponent(fixture.since)}`);
  assert.equal(observation.api.positive.status, 200);
  assert.ok(observation.api.positive.body.metrics.some(row => row.occurrences === 2 && row.total_value === 5), 'real API positive grouping control failed');
  observation.api.empty = await read('/api/v2/metrics?name_prefix=fixture.missing.');
  assert.deepEqual(observation.api.empty.body, { metrics: [] });
  observation.store.before = store(db);
  browser = await launchPatchedChromium({ headless: true });
  observation.browser = browser.version();
  // Each scenario begins in a fresh real console. The trusted plan describes
  // affordances, never expected words, outcomes or a generated-spec answer key.
  for (const mode of ['real', 'fixture', 'loading', 'empty', 'initial-error', 'refresh-error-recovery', 'bounded', 'race', 'range-transition', 'narrow-keyboard']) {
    scenarioName = mode;
    const scenario = { id: mode, snapshots: [], status: 'incomplete' };
    observation.scenarios.push(scenario);
    if (mode === 'range-transition') {
      scenario.range_binding = rangeTransition;
      if (!rangeTransition.applicable) {
        scenario.status = 'not-applicable';
        scenario.reason = rangeTransition.reason;
        continue;
      }
    }
    context = await browser.newContext({ viewport: { width: mode === 'narrow-keyboard' ? 390 : 1280, height: 800 } });
    const page = await context.newPage();
    await page.clock.setFixedTime(new Date('2026-11-07T12:05:00Z'));
    page.on('pageerror', error => observation.page_errors.push({ scenario: mode, message: error.message }));
    const requests = [];
    const generations = new ReadGenerations();
    const deferred = new Map();
    const initial = generations.begin('initial', { deferred: mode === 'loading' || mode === 'race' });
    const requestRecords = new Map();
    page.on('requestfailed', request => {
      const row = requestRecords.get(request);
      if (row) generations.complete(row, request.failure() || { errorText: 'requestfailed' });
      network.push({ event: 'requestfailed', scenario: mode, request_ordinal: row?.ordinal,
        generation: row?.generation, phase: row?.phase, url: request.url(), failure: request.failure() });
    });
    const inflight = [];
    page.on('response', response => {
      if (new URL(response.url()).pathname !== '/api/v2/metrics') return;
      const task = (async () => {
        const requestRow = requestRecords.get(response.request());
        const row = { scenario: mode, request_ordinal: requestRow?.ordinal, generation: requestRow?.generation,
          phase: requestRow?.phase, url: response.url(), status: response.status() };
        try { row.body = await response.json(); } catch (error) { row.error = error.message; }
        network.push({ event: 'response', ...row });
        if (requestRow) generations.complete(requestRow);
      })();
      inflight.push(task);
    });
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      if (url.origin !== base) { observation.blocked_origins.push(url.origin); await route.abort('blockedbyclient'); return; }
      const networkRow = { event: 'request', scenario: mode, method: request.method(), url: request.url(),
        post_data: request.postData(), resource_type: request.resourceType() };
      network.push(networkRow);
      if (url.pathname !== '/api/v2/metrics') return route.continue();
      const requestRow = { ordinal: requests.length + 1, method: request.method(),
        query: Object.fromEntries(url.searchParams), url: request.url() };
      requests.push(requestRow);
      requestRecords.set(request, requestRow);
      const generation = generations.record(requestRow);
      Object.assign(networkRow, { request_ordinal: requestRow.ordinal, generation: generation.id, phase: generation.phase });
      if (mode === 'real') return route.continue();
      if (generation.deferred) {
        deferred.set(requestRow, route);
        generations.defer(requestRow, () => supply(route, requestRow, generation));
        return;
      }
      await supply(route, requestRow, generation);
    });
    const supply = async (route, row, generation) => {
      deferred.delete(row);
      const supplied = fixtureResponse(contract.api_fixture.metrics, mode,
        route.request().url(), generation.phase, { revision: generation.revision, rangeWindows: rangeTransition.windows });
      if (supplied.supported === false) {
        (scenario.fixture_scope_errors ||= []).push({ request: route.request().url(), reason: supplied.reason });
        network.push({ event: 'fixture-unsupported', scenario: mode, generation: generation.id, request_ordinal: row.ordinal,
          request: route.request().url(), reason: supplied.reason });
        await route.abort('blockedbyclient');
        return;
      }
      const { status, body } = supplied;
      network.push({ event: 'fixture', scenario: mode, generation: generation.id, phase: generation.phase, request_ordinal: row.ordinal,
        request: route.request().url(), status, body });
      try { await route.fulfill({ status, json: body }); }
      catch (error) {
        const failure = row.failure || route.request().failure();
        if (!failure) throw error;
        network.push({ event: 'deferred-read-canceled', scenario: mode, generation: generation.id, request_ordinal: row.ordinal,
          request: route.request().url(), failure, detail: error.message });
      }
    };
    // Releasing a whole generation opens automatic fulfillment for its later
    // sequential companions before waiting for all responses and quiet arrivals.
    const release = (generation, revision = 0) => generations.release(generation, revision);
    const interactRead = async (phase, steps, requireRequests = true) => {
      const generation = generations.begin(phase);
      await actions(page, steps);
      await generations.wait(generation, { requireRequests });
      return generation;
    };
    try {
      await page.goto(`${base}/app/`, { waitUntil: 'domcontentloaded' });
      await actions(page, driver.enter);
      await page.waitForFunction(() => document.readyState === 'complete');
      // Arrival and completion cover every request, including sequential
      // companions, until the declared quiet interval. No fixed fan-out count.
      if (mode === 'loading') {
        await generations.wait(initial, { pending: true });
        scenario.snapshots.push(await snapshot(page, 'pending'));
        await release(initial);
      } else if (mode === 'race') {
        if (!driver.range?.length) { scenario.status = 'not-applicable'; scenario.reason = 'no range interaction declared; semantic review must verify applicability'; continue; }
        await generations.wait(initial, { pending: true });
        const newer = generations.begin('newer-first', { deferred: true });
        await actions(page, driver.range);
        await generations.wait(newer, { pending: true });
        await release(newer, 1);
        scenario.snapshots.push(await snapshot(page, 'newer-first'));
        await release(initial);
      } else {
        await generations.wait(initial);
      }
      scenario.snapshots.push(await snapshot(page, 'initial'));
      if (driver.inspect?.length) {
        await interactRead('detail', driver.inspect, false);
        scenario.snapshots.push(await snapshot(page, 'detail'));
      }
      if (mode === 'initial-error') {
        await interactRead('initial-recovered', driver.retry || driver.refresh);
        scenario.snapshots.push(await snapshot(page, 'initial-recovered'));
      }
      if (mode === 'fixture') {
        await interactRead('refreshed', driver.refresh);
        scenario.snapshots.push(await snapshot(page, 'refreshed'));
      }
      if (mode === 'refresh-error-recovery') {
        for (const label of ['refresh-failed', 'recovered']) {
          await interactRead(label, label === 'recovered' ? (driver.retry || driver.refresh) : driver.refresh);
          scenario.snapshots.push(await snapshot(page, label));
        }
      }
      if (mode === 'range-transition') {
        await interactRead('wide-loaded', driver.range);
        scenario.snapshots.push(await snapshot(page, 'wide-loaded'));
        const narrow = generations.begin('narrow-failed', { deferred: true });
        await actions(page, driver.range_return);
        await generations.wait(narrow, { pending: true });
        scenario.snapshots.push(await snapshot(page, 'narrow-pending'));
        await release(narrow);
        scenario.snapshots.push(await snapshot(page, 'narrow-failed'));
        await interactRead('narrow-recovered', driver.retry || driver.refresh);
        scenario.snapshots.push(await snapshot(page, 'narrow-recovered'));
      }
      if (mode === 'fixture' || mode === 'real') {
        await interactRead('away', driver.away, false);
        scenario.snapshots.push(await snapshot(page, 'away'));
        await interactRead('returned', driver.return, false);
        scenario.snapshots.push(await snapshot(page, 'returned'));
      }
      if (mode === 'narrow-keyboard') {
        for (let n = 0; n < 16; n++) { await page.keyboard.press('Tab'); scenario.snapshots.push(await snapshot(page, `tab-${n}`)); }
        if (driver.keyboard?.length) {
          await interactRead('keyboard-operated', driver.keyboard);
          scenario.keyboard_activation = 'consumer-read-observed';
          scenario.snapshots.push(await snapshot(page, 'keyboard-operated'));
        } else {
          scenario.keyboard_activation = 'not-configured';
        }
      }
      assert.ok(!scenario.fixture_scope_errors?.length, 'unsupported fixture scope; this observation cannot establish product behavior');
      assert.ok(generations.generations.every(item => item.lateRequests === 0), 'metrics requests arrived after their generation quiet bound; observation incomplete');
      scenario.status = 'observed';
    } catch (error) { scenario.error = error.stack;
      try { scenario.snapshots.push(await snapshot(page, 'failure')); } catch (capture) { scenario.capture_error = capture.message; }
    }
    finally {
      scenario.requests = requests;
      scenario.read_generations = generations.describe();
      scenario.query_diagnostics = requests.map(row => ({ method_is_get: row.method === 'GET',
        unsupported_keys: Object.keys(row.query).filter(key => !scopes.has(key)) }));
      for (const route of deferred.values()) await route.abort().catch(() => {});
      deferred.clear();
      await Promise.allSettled(inflight);
      await context.close(); context = undefined;
    }
  }
  observation.store.after = store(db);
  observation.store.unchanged = JSON.stringify(observation.store.before) === JSON.stringify(observation.store.after);
  observation.api.after = await Promise.all(regressionRoutes.map(read));
  observation.api.regression_unchanged = JSON.stringify(observation.api.before) === JSON.stringify(observation.api.after);
} catch (error) { observation.errors.push(error.stack); }
finally {
  await context?.close(); await browser?.close();
  if (server) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
  closeDb();
  write('network.json', network);
  observation.blocked_origins = [...new Set(observation.blocked_origins)].sort();
  observation.execution_complete = observation.errors.length === 0 && observation.scenarios.length === 10 &&
    observation.scenarios.every(row => ['observed', 'not-applicable'].includes(row.status));
  write('observation.json', observation);
}
console.log(JSON.stringify({ execution_complete: observation.execution_complete, semantic_verdict: 'unassessed', output }));
process.exitCode = observation.execution_complete ? 0 : 1;
