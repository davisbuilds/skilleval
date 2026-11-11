"""Author synthetic downstream candidate controls without executing captured code.

The caller supplies a disposable copy of the pinned baseline. This is operator
material, never a writer/implementer input or independent validation sample.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

COMPONENT = """<script lang="ts">
  import { onMount } from 'svelte';
  type Metric = { metric_name: string; attrs: Record<string, unknown> | null;
    occurrences: number; total_value: number; last_seen: string };
  let rows: Metric[] = $state([]);
  let loading = $state(false);
  let error = $state('');
  let fetched = $state('');
  let hours = $state('24');
  let version = 0;
  const limit = 200;
  async function refresh() {
    const current = ++version;
    loading = true;
    error = '';
    try {
      const since = new Date(Date.now() - Number(hours) * 3600_000).toISOString();
      const response = await fetch('/api/v2/metrics?' + new URLSearchParams({
        name_prefix: 'codex.memory.', agent: 'codex', since, limit: String(limit) }));
      if (!response.ok) throw new Error('Operational reports could not be read');
      const data = await response.json();
      if (current !== version) return;
      rows = data.metrics;
      fetched = new Date().toISOString();
    } catch (cause) {
      if (current === version) error = cause instanceof Error ? cause.message : 'Read failed';
    } finally { if (current === version) loading = false; }
  }
  onMount(() => { void refresh(); });
</script>
<section aria-label="Memory operational reports"
  style="padding:16px;overflow:auto;flex-shrink:0;max-height:55vh">
  <h2>Codex memory reported activity</h2>
  <label>Recent reports
    <select aria-label="Recent reports" value={hours}
      onchange={(event) => {
        hours = event.currentTarget.value;
        rows = [];
        fetched = '';
        void refresh();
      }}>
      <option value="24">Last 24 hours</option><option value="168">Last 7 days</option>
    </select>
  </label>
  <button onclick={() => void refresh()}>Refresh memory reports</button>
  <p>Window: last {hours} hours; lower bound and last event times use UTC.</p>
  {#if loading}<p role="status">Reading operational reports…</p>{/if}
  {#if error}<p role="alert">{error}.
    {rows.length ? 'Displayed reports are stale last-known data.' : 'No current data available.'}
  </p>{/if}
  {#if fetched}<p>Fetched at {fetched}; event time appears separately for each group.</p>{/if}
  {#if !loading && !error && rows.length === 0}<p>EMPTY_MESSAGE</p>{/if}
  <p>Up to {limit} returned groups; a returned slice does not guarantee complete history.
    Counts are stored points, not unique sessions or exact job executions.
    Value sums preserve reported counter values.</p>
  LAYOUT
</section>
"""

TABLE = """<table>
    <thead><tr><th>Metric</th><th>Attributes</th><th>Stored points</th>
      <th>Value sum</th><th>Last event UTC</th></tr></thead>
    <tbody>{#each rows as row}<tr><td>{row.metric_name}</td><td>{JSON.stringify(row.attrs)}</td>
      <td>{row.COUNT}</td><td>{row.VALUE}</td><td>{row.last_seen}</td></tr>{/each}</tbody>
  </table>"""
CARDS = """<div>{#each rows as row}
  <article style="padding:8px;border:1px solid currentColor;margin:6px">
    <h3>{row.metric_name}</h3><p>Attributes: {JSON.stringify(row.attrs)}</p>
    <p>Stored points: {row.COUNT}; Value sum: {row.VALUE}</p><p>Last event UTC: {row.last_seen}</p>
  </article>{/each}</div>"""


def control_bytes(design, mutation):
    if design not in {"monitor-table", "analytics-cards"}:
        raise ValueError("unknown authored control design")
    if mutation not in {"none", "swapped-measures", "false-health", "unwired"}:
        raise ValueError("unknown authored control mutation")
    layout = TABLE if design == "monitor-table" else CARDS
    count, value = (
        ("total_value", "occurrences")
        if mutation == "swapped-measures"
        else ("occurrences", "total_value")
    )
    text = COMPONENT.replace("LAYOUT", layout.replace("COUNT", count).replace("VALUE", value))
    text = text.replace(
        "EMPTY_MESSAGE",
        "Memory is healthy; no rate limit skips."
        if mutation == "false-health"
        else (
            "No matching reports in this window; absence does not establish "
            "exporter coverage or memory health."
        ),
    )
    return text.encode()


def driver(design):
    tab = "Monitor" if design == "monitor-table" else "Analytics"
    other = "Analytics" if tab == "Monitor" else "Monitor"

    def click(name):
        return {"action": "click", "target": {"role": "button", "name": name}}

    return {
        "schema_version": 1,
        "enter": [click(tab)],
        "away": [click(other)],
        "return": [click(tab)],
        "refresh": [click("Refresh memory reports")],
        "retry": [click("Refresh memory reports")],
        "keyboard": [
            {"action": "focus", "target": {"role": "button", "name": "Refresh memory reports"}},
            {"action": "press", "key": "Enter"},
        ],
        "inspect": [],
        "range": [
            {
                "action": "select",
                "target": {"role": "combobox", "name": "Recent reports"},
                "value": "168",
            }
        ],
        "range_return": [
            {
                "action": "select",
                "target": {"role": "combobox", "name": "Recent reports"},
                "value": "24",
            }
        ],
        "range_windows": {"wide_hours": 168, "narrow_hours": 24},
    }


def apply(root, design, mutation):
    app = root / "frontend/src/App.svelte"
    text = app.read_text()
    component = root / "frontend/src/OperatorMemoryControl.svelte"
    if component.exists() or "OperatorMemoryControl" in text:
        raise ValueError("control already exists; start from a fresh baseline copy")
    if not text.startswith('<script lang="ts">'):
        raise ValueError("unexpected baseline App.svelte contract")
    component.write_bytes(control_bytes(design, mutation))
    if mutation != "unwired":
        text = text.replace(
            '<script lang="ts">',
            '<script lang="ts">\n'
            '  import OperatorMemoryControl from "./OperatorMemoryControl.svelte";',
            1,
        )
        target = "<MonitorPage />" if design == "monitor-table" else "<AnalyticsShell />"
        if text.count(target) != 1:
            raise ValueError("unexpected baseline console integration point")
        text = text.replace(target, "<OperatorMemoryControl />\n    " + target)
        app.chmod(app.stat().st_mode | 0o200)
        app.write_text(text)
    return driver(design)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--design", choices=("monitor-table", "analytics-cards"), required=True)
    parser.add_argument(
        "--mutation",
        choices=("none", "swapped-measures", "false-health", "unwired"),
        default="none",
    )
    parser.add_argument("--driver", type=Path, required=True)
    args = parser.parse_args()
    if args.driver.exists():
        raise ValueError("refuse to overwrite a driver")
    result = apply(args.candidate, args.design, args.mutation)
    args.driver.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
