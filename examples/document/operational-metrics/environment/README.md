# Guest environment qualification

These trusted operator tools exercise the frozen AgentMonitor source and its
built console. They belong outside solver task input and outside image runtime
trees. They do not grade a generated specification or the feature a later agent
implements. Only synthetic state is created, in disposable application homes and
databases outside a scratch repository. No host database, provider auth or real
session history is an input.

Each validation lane has a 570-second total command deadline and a 700-KiB raw
log budget. These are limits, not measured runtimes; retain actual wall times
from the outer VM/native receipt. The browser lane rebuilds its own scratch copy
and runs one headless browser. Run lanes separately so a broad root suite cannot
use the browser lane's time or artifact budget.

Use the [image recipe](../../../../environments/operational-metrics/README.md)
with the frozen source's exact manifests. Run every lane through the production
`solver-isolated-v1` route as UID/GID 61000 from `/workspace/task`, with
`/workspace/node_modules` projecting the read-only image dependencies. Ordinary
host tests import only these authored Python helpers and fake executables; never
run captured AgentMonitor commands on the host.

| Behavior | Tool / lane | Expected observation |
| --- | --- | --- |
| Effective dependencies and tools | `toolchain.py` | Read-only alias write fails with EROFS; task-local cache writes work; SQLite/zstd/tsx controls pass; exact tools and Chrome version recorded. |
| Lint and type checks | `validate.py checks` | Ordinary lint, backend TypeScript and frontend check exits and complete logs. |
| Ordinary root suite and built output | `validate.py root-tests` | `pnpm build`, then unchanged `pnpm test`; complete TAP identities, failures, skips and generated-artifact budget. |
| Frontend suite | `validate.py frontend-tests` | Ordinary Vitest JSON report with nonzero complete assertion identities. |
| Existing operational-metrics contracts | `validate.py metrics-tests` | Store, classification, retry and CLI tests through unchanged source; complete TAP report. |
| Built-console browser consumer | `validate.py browser` plus assets | Built app on an ephemeral loopback port; frozen patched-Chrome launcher, actual console navigation, same-origin metrics reads, synthetic grouping/filter controls, screenshot. |

The wrappers are standalone Python so a trusted native control can inline their
bytes using `python3 -I -c`. Main entry points refuse a non-Linux/non-solver route.
`validate.py` accepts `--source` (default current directory) and `--dependencies`
(default `/workspace/node_modules`). The browser lane additionally requires the
exact `browser.mjs` and `metrics-fixture.json` bytes, supplied as base64 via
`--browser-base64` and `--fixture-base64`. The native controller can build these
arguments with Python's `base64.b64encode`; no shell interpolation is needed.
The decoded assets are written only into the disposable scratch directory.

Task-package JSON lanes must add `--summary`. Exact expected JSON is established
only after reviewing discovery-run raw evidence; a discovery pass does not
automatically approve expectations. This projection preserves command exits,
timeouts, output completeness, every test identity/failure/skip, source-change
controls, native collection-bound booleans and browser API/control observations.
It omits dynamic screenshot bytes/hashes, byte-size diagnostics, Codex support
filenames and temporary process/origin paths. Full observations, including the
screenshot, are retained on stderr after the raw command logs. Their combined
stderr cap is 1 MiB; exceeding it marks the result incomplete. The screenshot's
PNG signature, nonempty contents, byte count and hash are checked before the
summary can report `screenshot_valid_png: true`.

Each lane prints compact JSON on stdout and complete bounded command stdout/stderr
on stderr, stopping on timeout or truncation. Nonzero exits remain failed command
observations while later lane commands still run. Reports retain test
failures and skips separately; zero reported tests or incomplete reports fail.
A green transport with skips is not a clean test population. Existing source
hashes are checked in both scratch and original captures; generated artifacts
are counted and measured without collecting dependencies. Each subprocess owns
a process group, killed before scratch cleanup. Raw logs and the JSON must be
retained by the outer controller before its VM disappears.

The full final scratch source (including Git metadata and generated output,
excluding injected dependency bridges) is checked against native collection's
10,000-entry and default 64-MiB native archive ceilings (including the native
collector's conservative tar-header allowance), and the 32-MiB per-file exported
evidence ceiling. Actual sizes stay in raw
observations; summary booleans fail the lane if a ceiling is exceeded. The
offline validator separately enforces its existing 8-MiB file-write limit. These
checks describe this scratch candidate; the outer native receipt must still
confirm complete collection of its task, home and evidence trees.

`browser.mjs` imports the actual compiled schema, database connection and Express
app, checks the opened DB handle resolves to the fresh synthetic path before
seeding, and starts `createApp` rather than production watcher/provider polling.
It preserves the current API shape: `{metrics:[{metric_name,attrs,occurrences,
total_value,last_seen}]}`. Its rows distinguish reported occurrence count from
summed values, success from rate-limit skips, old from in-window records, unknown
attributes, unrelated agent/name values, session filters and the limit. A
known-positive slice precedes empty-prefix/agent controls. Operational rows must
leave `events` empty. Browser requests are restricted to the owned app origin,
and the VM supplies the network boundary. The screenshot is returned as base64
with byte count and SHA256 in the JSON so it survives scratch cleanup.

This browser smoke establishes the environment can drive the current console and
read the existing API. It does not expect a new operational-metrics navigation
item before implementation, prove feature acceptance, or replace review of the
generated specification. It does not run every cross-browser E2E suite. Failed
or skipped baseline checks remain explicit evidence; do not change historical
source or weaken assertions to manufacture a clean baseline.

## Qualified development baseline

On 2026-11-08, the selected AgentMonitor revision
`0dd253129d143b5811224a0f80554a64eb3f4da0` completed all six lanes through
`solver-isolated-v1` on Linux aarch64 with the normal two-vCPU/2-GiB VM. Each
lane matched its reviewed expectations, source bytes stayed unchanged, collection
budgets held, and VM cleanup completed. No inference was used for these lanes.

- Tools: Node 24.22.1, pnpm 11.28.6, Codex 0.164.0, Playwright 1.64.1,
  Chrome for Testing 156.0.8078.80; read-only dependency refusal and SQLite/zstd/
  TypeScript positive controls passed.
- Lint, backend TypeScript and frontend checks passed.
- Root suite: 1,772 tests, 1,766 passed, one failed and five skipped. The failure
  is `serve launches the runtime through Portless by default`: shutdown returned
  null rather than zero. The skips cover case-insensitive filesystems and four
  macOS launchd cases. These are explicit baseline exceptions, not passing tests.
- Frontend: 279 passed. Existing metrics store/classification/retry/CLI: 30 passed.
- Browser: real Chrome launched under the solver boundary, drove Monitor →
  Analytics → Monitor, read the same-origin metrics API, discriminated known
  nonempty and empty filter controls, kept usage tokens zero, and retained a
  verified PNG. The screenshot was inspected. This remains existing-console
  environment evidence, not acceptance of the future metrics feature.

Retained identities (private logs and captures remain external to this checkout):

| Artifact | SHA256 |
| --- | --- |
| Selected baseline manifest | `5b348c0e87714a3459263f3a19b9a805ebec4245ffe3492ed0a48013620c10a5` |
| Browser image archive | `f65f8dde7a23718bf76ce18ac93b345bc5bf50fdce9f673520ee3e3b4e7f672a` |
| Task package | `3d2df9fd6868379ad27024f5e390be81a45ebd29a35d8c22c51d1472f0d0929c` |

The first characterization is preserved separately. Its browser probe wrongly
expected `#monitor`; the pinned router returns Monitor to an empty hash. A new
probe generation corrected that expectation and checked the resulting Monitor
view. Build attempts also exposed a trailing-space Chrome version string and a
redundant inherited WORKDIR layer; the final image fixes those packaging issues
without weakening the solver boundary or editing captured application source.
