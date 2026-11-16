import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const check = process.argv.includes("--check");
const temporary = check ? await mkdtemp(join(tmpdir(), "skilleval-ui-")) : null;
try {
  const args = [join(root, "node_modules/typescript/bin/tsc"), "--project", root];
  if (temporary) args.push("--outDir", temporary);
  const result = spawnSync(process.execPath, args, { stdio: "inherit" });
  if (result.status !== 0) process.exitCode = result.status ?? 1;
  else if (temporary) {
    const expected = await readFile(join(temporary, "app.js"));
    const actual = await readFile(join(root, "src/skilleval/review_ui/app.js"));
    if (!expected.equals(actual)) {
      console.error("Review UI asset is stale. Run npm run build:ui.");
      process.exitCode = 1;
    } else console.log("Review UI types and committed JavaScript agree.");
  }
} finally {
  if (temporary) await rm(temporary, { recursive: true, force: true });
}
