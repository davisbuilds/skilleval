# Completed-session development validation

Maintainer-observed Linux aarch64 results on 2026-11-02. Exact captures, images,
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
These controls used no credentials or real model inference.

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

New task preparation freezes an adjacent worker copy. Later template edits cannot
silently change it. Changed inputs, workers or contracts require new generations.
