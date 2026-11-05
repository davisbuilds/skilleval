"""Explicit guest-only dependency and tool controls, supplied as a JSON lane."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

NODE = r"""
import Database from 'better-sqlite3';
import {zstdCompressSync,zstdDecompressSync} from 'node:zlib';
const db = new Database(':memory:');
db.exec('CREATE TABLE records (value TEXT)');
db.prepare('INSERT INTO records VALUES (?)').run('sqlite-positive');
const sql = db.prepare('SELECT value FROM records').get().value;
const sqlite = db.prepare('SELECT sqlite_version() AS version').get().version;
db.close();
const input = Buffer.from('zstd-positive');
console.log(JSON.stringify({node:process.version,sqlite,sql,
  zstd:zstdDecompressSync(zstdCompressSync(input)).equals(input)}));
"""


def main() -> None:
    if os.getuid() != 61000 or Path.cwd() != Path("/workspace/task"):
        raise RuntimeError("tool controls require the explicit solver-isolated guest route")
    aliases = [
        Path("/workspace/node_modules"),
        Path("/usr/local/share/task-dependencies/node_modules"),
    ]
    projections = []
    for alias in aliases:
        result = {
            "path": str(alias),
            "tsx_sha256": hashlib.sha256((alias / "tsx/package.json").read_bytes()).hexdigest(),
            "readonly": bool(os.statvfs(alias).f_flag & os.ST_RDONLY),
            "write_errno": None,
        }
        try:
            descriptor, name = tempfile.mkstemp(prefix=".dependency-write-", dir=alias)
            os.close(descriptor)
            Path(name).unlink()
        except OSError as error:
            result["write_errno"] = error.errno
        projections.append(result)
    with tempfile.TemporaryDirectory(prefix=".tools-", dir=Path.cwd()) as directory:
        root = Path(directory)
        script = root / "example.ts"
        script.write_text("const value: string = 'tsx-positive'; console.log(value);\n")
        transpilation = subprocess.run(
            ["node", "--import", str(aliases[0] / "tsx/dist/loader.mjs"), str(script)],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()
    with tempfile.TemporaryDirectory(prefix=".bin-control-", dir="node_modules") as directory:
        (Path(directory) / "positive").write_text("writable task-local cache")
    runtime = json.loads(
        subprocess.run(
            ["node", "--input-type=module", "-e", NODE],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
    )
    versions = {}
    for command in ("pnpm", "git", "jq", "codex"):
        versions[command] = subprocess.run(
            [command, "--version"], capture_output=True, text=True, check=True, timeout=30
        ).stdout.strip()
    install_policy = subprocess.run(
        ["pnpm", "config", "get", "verify-deps-before-run"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()
    observed = {
        "uid": os.getuid(),
        "groups": os.getgroups(),
        "dependencies": projections,
        "task_writable": True,
        "task_node_modules_writable": True,
        "tsx": transpilation,
        "runtime": runtime,
        "versions": versions,
        "pnpm_verify_deps_before_run": install_policy,
    }
    print(json.dumps(observed, sort_keys=True))
    if (
        any(not row["readonly"] or row["write_errno"] != errno.EROFS for row in projections)
        or projections[0]["tsx_sha256"] != projections[1]["tsx_sha256"]
        or transpilation != "tsx-positive"
        or runtime["sql"] != "sqlite-positive"
        or runtime["zstd"] is not True
        or install_policy != "false"
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
