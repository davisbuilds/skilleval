type Category = "human-needed" | "agent-follow-up" | "qualified-unknown";
interface Evidence { id: string; label: string; locator: string; excerpt: string; source_sha256: string }
interface Arm { id: string; label: string; status: string; quality: string[]; usage: string[]; cost: string[]; timing: string[]; evidence: string[] }
interface Exception { id: string; title: string; category: Category; question: string; why_human: string | null; investigation: string[]; impact: string; evidence: string[]; options: string[] }
interface Packet {
  schema_version: 1; packet_id: string; title: string; generated_at: string;
  author: { name: string; role: "agent" | "human"; model: string | null };
  source_spec_sha256: string;
  summary: { headline: string; conclusion: string; scope: string; limitations: string[]; evidence: string[] };
  arms: Arm[]; exceptions: Exception[]; evidence: Evidence[];
  sources: { id: string; label: string; sha256: string }[];
}
interface Decision { response_id: string; packet_id: string; exception_id: string; answer: string; created_at: string }
interface DecisionState { packet_id: string; decisions: Decision[]; session_token: string }

const rootElement = document.querySelector<HTMLElement>("#app");
if (!rootElement) throw new Error("Missing review application root.");
const root = rootElement;
const categories: Record<Category, string> = {
  "human-needed": "Owner input",
  "agent-follow-up": "Agent follow-up",
  "qualified-unknown": "Qualified unknown",
};

function element<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function list(items: string[]): HTMLUListElement {
  const node = element("ul");
  for (const item of items) node.append(element("li", "", item));
  return node;
}

function field(parent: HTMLElement, title: string, items: string[]): void {
  parent.append(element("h4", "", title));
  parent.append(items.length ? list(items) : element("p", "muted", "Not reported."));
}

function timestamp(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, { credentials: "same-origin", ...options });
  if (!response.ok) throw new Error(`The local server returned HTTP ${response.status}.`);
  return await response.json() as T;
}

async function responseState(packet: Packet): Promise<DecisionState> {
  const state = await request<DecisionState>("/api/decisions");
  if (state.packet_id !== packet.packet_id) throw new Error("The response records do not match this packet.");
  return state;
}

function render(packet: Packet, state: DecisionState | null, historyError?: string, drafts = new Map<string, string>()): void {
  const evidence = new Map(packet.evidence.map(item => [item.id, item]));

  function evidenceDetails(ids: string[]): HTMLDetailsElement | null {
    if (!ids.length) return null;
    const details = element("details");
    details.append(element("summary", "", `Evidence · ${ids.length}`));
    for (const id of ids) {
      const item = evidence.get(id);
      const block = element("div", "evidence");
      if (item) {
        block.append(element("h4", "", item.label));
        block.append(element("span", "locator", item.locator));
        block.append(element("pre", "", item.excerpt));
        block.append(element("span", "locator", `Source SHA-256 · ${item.source_sha256}`));
      } else block.append(element("p", "error", `Evidence ${id} is unavailable in this packet.`));
      details.append(block);
    }
    return details;
  }

  function appendEvidence(parent: HTMLElement, ids: string[]): void {
    const details = evidenceDetails(ids);
    if (details) parent.append(details);
  }

  function responseHistory(exceptionId: string): HTMLDivElement {
    const history = element("div");
    const records = state?.decisions.filter(item => item.exception_id === exceptionId) ?? [];
    if (!records.length) return history;
    history.className = "answers";
    history.append(element("h4", "", "Recorded operator responses"));
    history.append(element("p", "muted", "Responses are retained separately. They do not rewrite or independently validate the original assessment."));
    for (const record of records) {
      history.append(element("p", "answer", record.answer));
      history.append(element("p", "muted", timestamp(record.created_at)));
    }
    return history;
  }

  function exceptionCard(item: Exception): HTMLElement {
    const card = element("article", "card question");
    card.append(element("span", `badge ${item.category}`, categories[item.category]));
    card.append(element("h3", "", item.title));
    card.append(element("p", "", item.question));
    card.append(element("p", "impact", item.impact));
    if (item.category === "human-needed") {
      card.append(element("p", "why", `Why owner input is needed: ${item.why_human ?? "No reason was supplied."}`));
    }
    field(card, "Prior investigation", item.investigation);
    appendEvidence(card, item.evidence);
    let history = responseHistory(item.id);
    card.append(history);

    if (item.category === "human-needed") {
      const form = element("form");
      const inputId = `answer-${packet.exceptions.indexOf(item)}`;
      if (item.options.length) {
        const optionsLabel = element("label", "", "Suggested response");
        const select = element("select");
        select.id = `${inputId}-suggested`;
        optionsLabel.htmlFor = select.id;
        const empty = element("option", "", "Choose a suggestion, or write your own");
        empty.value = "";
        select.append(empty);
        for (const option of item.options) select.append(element("option", "", option));
        select.addEventListener("change", () => { if (select.value) { input.value = select.value; drafts.set(item.id, select.value); } });
        form.append(optionsLabel, select);
      }
      const label = element("label", "", "Your response");
      label.htmlFor = inputId;
      const input = element("textarea");
      input.id = inputId;
      input.name = "answer";
      input.required = true;
      input.maxLength = 10000;
      input.value = drafts.get(item.id) ?? "";
      const hint = element("p", "muted", "Saving records your answer; the original packet remains unchanged.");
      const button = element("button", "", "Save response");
      button.type = "submit";
      button.disabled = !state;
      const status = element("span", "save-status");
      status.setAttribute("role", "status");
      if (!state) {
        status.className = "save-status error";
        status.textContent = "Response history unavailable; saving disabled.";
      }
      const actions = element("div", "form-actions");
      actions.append(button, status);
      form.append(label, input, hint, actions);
      form.addEventListener("submit", async event => {
        event.preventDefault();
        if (!state) return;
        const submittedDraft = input.value;
        const answer = submittedDraft.trim();
        if (!answer) { input.setCustomValidity("Enter a response before saving."); input.reportValidity(); return; }
        input.setCustomValidity("");
        button.disabled = true;
        status.className = "save-status";
        status.textContent = "Saving…";
        try {
          const record = await request<Decision>("/api/decisions", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-Skilleval-Token": state.session_token },
            body: JSON.stringify({ packet_id: packet.packet_id, exception_id: item.id, answer }),
          });
          if (record.packet_id !== packet.packet_id || record.exception_id !== item.id) throw new Error("The saved response did not match this question.");
          state.decisions.push(record);
          const replacement = responseHistory(item.id);
          history.replaceWith(replacement);
          history = replacement;
          if (input.value === submittedDraft) {
            input.value = "";
            drafts.delete(item.id);
            status.textContent = "Response saved.";
          } else status.textContent = "Response saved. Your newer draft is still here.";
        } catch (error) {
          status.className = "save-status error";
          status.textContent = `Could not save. Your draft is still here. ${error instanceof Error ? error.message : "Retry when the local server is available."}`;
        } finally { button.disabled = false; }
      });
      input.addEventListener("input", () => { input.setCustomValidity(""); drafts.set(item.id, input.value); });
      card.append(form);
    }
    return card;
  }

  root.replaceChildren();
  document.title = `${packet.title} · Skilleval`;
  root.append(element("div", "eyebrow", "RETAINED EVIDENCE / ATTRIBUTED SYNTHESIS"));
  root.append(element("h1", "", packet.title));
  const attribution = `${packet.author.name} · ${packet.author.role}${packet.author.model ? ` · ${packet.author.model}` : ""} · ${timestamp(packet.generated_at)}`;
  root.append(element("p", "muted", attribution));
  const human = packet.exceptions.filter(item => item.category === "human-needed");
  const followups = packet.exceptions.filter(item => item.category === "agent-follow-up").length;
  const unknowns = packet.exceptions.filter(item => item.category === "qualified-unknown").length;
  const triage = element("div", "triage");
  triage.setAttribute("role", "group");
  triage.setAttribute("aria-label", "Review triage");
  triage.append(element("span", "badge human-needed", human.length ? `${human.length} owner question${human.length === 1 ? "" : "s"}` : "No human input required"));
  triage.append(element("span", "badge agent-follow-up", `${followups} agent follow-up${followups === 1 ? "" : "s"}`));
  triage.append(element("span", "badge qualified-unknown", `${unknowns} qualified unknown${unknowns === 1 ? "" : "s"}`));
  root.append(triage);
  if (!state) {
    const warning = element("section", "history-error");
    warning.setAttribute("role", "alert");
    warning.append(element("h2", "", "Response history is unavailable"));
    warning.append(element("p", "", "The immutable results and evidence remain available below. Existing responses are unknown, and saving is disabled until history loads successfully."));
    warning.append(element("p", "muted", historyError ?? "The local server could not load the response history."));
    const retry = element("button", "", "Retry response history");
    retry.addEventListener("click", async () => {
      retry.disabled = true;
      retry.textContent = "Retrying…";
      try { render(packet, await responseState(packet), undefined, drafts); }
      catch (error) { render(packet, null, error instanceof Error ? error.message : "Response history remains unavailable.", drafts); }
    });
    warning.append(retry);
    root.append(warning);
  }

  const hero = element("section", "hero");
  hero.append(element("div", "eyebrow", "CONCLUSION"));
  hero.append(element("h2", "", packet.summary.headline));
  hero.append(element("p", "", packet.summary.conclusion));
  hero.append(element("h4", "", "Scope"), element("p", "", packet.summary.scope));
  if (packet.summary.limitations.length) {
    const limits = element("details");
    limits.append(element("summary", "", `Limits on this conclusion · ${packet.summary.limitations.length}`));
    limits.append(list(packet.summary.limitations));
    hero.append(limits);
  }
  hero.append(element("p", "boundary", "Authored synthesis preserves original judgments; independent reliability and skill effectiveness require separate evidence."));
  appendEvidence(hero, packet.summary.evidence);
  root.append(hero);

  const results = element("section", "section");
  results.append(element("h2", "", "Comparison at a glance"));
  const arms = element("div", "arms");
  for (const arm of packet.arms) {
    const card = element("article", "card arm");
    const heading = element("h3", "", arm.label);
    heading.append(element("span", "badge", arm.status));
    card.append(heading);
    field(card, "Quality", arm.quality);
    field(card, "Usage", arm.usage);
    field(card, "Cost", arm.cost);
    field(card, "Timing", arm.timing);
    appendEvidence(card, arm.evidence);
    arms.append(card);
  }
  if (!packet.arms.length) arms.append(element("p", "muted", "No arm summaries were supplied."));
  results.append(arms);
  root.append(results);

  const inbox = element("section", "section");
  const heading = element("div", "section-heading");
  heading.append(element("h2", "", "Material owner questions"), element("span", "badge human-needed", `${human.length}`));
  inbox.append(heading);
  if (!human.length) inbox.append(element("p", "empty", "No human input is required for this packet."));
  else {
    inbox.append(element("p", "muted", "Only questions that need owner judgment appear here. Responses are recorded without changing the retained assessment."));
    for (const item of human) inbox.append(exceptionCard(item));
  }
  for (const category of ["agent-follow-up", "qualified-unknown"] as const) {
    const items = packet.exceptions.filter(item => item.category === category);
    if (!items.length) continue;
    const group = element("details", "group");
    group.append(element("summary", "", `${categories[category]} · ${items.length}`));
    group.append(element("p", "muted", category === "agent-follow-up" ? "Further investigation can proceed without owner input." : "These limits remain explicit; they are not assigned as owner homework."));
    for (const item of items) group.append(exceptionCard(item));
    inbox.append(group);
  }
  root.append(inbox);

  const provenance = element("details", "section");
  provenance.append(element("summary", "", "Packet provenance"));
  provenance.append(element("p", "provenance", `Packet · ${packet.packet_id}`));
  provenance.append(element("p", "provenance", `Source specification SHA-256 · ${packet.source_spec_sha256}`));
  const sources = element("ul", "source-list");
  for (const source of packet.sources) sources.append(element("li", "", `${source.id} · ${source.label} · SHA-256 ${source.sha256}`));
  provenance.append(sources);
  root.append(provenance);
}

async function load(): Promise<void> {
  try {
    const packet = await request<Packet>("/api/packet");
    if (packet.schema_version !== 1) throw new Error("The local packet uses an unsupported schema version.");
    try { render(packet, await responseState(packet)); }
    catch (error) { render(packet, null, error instanceof Error ? error.message : "Response history could not be loaded."); }
  } catch (error) {
    root.replaceChildren(element("h1", "", "Local results could not be loaded"));
    root.append(element("p", "load-error", error instanceof Error ? error.message : "The local review server is unavailable."));
    const retry = element("button", "", "Retry");
    retry.addEventListener("click", () => { retry.disabled = true; void load(); });
    root.append(retry);
  }
}

void load();
