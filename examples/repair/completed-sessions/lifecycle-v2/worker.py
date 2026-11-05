"""VM-only lifecycle observations, independent of the frozen six-probe worker.

Importing this authored Python module on the host runs no application code.
Execution requires the offline Linux repair-check boundary; no expected answers are
loaded by this worker. Output describes finite controls, never admission.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

NODE_ADAPTER = r"""
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { pathToFileURL } from 'node:url';
import { zstdCompressSync } from 'node:zlib';

if (process.platform !== 'linux' || process.getuid() !== 65534) {
  throw Error('Requires Linux VM repair-check UID 65534');
}
const source = process.argv[2];
const request = JSON.parse(fs.readFileSync(0, 'utf8'));
console.log = (...args) => console.error(...args);
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'completed-lifecycle-v2-'));
const home = path.join(root, 'codex-home');
const sessions = path.join(home, 'sessions', '2026', '10', '07');
const claude = path.join(root, 'claude');
for (const name of [sessions, path.join(claude, 'projects'),
    ...['home', 'skills', 'executions', 'projects'].map(name => path.join(root, name))]) {
  fs.mkdirSync(name, { recursive: true });
}
process.env.HOME = path.join(root, 'home');
process.env.CODEX_HOME = home;
process.env.AGENTMONITOR_DB_PATH = path.join(root, 'observations.sqlite');
process.env.AGENTMONITOR_AUTO_IMPORT_MINUTES = '0';
process.env.AGENTMONITOR_CLAUDE_DIR = claude;
process.env.AGENTMONITOR_SKILL_CATALOG_DIRS = path.join(root, 'skills');
process.env.AGENTMONITOR_EXECUTIONS_DIR = path.join(root, 'executions');
process.env.AGENTMONITOR_PROJECTS_DIR = path.join(root, 'projects');
const load = relative => import(pathToFileURL(path.join(source, relative)).href);
const encode = records => Buffer.from(records.map(record => JSON.stringify(record))
  .join('\n') + '\n');
const plainPath = fixture => path.join(sessions, fixture.stem + '.jsonl');
const writeArchive = fixture => fs.writeFileSync(plainPath(fixture) + '.zst',
  zstdCompressSync(encode(fixture.full)));
let connection;
try {
  const schema = await load('src/db/schema.ts');
  connection = await load('src/db/connection.ts');
  const imports = await load('src/import/index.ts');
  const watcher = await load('src/watcher/index.ts');
  const totals = await load('src/import/session-total-check.ts');
  schema.initSchema();
  const db = connection.getDb();
  if (fs.realpathSync(db.name) !== fs.realpathSync(process.env.AGENTMONITOR_DB_PATH)) {
    throw Error('Unexpected database outside lifecycle scratch');
  }
  // No force option: the final restoration must go through normal cache checks.
  const ingest = () => {
    const imported = imports.runImport({ source: 'codex', codexDir: home });
    const sync = watcher.syncAllCodexFiles(db, home);
    console.error(JSON.stringify({ diagnostic: 'normal-ingest', imported, sync }));
  };
  const snapshot = fixture => ({
    browser_ids: db.prepare(`SELECT id FROM browsing_sessions
      WHERE file_path IS NOT NULL ORDER BY id`).all().map(row => row.id),
    messages: db.prepare(`SELECT ordinal, role, content FROM messages
      WHERE session_id = ? ORDER BY ordinal`).all(fixture.stem)
      .map(row => ({ ...row, content: JSON.parse(row.content) })),
    usage: db.prepare(`SELECT tokens_in, cache_read_tokens, tokens_out,
      json_extract(metadata, '$._response_id') AS response_id FROM events
      WHERE session_id = ? AND source = 'import' AND event_type = 'llm_response'
      ORDER BY client_timestamp, event_id`).all(fixture.session_id),
  });
  let observation;
  if (request.scenario === 'cached-sibling-handover') {
    const fixture = request.fixture;
    writeArchive(fixture);
    const archiveBefore = fs.readFileSync(plainPath(fixture) + '.zst');
    ingest();
    const initial = snapshot(fixture);
    fs.writeFileSync(plainPath(fixture), encode(fixture.full.slice(0, request.prefix_lines)));
    ingest();
    const shortened = snapshot(fixture);
    fs.rmSync(plainPath(fixture));
    ingest();
    const restored = snapshot(fixture);
    ingest();
    observation = { initial, shortened, restored, repeated: snapshot(fixture),
      archive_unchanged: archiveBefore.equals(fs.readFileSync(plainPath(fixture) + '.zst')) };
  } else if (request.scenario === 'reader-limit') {
    writeArchive(request.archive);
    fs.writeFileSync(plainPath(request.plain), encode(request.plain.full));
    const older = new Date('2026-10-08T12:00:00Z');
    const newer = new Date('2026-10-10T12:00:00Z');
    fs.utimesSync(plainPath(request.plain), older, older);
    fs.utimesSync(plainPath(request.archive) + '.zst', newer, newer);
    ingest();
    const expectedTotals = new Map([request.archive, request.plain].map(fixture => {
      const records = fixture.full.filter(record => record.type === 'token_usage_record');
      const own = records.map(record => record.payload.usage);
      return [fixture.session_id, own.reduce((sum, row) => sum + row.input_tokens
        + row.output_tokens, 0)];
    }));
    const summarize = limit => {
      const result = totals.checkSessionTotals({ source: 'codex', codexDir: home,
        claudeDir: claude, limit });
      console.error(JSON.stringify({ diagnostic: 'reader-limit', limit, result }));
      const rows = result.sessions;
      return { session_count: rows.length,
        checked_count: rows.filter(row => row.status === 'checked').length,
        distinct_sessions: new Set(rows.map(row => row.session_id)).size,
        values_match_fixture: rows.every(row => expectedTotals.has(row.session_id)
          && row.amon === expectedTotals.get(row.session_id)
          && row.harness === expectedTotals.get(row.session_id) && row.ratio === 1) };
    };
    // The one-session observation accepts either checkable session, preserving
    // valid limiting strategies; the two-session observation requires both.
    observation = { limit1: summarize(1), limit2: summarize(2) };
  } else {
    throw Error(`Unknown lifecycle scenario: ${request.scenario}`);
  }
  process.stdout.write(JSON.stringify(observation));
} finally {
  connection?.closeDb();
  fs.rmSync(root, { recursive: true, force: true });
}
"""


def require_guest():
    if sys.platform != "linux" or os.getuid() != 65534:
        raise RuntimeError("Requires Linux VM repair-check UID 65534")


def observe(source, request):
    """Import application modules only after the explicit guest execution guard."""
    require_guest()
    source = Path(source).resolve()
    node = os.environ.get("SKILLEVAL_NODE") or shutil.which("node")
    if node is None:
        raise RuntimeError("Node is required on the check VM PATH")
    loaders = (
        source / "node_modules/tsx/dist/loader.mjs",
        Path("/usr/local/share/task-dependencies/node_modules/tsx/dist/loader.mjs"),
    )
    loader = os.environ.get("SKILLEVAL_TSX_IMPORT") or next(
        (str(candidate) for candidate in loaders if candidate.is_file()), "tsx"
    )
    with tempfile.TemporaryDirectory(prefix="completed-lifecycle-adapter-v2-") as temporary:
        adapter = Path(temporary) / "observe.mjs"
        adapter.write_text(NODE_ADAPTER)
        completed = subprocess.run(
            [node, "--import", loader, str(adapter), str(source)],
            input=json.dumps(request),
            text=True,
            capture_output=True,
            cwd=source,
            timeout=45,
            check=False,
        )
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="")
    if completed.returncode != 0:
        raise RuntimeError(f"Node lifecycle process exited {completed.returncode}")
    return json.loads(completed.stdout)


def main():
    require_guest()
    requests = json.load(sys.stdin)["requests"]
    print(json.dumps({"observations": [observe(sys.argv[1], row) for row in requests]}))


if __name__ == "__main__":
    main()
