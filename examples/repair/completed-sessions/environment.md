# Completed-session development validation

Maintainer-observed Linux aarch64 results through 2026-11-05. Exact captures, images,
receipts, logs and failed generations remain private retained evidence. These
observations qualify a bounded development route; they are not a public benchmark
release, independently audited correctness labels or a skill-effect estimate.

## Inputs and execution

The [capture helper](README.md) preserves AgentMonitor baseline `5f681e2`, reference
`f700961`, all 620 original tracked files and Matt Pocock's complete selected
`diagnosing-bugs` package at `a496f55`. The baseline backlog already describes the
compressed-session defect. This is repair completeness and verification with
ordinary diagnostic guidance, not concealed-cause discovery.

The tested route uses smolvm 1.32.3, Codex 0.164.0, Node 24.22.1, pnpm 11.28.6,
SQLite 3.53.4 and the source's frozen dependencies. Native tools run as UID 61000
through `solver-isolated-v1`; offline observations run separately as UID 65534.
No operator instructions, personal session history or application database is
supplied. Expected observations stay outside the guest.

Image controls verify native SQLite and zstd, tsx execution, readable dependencies
through both declared aliases, read-only mount flags and `EROFS` on writes. Task
storage and its local `node_modules` directory remain writable. Synthetic native
solver controls passed 80/80 assertions; operator-context controls passed 56/56.
These controls used no credentials or real model inference. Both control suites
were repeated successfully on 2026-11-05 with the current execution source.

## Ordinary commands

Both source snapshots ran the same commands under the solver boundary. The
wrapper copied the complete source into disposable scratch space, preserving
ordinary guidance, Git and test discovery. Existing source bytes stayed unchanged.

| Lane | Baseline | Reference |
| --- | --- | --- |
| Lint, TypeScript and frontend checks | Passed | Passed |
| Root build, including frontend build | Passed | Passed |
| Root tests | 1,677 passed, 1 failed, 5 skipped | 1,681 passed, 1 failed, 5 skipped |
| Frontend tests | 261 passed | 261 passed |

The shared failure is `serve launches the runtime through Portless by default`.
Startup begins a Codex quota request; shutdown can wait beyond the test's deadline.
A separate unchanged-test diagnostic failed twice with Codex available on PATH
and passed twice with it unavailable. This supports the quota/shutdown explanation;
the final ordinary environment keeps Codex available and preserves the failure.
The five skips cover a case-insensitive filesystem and four macOS launchd cases.
Matching these declared observations does not make the suite clean.

A separate synthetic native command built directly in the task workspace and
collected its outputs through the production artifact archive. The build produced
290 files totaling 2,817,694 bytes, all under `dist/` or `frontend/dist/`. The task
permits those directories and retains their bytes and patch entries, avoiding a
false scope failure for an ordinary build. Generated output alone is not a repair.

Chromium E2E, separate built/verify/parity lanes, optional sibling-skill smoke and
installed macOS services remain outside this validation. The six outcome probes
exercise finite production paths, not every live watcher or service lifecycle.

## Disposable application home

On 2026-11-05, both snapshots repeated the ordinary commands above with a copy of
the three supplied history files in a disposable `CODEX_HOME` outside source.
Pass/failure/skip identities and build outputs matched the earlier observations.
Original captured files and the supplied source-tree history stayed unchanged.
The existing root-test failure remains declared; matching it is not a clean suite.

The separate `application-home` lane in [validate.py](environment/validate.py)
exercises production discovery/import, checks the actual temporary database
identity, and starts the pinned Codex app-server through production quota code.
It forwards initialization while suppressing account RPCs; the resulting quota
timeout is expected. The credential-free offline VM supplies the network boundary.
This control qualifies local support initialization, not provider quota behavior.

The copied-home case created real Codex support files outside source while
preserving the fixture. A positive control using the old source-tree home created
the same support state inside its disposable source copy. Both baseline and
reference reproduced this distinction, and all owned scratch and VMs were removed.
Ordinary root tests retain their full quota behavior without this interception.

Each lane records home placement, fixture preservation, and generated support
entries. Declared root-test observations include those support entries. An initial
validation with an empty expected list remains retained separately; only that
metadata differed, and a fresh generation reproduced the corrected expectations.
Full raw output is bounded, and truncating an added log header also marks the
observation incomplete. Authored transport tests do not substitute for these VM
observations.

## Independent outcome controls

The same observer runs against untouched, reference and deliberately incomplete
repairs. It checks actual stored messages, usage, identities, deduplication,
project repair and reader behavior; it does not match preferred helper names or
the skill's procedure.

| Control | Observed distinction |
| --- | --- |
| Untouched baseline | Plain append passes; the five compressed-file probes fail |
| Historical reference | All six probes pass |
| Reference with import reader restored to baseline | Initial compressed read, transition and corrupt-file probes fail |
| Reference with project repair restored to baseline | Initial compressed, transition and coexisting-form probes fail |
| Reference with old browser session-key derivation | Initial compressed, transition, coexisting-form and fallback-name probes fail |

These are related cases for one exposed task, not six independent tasks. Alternate
valid implementations remain eligible. A semantic assessment must still examine
source changes, regression risk, verification claims and limitations independently.

## Corrections retained in earlier generations

- The first solver brief pointed application `CODEX_HOME` at the supplied
  source-tree fixture while requiring scratch and databases outside source.
  Ordinary quota polling could create Codex support databases in that home even
  with the main application database in temporary storage. Retained observations
  show transient support files followed by cleanup and unchanged final fixture
  bytes. This is a task-context confound, not independent evidence of personal-store
  access or poor repair quality. New briefs require a disposable copy of the
  supplied home outside source. Existing task contracts, runs and judgments retain
  the original brief; the historical development pair does not qualify the revised
  context. The disposable-home validation above qualifies the new context for
  development work without upgrading those earlier attempts.
- pnpm's automatic dependency check attempted installation in the offline guest.
  The image now explicitly disables implicit installation for preinstalled frozen
  dependencies, including nested scripts; ordinary commands remain unchanged.
- smolvm replaces the image's workspace. Solver and observer preparation now
  project dependencies explicitly. Official archive ownership also needed to be
  normalized during image construction; runtime ancestry guards remain intact.
- A transcript hash expectation depended on JSON object-key order. The production
  request serializer changed that order, falsely rejecting valid plain behavior
  and the reference. The current worker compares decoded bytes with the fixture
  actually written from that request, while retaining independent database checks.
  Original worker bytes, expectations and failed receipts remain retained.
- The first development pair exposed an archive-format limit for long filenames.
  npm cache names reproduce the failure; the original failing filename was not
  retained. Bounded path metadata now preserves these files through collection,
  readback and checker transfer. A fresh synthetic native build/pack control
  retained exact long cache/source bytes and reached the offline checker. Failed
  original archives remain unusable evidence, without rewritten end markers.
- The initial 32-request allowance stopped one agent before its final response;
  the other reached the 780-second deadline. Both attempts also failed collection,
  so neither supports a completed quality comparison. Explicit budgets may now
  select up to 128 requests, with defaults unchanged and separate observation
  limits. A new pair selects 64 requests and the same deadline; it cannot replace
  the first pair or establish a skill effect by itself.

New task preparation freezes an adjacent worker copy. Later template edits cannot
silently change it. Changed inputs, workers or contracts require new generations.
