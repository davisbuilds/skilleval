"""VM-only behavioral observations for the completed-session development task.

The Python entrypoint uses only the standard library. Its authored Node adapter
imports candidate modules solely when explicitly launched in the check VM. It
does not consume expected outcomes or inspect the candidate's implementation.
Node must be on PATH and the frozen source must have its ordinary dependencies,
including tsx and the native better-sqlite3 module, available to module resolution.
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
import crypto from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { zstdCompressSync } from 'node:zlib';

const source = process.argv[2];
const request = JSON.parse(fs.readFileSync(0, 'utf8'));
// Production logging is retained as diagnostics, never mixed into the protocol.
console.log = (...args) => console.error(...args);
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'completed-sessions-'));
const codexDir = path.join(temporary, 'codex');
const claudeDir = path.join(temporary, 'claude');
const directory = path.join(codexDir, 'sessions', '2026', '09', '18');
const cwd = path.join(temporary, 'Dev', '.worktrees', 'aurora-task');
fs.mkdirSync(path.join(temporary, 'Dev', 'aurora', '.git'), { recursive: true });
fs.mkdirSync(directory, { recursive: true });
fs.mkdirSync(path.join(claudeDir, 'projects'), { recursive: true });
fs.mkdirSync(path.join(temporary, 'empty-skills'), { recursive: true });
process.env.AGENTMONITOR_DB_PATH = path.join(temporary, 'observations.sqlite');
process.env.AGENTMONITOR_AUTO_IMPORT_MINUTES = '0';
process.env.AGENTMONITOR_CLAUDE_DIR = claudeDir;
process.env.CODEX_HOME = codexDir;
process.env.AGENTMONITOR_SKILL_CATALOG_DIRS = path.join(temporary, 'empty-skills');
const load = relative => import(pathToFileURL(path.join(source, relative)).href);
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
let connection;

try {
  const schema = await load('src/db/schema.ts');
  connection = await load('src/db/connection.ts');
  const imports = await load('src/import/index.ts');
  const codex = await load('src/import/codex.ts');
  const watcher = await load('src/watcher/index.ts');
  const projects = await load('src/import/project-repair.ts');
  const totals = await load('src/import/session-total-check.ts');
  const identity = await load('src/util/project-identity.ts');
  identity.setScratchRootsForTests([]);
  schema.initSchema();
  const db = connection.getDb();
  if (fs.realpathSync(db.name) !== fs.realpathSync(process.env.AGENTMONITOR_DB_PATH)) {
    throw new Error('observation database is not the temporary fixture database');
  }
  const plain = path.join(directory, `${request.stem}.jsonl`);
  const compressed = `${plain}.zst`;
  const encode = records => Buffer.from(records.map(record => JSON.stringify(record)
    .replaceAll('"$CWD"', JSON.stringify(cwd))).join('\n') + '\n');
  const writePlain = records => fs.writeFileSync(plain, encode(records));
  const writeCompressed = records => fs.writeFileSync(
    compressed, zstdCompressSync(encode(records)));
  const names = files => files.map(file => path.basename(file)).sort();
  const discover = () => ({
    import: names(codex.discoverCodexLogs(codexDir)),
    browser: names(watcher.discoverCodexSessionFiles(codexDir)),
  });
  const all = (query, ...params) => db.prepare(query).all(...params);
  const snapshot = () => ({
    browser: all(`SELECT id, project, cwd, message_count, user_message_count
      FROM browsing_sessions WHERE file_path IS NOT NULL ORDER BY id`).map(row => ({
        ...row, cwd: row.cwd === cwd ? '$CWD' : row.cwd,
      })),
    messages: all(`SELECT session_id, ordinal, role, content, timestamp FROM messages
      WHERE session_id IN (SELECT id FROM browsing_sessions WHERE file_path IS NOT NULL)
      ORDER BY session_id, ordinal`).map(row => ({ ...row, content: JSON.parse(row.content) })),
    usage: all(`SELECT session_id, tokens_in, cache_read_tokens, tokens_out, model,
      client_timestamp, json_extract(metadata, '$._response_id') AS response_id
      FROM events WHERE source = 'import' AND event_type = 'llm_response'
      ORDER BY session_id, client_timestamp, event_id`),
    totals: totals.checkSessionTotals({ source: 'codex', codexDir, claudeDir }).sessions,
  });
  const rowIdentity = () => ({
    messages: all(`SELECT session_id, ordinal, id FROM messages
      WHERE session_id IN (SELECT id FROM browsing_sessions WHERE file_path IS NOT NULL)
      ORDER BY session_id, ordinal`),
    events: all(`SELECT event_id, id FROM events WHERE source = 'import' ORDER BY event_id`),
  });
  const retained = (before, after, key) => ({
    before_count: before.length,
    retained_count: before.filter(row => after.some(next => key(next) === key(row)
      && next.id === row.id)).length,
  });
  const retainedIdentity = (before, after) => ({
    messages: retained(before.messages, after.messages, row => `${row.session_id}:${row.ordinal}`),
    events: retained(before.events, after.events, row => row.event_id),
  });
  const cycle = (force = false) => {
    const discovery = discover();
    const sync = watcher.syncAllCodexFiles(db, codexDir, { force });
    const imported = imports.runImport({ source: 'codex', codexDir, force });
    return { discovery, sync: { total: sync.total, errors: sync.errors },
      import_files: imported.totalFiles, state: snapshot() };
  };

  let observation;
  if (request.scenario === 'corrupt-compressed') {
    fs.writeFileSync(compressed, Buffer.from('authored non-zstd bytes\n'));
    let readRaised = false;
    try { imports.readCodexRollout(compressed); } catch { readRaised = true; }
    observation = { discovery: discover(), read_raised: readRaised,
      sync_result: watcher.syncCodexSessionFileDetailed(db, compressed, { force: true }).result,
      browser_rows: all('SELECT id FROM browsing_sessions').length,
      usage_rows: all('SELECT id FROM events').length };
  } else if (request.scenario === 'fallback-name') {
    writeCompressed(request.final);
    const events = codex.parseCodexFile(compressed, { codexDir });
    const sync = watcher.syncCodexSessionFileDetailed(db, compressed, { force: true });
    observation = { discovery: discover(), sync_result: sync.result,
      event_session_ids: [...new Set(events.map(event => event.session_id))].sort(),
      browser_ids: all('SELECT id FROM browsing_sessions WHERE file_path IS NOT NULL ORDER BY id')
        .map(row => row.id),
      messages: snapshot().messages };
  } else {
    const stages = [];
    let initialIdentity;
    if (request.scenario === 'first-compressed') {
      writeCompressed(request.final);
    } else if (request.scenario === 'both-forms') {
      writeCompressed(request.initial);
      writePlain(request.final);
    } else if (request.scenario === 'transition' || request.scenario === 'plain-append') {
      writePlain(request.initial);
      stages.push(cycle());
      initialIdentity = rowIdentity();
      if (request.scenario === 'transition') {
        writeCompressed(request.final);
        fs.rmSync(plain);
      } else {
        fs.appendFileSync(plain, encode(request.final.slice(request.initial.length)));
      }
    } else {
      throw new Error(`unknown observation scenario: ${request.scenario}`);
    }
    stages.push(cycle(true));
    const finalIdentity = rowIdentity();
    const currentFile = fs.existsSync(plain) ? plain : compressed;
    const read = imports.readCodexRollout(currentFile);
    const directEvents = codex.parseCodexFile(currentFile, { codexDir });
    const direct = { content_matches_fixture: read.bytes.equals(encode(request.final)),
      hash_matches_content: read.hash === sha(read.bytes),
      parser_session_ids: [...new Set(directEvents.map(event => event.session_id))].sort(),
      parser_usage: directEvents.filter(event => event.event_type === 'llm_response')
        .map(event => ({
        response_id: event.metadata?._response_id ?? null,
        tokens_in: event.tokens_in, cache_read_tokens: event.cache_read_tokens ?? 0,
        tokens_out: event.tokens_out, model: event.model ?? null,
      })) };
    // Repeat through normal change detection and then a forced full reread.
    const repeat = cycle();
    const repeatedIdentity = rowIdentity();
    const forced = cycle(true);
    const forcedIdentity = rowIdentity();
    // Both forms must represent the current plain transcript, even when the
    // stale compressed copy is discovered later in a directory listing.
    let compressedOnly;
    if (request.scenario === 'both-forms') {
      writeCompressed(request.final);
      fs.rmSync(plain);
      compressedOnly = cycle(true);
    }
    // Deliberately stale projects exercise production preview/apply, not the
    // parser's already-correct project derivation. Unknown projects stay intact.
    db.prepare("UPDATE events SET project = 'aurora-task' WHERE session_id = ?")
      .run(request.session_id);
    db.prepare("UPDATE sessions SET project = 'aurora-task' WHERE id = ?")
      .run(request.session_id);
    db.prepare("UPDATE browsing_sessions SET project = 'aurora-task'").run();
    const projectState = () => ({
      events: all('SELECT DISTINCT project FROM events WHERE session_id = ? ORDER BY project',
        request.session_id).map(row => row.project),
      sessions: all('SELECT id, project FROM sessions ORDER BY id'),
      browser: all('SELECT id, project FROM browsing_sessions ORDER BY id'),
    });
    const preview = projects.repairProjectNames(db, { codexDir, claudeDir, apply: false });
    const previewState = projectState();
    const applied = projects.repairProjectNames(db, { codexDir, claudeDir, apply: true });
    const appliedState = projectState();
    const again = projects.repairProjectNames(db, { codexDir, claudeDir, apply: true });
    observation = { stages, direct, repeat, forced,
      repeat_identity: retainedIdentity(finalIdentity, repeatedIdentity),
      forced_identity: retainedIdentity(repeatedIdentity, forcedIdentity),
      project_repair: { preview: preview.rows, preview_state: previewState,
        applied: applied.rows, applied_state: appliedState, again: again.rows } };
    if (initialIdentity) {
      observation.transition_identity = retainedIdentity(initialIdentity, finalIdentity);
    }
    if (compressedOnly) observation.compressed_only = compressedOnly;
  }
  process.stdout.write(JSON.stringify(observation));
} finally {
  connection?.closeDb();
  fs.rmSync(temporary, { recursive: true, force: true });
}
"""


def observe(source, request):
    """Run one independently isolated Node process; failures remain check errors."""
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
    with tempfile.TemporaryDirectory(prefix="completed-session-adapter-") as temporary:
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
        raise RuntimeError(f"Node observation process exited {completed.returncode}")
    return json.loads(completed.stdout)


def main():
    requests = json.load(sys.stdin)["requests"]
    observations = [observe(sys.argv[1], request) for request in requests]
    print(json.dumps({"observations": observations}))


if __name__ == "__main__":
    main()
