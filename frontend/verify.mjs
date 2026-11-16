// Optional real-browser check. Supply an installed Playwright module via
// SKILLEVAL_PLAYWRIGHT_MODULE, or install it in the verification environment.
// No browser package is needed to build, package, or use the review UI.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);
let modulePath = process.env.SKILLEVAL_PLAYWRIGHT_MODULE;
if (!modulePath) {
  try { modulePath = require.resolve("playwright"); }
  catch { throw new Error("Set SKILLEVAL_PLAYWRIGHT_MODULE to an installed Playwright module; this optional check does not download tools."); }
}
const { chromium } = await import(pathToFileURL(resolve(modulePath)).href);
const hash = "a".repeat(64);
const longestSuggestion = "L".repeat(10000);
const packet = {
  schema_version: 1, packet_id: "synthetic-packet", title: "Synthetic local comparison",
  generated_at: "2026-01-01T00:00:00Z", author: { name: "Fixture analyst", role: "agent", model: "synthetic" },
  source_spec_sha256: hash,
  summary: { headline: "A bounded result, with one owner question", conclusion: "No effectiveness claim follows from this fixture.", scope: "One synthetic comparison.", limitations: ["Independent reliability remains unmeasured."], evidence: ["e1"] },
  arms: [{ id: "off", label: "Skill OFF", status: "reviewed scope only", quality: ["One observed requirement satisfied"], usage: ["Unknown usage"], cost: ["Unknown cost"], timing: ["One minute"], evidence: ["e1"] }],
  exceptions: [
    { id: "owner", title: "Choose intended scope", category: "human-needed", question: "Should the expanded scope count?", why_human: "Only the owner knows the intended scope.", investigation: ["Compared both supplied requirements."], impact: "Changes the scoped conclusion.", evidence: ["e1"], options: ["Keep original scope", "Expand scope", longestSuggestion] },
    { id: "agent", title: "Inspect retained trace", category: "agent-follow-up", question: "Does the retained trace answer the timing gap?", why_human: null, investigation: [], impact: "Timing remains incomplete.", evidence: [], options: [] },
    { id: "unknown", title: "Unavailable usage", category: "qualified-unknown", question: "Canonical usage was not retained.", why_human: null, investigation: ["Checked available receipts."], impact: "Total cost cannot be concluded.", evidence: [], options: [] }
  ],
  evidence: [{ id: "e1", label: "Captured excerpt", locator: "private/example.json:3", excerpt: '<img src=x onerror="window.__evidenceExecuted=true">', source_sha256: hash }],
  sources: [{ id: "source1", label: "Retained result", sha256: hash }]
};
const decisions = [];
let rejectSave = false;
let rejectHistory = false;
let delayedSave = null;
const server = createServer(async (request, response) => {
  const send = (status, body) => { response.writeHead(status, { "Content-Type": "application/json" }); response.end(JSON.stringify(body)); };
  if (request.url === "/favicon.ico") { response.writeHead(204); response.end(); return; }
  if (request.url === "/api/packet") return send(200, packet);
  if (request.url === "/api/decisions" && request.method === "GET") return rejectHistory ? send(500, { error: "Fixture history unavailable" }) : send(200, { packet_id: packet.packet_id, decisions, session_token: "fixture-session" });
  if (request.url === "/api/decisions" && request.method === "POST") {
    if (rejectSave) return send(500, { error: "Fixture storage unavailable" });
    assert.equal(request.headers["x-skilleval-token"], "fixture-session");
    let content = "";
    for await (const chunk of request) content += chunk;
    const body = JSON.parse(content);
    assert.equal(body.packet_id, packet.packet_id);
    assert.equal(body.exception_id, "owner");
    if (delayedSave) {
      const gate = delayedSave;
      delayedSave = null;
      gate.started();
      await gate.wait;
    }
    const record = { ...body, response_id: `response-${decisions.length}`, created_at: "2026-01-01T01:00:00Z" };
    decisions.push(record);
    return send(201, record);
  }
  const assets = { "/": ["index.html", "text/html"], "/app.js": ["app.js", "text/javascript"], "/style.css": ["style.css", "text/css"] };
  const asset = assets[request.url];
  if (!asset) { response.writeHead(404); response.end(); return; }
  response.writeHead(200, { "Content-Type": asset[1] });
  response.end(await readFile(join(root, "src/skilleval/review_ui", asset[0])));
});
await new Promise(resolveReady => server.listen(0, "127.0.0.1", resolveReady));
const url = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ headless: true, channel: process.env.SKILLEVAL_BROWSER_CHANNEL });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  const remoteRequests = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("request", request => { if (!request.url().startsWith(url + "/")) remoteRequests.push(request.url()); });
  await page.goto(url);
  await page.getByRole("heading", { name: packet.title, exact: true }).waitFor();
  const triage = page.getByRole("group", { name: "Review triage" });
  assert.match(await triage.textContent(), /1 owner question.*1 agent follow-up.*1 qualified unknown/);
  assert.equal(await page.getByText("Limits on this conclusion · 1", { exact: true }).count(), 1);
  assert.equal(await page.getByText("Unknown cost", { exact: true }).count(), 1);
  assert.equal(await page.getByLabel("Your response").count(), 1, "Only material human questions accept responses");
  await page.getByRole("heading", { name: "Choose intended scope", exact: true }).waitFor();
  await page.getByText("Evidence · 1", { exact: true }).first().click();
  assert.ok((await page.locator("pre").first().textContent()).includes("<img"));
  assert.equal(await page.locator("img").count(), 0);
  assert.equal(await page.evaluate(() => window.__evidenceExecuted), undefined);
  await page.getByLabel("Your response").fill("Keep the original scope.");
  await page.getByRole("button", { name: "Save response", exact: true }).click();
  await page.getByText("Response saved.", { exact: true }).waitFor();
  assert.equal(decisions.length, 1);
  if (process.env.SKILLEVAL_UI_SCREENSHOT) await page.screenshot({ path: process.env.SKILLEVAL_UI_SCREENSHOT, fullPage: true });
  await page.reload();
  await page.getByText("Keep the original scope.", { exact: true }).waitFor();
  for (const mode of ["typed", "selected"]) {
    let releaseSave;
    let reportStarted;
    const started = new Promise(resolveStarted => { reportStarted = resolveStarted; });
    delayedSave = { started: reportStarted, wait: new Promise(resolveSave => { releaseSave = resolveSave; }) };
    const submitted = `Submitted response before a ${mode} draft.`;
    await page.getByLabel("Your response").fill(submitted);
    await page.getByRole("button", { name: "Save response", exact: true }).click();
    await started;
    const expectedDraft = mode === "typed" ? "New draft typed while the save is in flight." : longestSuggestion;
    if (mode === "typed") await page.getByLabel("Your response").fill(expectedDraft);
    else await page.getByLabel("Suggested response").selectOption({ index: 3 });
    releaseSave();
    await page.getByText(submitted, { exact: true }).waitFor();
    assert.equal(await page.getByLabel("Your response").inputValue(), expectedDraft, `A successful save must preserve a newer ${mode} draft`);
    await page.getByText("Response saved. Your newer draft is still here.", { exact: true }).waitFor();
  }
  assert.equal(decisions.length, 3);
  await page.getByRole("button", { name: "Save response", exact: true }).click();
  await page.getByText("Response saved.", { exact: true }).waitFor();
  assert.equal(decisions.length, 4);
  assert.equal(decisions[3].answer.length, 10000, "The longest valid suggestion must save without truncation");
  assert.equal(await page.getByLabel("Your response").inputValue(), "");
  rejectSave = true;
  await page.getByLabel("Your response").fill("Draft retained after a failed save.");
  await page.getByRole("button", { name: "Save response", exact: true }).click();
  await page.getByText(/Could not save/).waitFor();
  assert.equal(await page.getByLabel("Your response").inputValue(), "Draft retained after a failed save.");
  assert.equal(decisions.length, 4);
  rejectHistory = true;
  await page.reload();
  await page.getByRole("heading", { name: "Response history is unavailable", exact: true }).waitFor();
  assert.equal(await page.getByRole("heading", { name: packet.title, exact: true }).count(), 1);
  assert.equal(await page.getByText("Unknown cost", { exact: true }).count(), 1);
  assert.ok(await page.getByRole("button", { name: "Save response", exact: true }).isDisabled());
  assert.equal(await page.getByText("Keep the original scope.", { exact: true }).count(), 0);
  await page.getByLabel("Your response").fill("Draft preserved while history is unavailable.");
  rejectHistory = false;
  await page.getByRole("button", { name: "Retry response history", exact: true }).click();
  await page.getByText("Keep the original scope.", { exact: true }).waitFor();
  assert.equal(await page.getByLabel("Your response").inputValue(), "Draft preserved while history is unavailable.");
  assert.ok(await page.getByRole("button", { name: "Save response", exact: true }).isEnabled());
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), "Mobile screen must not overflow horizontally");
  packet.exceptions = [];
  await page.reload();
  await page.getByText("No human input is required for this packet.", { exact: true }).waitFor();
  assert.match(await triage.textContent(), /No human input required.*0 agent follow-ups.*0 qualified unknowns/);
  assert.equal(await page.getByLabel("Your response").count(), 0);
  assert.deepEqual(errors, []);
  assert.deepEqual(remoteRequests, [], "Review UI must not request remote assets or services");
  console.log("Review UI browser checks passed: summary, triage, literal evidence, persisted responses, failed-save draft, history failure/retry, empty inbox, mobile layout.");
} finally {
  await browser.close();
  await new Promise(resolveClosed => server.close(resolveClosed));
}
