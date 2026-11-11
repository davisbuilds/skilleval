// Trusted operator observer. Execute only through the isolated guest runner.
// Candidate code is imported only after the guest identity/state checks.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { once } from 'node:events';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { regressionRoutes, fixtureResponse, retainScreenshot, rangeTransitionBinding } from './scenarios.mjs';

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
  fixture_contract_sha256: digest(fs.readFileSync(contractPath)), driver_sha256: digest(fs.readFileSync(driverPath)),
  scenarios: [], page_errors: [], blocked_origins: [], api: {}, store: {}, errors: [] };
let network = [], pending = [], scenarioName = '', sequence = 0;
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
    const requestRecords = new Map();
    page.on('requestfailed', request => {
      const row = requestRecords.get(request);
      if (row) row.failure = request.failure();
      network.push({ event: 'requestfailed', scenario: mode, url: request.url(), failure: request.failure() });
    });
    const inflight = [];
    page.on('response', response => {
      if (new URL(response.url()).pathname !== '/api/v2/metrics') return;
      const task = (async () => {
        const row = { scenario: mode, url: response.url(), status: response.status() };
        try { row.body = await response.json(); } catch (error) { row.error = error.message; }
        network.push({ event: 'response', ...row });
        const requestRow = requestRecords.get(response.request());
        if (requestRow) requestRow.completed = true;
      })();
      inflight.push(task);
    });
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      if (url.origin !== base) { observation.blocked_origins.push(url.origin); await route.abort('blockedbyclient'); return; }
      network.push({ event: 'request', scenario: mode, method: request.method(), url: request.url(),
        post_data: request.postData(), resource_type: request.resourceType() });
      if (url.pathname !== '/api/v2/metrics') return route.continue();
      const requestRow = { ordinal: requests.length + 1, method: request.method(),
        query: Object.fromEntries(url.searchParams), url: request.url() };
      requests.push(requestRow);
      requestRecords.set(request, requestRow);
      if (mode === 'real') return route.continue();
      if (mode === 'loading' || mode === 'race' ||
          (mode === 'range-transition' && requests.length === 3)) {
        pending.push(route); return;
      }
      const supplied = fixtureResponse(contract.api_fixture.metrics, mode, request.url(), requests.length, 0, rangeTransition.windows);
      network.push({ event: 'fixture', scenario: mode, request: request.url(), ...supplied });
      await route.fulfill({ status: supplied.status, json: supplied.body });
    });
    const release = async (index, revision, allowCanceled = false, supplied = null) => {
      const route = pending.splice(index, 1)[0];
      assert.ok(route, 'requested deferred read did not occur');
      const { status, body } = supplied || fixtureResponse(contract.api_fixture.metrics, 'deferred', route.request().url(), 1, revision);
      network.push({ event: 'fixture', scenario: mode, request: route.request().url(), status, body });
      try { await route.fulfill({ status, json: body }); }
      catch (error) {
        if (!allowCanceled || !route.request().failure()) throw error;
        network.push({ event: 'deferred-read-canceled', scenario: mode, request: route.request().url(),
          failure: route.request().failure(), detail: error.message });
      }
    };
    const waitForRead = async count => {
      const end = Date.now() + 5000;
      while (Date.now() < end) {
        if (requests.length > count && (requests[count].completed || requests[count].failure)) return;
        await page.waitForTimeout(25);
      }
      throw new Error('consumer read did not complete within observation deadline');
    };
    const interactRead = async steps => {
      const count = requests.length;
      await actions(page, steps);
      await waitForRead(count);
    };
    try {
      await page.goto(`${base}/app/`, { waitUntil: 'domcontentloaded' });
      await actions(page, driver.enter);
      await page.waitForFunction(() => document.readyState === 'complete');
      // Read arrival is part of the wiring check, rather than a helper call.
      const deadline = Date.now() + 5000;
      while (!requests.length && Date.now() < deadline) await page.waitForTimeout(25);
      assert.ok(requests.length, 'no consumer metrics read reached the actual console');
      if (mode === 'loading') {
        scenario.snapshots.push(await snapshot(page, 'pending'));
        await release(0, 0);
        await waitForRead(0);
      } else if (mode === 'race') {
        // Optional only when a design has range changes that can overlap.
        if (!driver.range?.length) { scenario.status = 'not-applicable'; scenario.reason = 'no range interaction declared; semantic review must verify applicability'; continue; }
        await actions(page, driver.range);
        const end = Date.now() + 5000;
        while (pending.length < 2 && Date.now() < end) await page.waitForTimeout(25);
        assert.ok(pending.length >= 2, 'range interaction did not issue an overlapping read');
        const newerIndex = requests.length - 1;
        await release(pending.length - 1, 1);
        await waitForRead(newerIndex);
        scenario.snapshots.push(await snapshot(page, 'newer-first'));
        await release(0, 0, true);
        await waitForRead(0);
      } else {
        await waitForRead(0);
      }
      scenario.snapshots.push(await snapshot(page, 'initial'));
      await actions(page, driver.inspect);
      if (driver.inspect?.length) scenario.snapshots.push(await snapshot(page, 'detail'));
      if (mode === 'initial-error') {
        await interactRead(driver.retry || driver.refresh);
        scenario.snapshots.push(await snapshot(page, 'initial-recovered'));
      }
      if (mode === 'fixture') {
        await interactRead(driver.refresh);
        scenario.snapshots.push(await snapshot(page, 'refreshed'));
      }
      if (mode === 'refresh-error-recovery') {
        for (const label of ['refresh-failed', 'recovered']) {
          await interactRead(label === 'recovered' ? (driver.retry || driver.refresh) : driver.refresh);
          scenario.snapshots.push(await snapshot(page, label));
        }
      }
      if (mode === 'range-transition') {
        await interactRead(driver.range);
        scenario.snapshots.push(await snapshot(page, 'wide-loaded'));
        const count = requests.length;
        await actions(page, driver.range_return);
        const end = Date.now() + 5000;
        while (!pending.length && Date.now() < end) await page.waitForTimeout(25);
        assert.ok(pending.length, 'narrow range interaction did not issue a deferred read');
        scenario.snapshots.push(await snapshot(page, 'narrow-pending'));
        const failure = fixtureResponse(contract.api_fixture.metrics, mode, pending[0].request().url(), 3);
        await release(0, 0, false, failure);
        await waitForRead(count);
        scenario.snapshots.push(await snapshot(page, 'narrow-failed'));
        await interactRead(driver.retry || driver.refresh);
        scenario.snapshots.push(await snapshot(page, 'narrow-recovered'));
      }
      if (mode === 'fixture' || mode === 'real') {
        await actions(page, driver.away);
        scenario.snapshots.push(await snapshot(page, 'away'));
        await actions(page, driver.return);
        scenario.snapshots.push(await snapshot(page, 'returned'));
      }
      if (mode === 'narrow-keyboard') {
        for (let n = 0; n < 16; n++) { await page.keyboard.press('Tab'); scenario.snapshots.push(await snapshot(page, `tab-${n}`)); }
        if (driver.keyboard?.length) {
          await interactRead(driver.keyboard);
          scenario.keyboard_activation = 'consumer-read-observed';
          scenario.snapshots.push(await snapshot(page, 'keyboard-operated'));
        } else {
          scenario.keyboard_activation = 'not-configured';
        }
      }
      scenario.status = 'observed';
    } catch (error) { scenario.error = error.stack;
      try { scenario.snapshots.push(await snapshot(page, 'failure')); } catch (capture) { scenario.capture_error = capture.message; }
    }
    finally {
      scenario.requests = requests;
      scenario.query_diagnostics = requests.map(row => ({ method_is_get: row.method === 'GET',
        unsupported_keys: Object.keys(row.query).filter(key => !scopes.has(key)) }));
      for (const route of pending.splice(0)) await route.abort().catch(() => {});
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
