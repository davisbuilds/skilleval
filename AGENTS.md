# skilleval

Local-first evaluation of explicit skill ON/OFF on relevant agent tasks. Measure
quality and outcomes alongside tokens, cost, and time. Python owns the CLI,
execution, evidence, and grading; native task tools own executable observations.
TypeScript owns the local results/exception view; the pi adapter is later work.

## Documentation map

- [README.md](README.md): public introduction, setup, and workflow entry points.
- [VISION.md](docs/project/VISION.md), [ROADMAP.md](docs/project/ROADMAP.md),
  [BACKLOG.md](docs/project/BACKLOG.md): principles, selected priorities, and future work.
- [MEASUREMENT.md](docs/project/MEASUREMENT.md): task selection, acceptance maps,
  grader validity and comparison design.
- [ARCHITECTURE.md](docs/system/ARCHITECTURE.md): data flow and trust boundaries.
- [TASKS.md](docs/system/TASKS.md), [REPAIRS.md](docs/system/REPAIRS.md): task packages,
  environment validation, file artifacts, executable checks, and outcome selection.
- [SOURCE-CAPTURE.md](docs/system/SOURCE-CAPTURE.md): pinned Git inputs, declared
  context exclusions, synthetic history and operator-only provenance.
- [STUDIES.md](docs/system/STUDIES.md), [ACCOUNTING.md](docs/system/ACCOUNTING.md):
  paired plans, attempts, reporting, and usage/cost/time semantics.
- [HANDOFFS.md](docs/system/HANDOFFS.md): exact specification selection, fresh
  vanilla implementation inputs, linked stage consumption and question handling.
- [STORAGE.md](docs/system/STORAGE.md): durable data roots, native rollouts, staging
  cleanup, and legacy path preservation.
- [RESULTS.md](docs/system/RESULTS.md): attributed synthesis, local browser review,
  evidence excerpts and separate operator answers.
- [SYNTHESIS.md](docs/system/SYNTHESIS.md): repeatable agent investigation of selected
  retained runs, native invocation provenance and deterministic accounting.
- [ENVIRONMENT.md](docs/system/ENVIRONMENT.md), [GATEWAY.md](docs/system/GATEWAY.md),
  [NATIVE.md](docs/system/NATIVE.md): qualification, recovery, inference, and native runs.
- [CLAUDE-JUDGES.md](docs/system/CLAUDE-JUDGES.md): subscription-native Claude
  authentication, host context controls and isolated VM investigation tools.
- [TASK-PRESENTATION.md](docs/system/TASK-PRESENTATION.md): solver-visible context,
  evaluation disclosures, the solver boundary, and its qualification limits.
- [CALIBRATION.md](docs/system/CALIBRATION.md), [RELIABILITY.md](docs/system/RELIABILITY.md):
  judge contracts, authored controls, stability checks, and independent audit limits.
- [RESEARCH.md](docs/system/RESEARCH.md): opt-in native web search, research judgments,
  retained source observations and retrieval visibility limits.
- [PILOT-CANDIDATES.md](docs/project/PILOT-CANDIDATES.md),
  [SOURCES.md](docs/project/SOURCES.md): task selection and external provenance.
- [CONTRIBUTING.md](CONTRIBUTING.md): contribution scope and review expectations.
- [GIT_HISTORY_POLICY.md](docs/project/GIT_HISTORY_POLICY.md): hosting, required
  checks, review, and merge policy; [CI tools](tools/ci/README.md): tool renewal.

## Command quickstart

Use `uv sync --locked`, `npm ci --ignore-scripts` and `uv run skilleval --help`
from the source checkout. Node (see `.node-version`) is needed for UI development
and the gate, not for the installed Python CLI or results server.
**Pre-push: `scripts/check`.** Use uv >= 0.9.17, preserve the lockfile and seven-day
release quarantine. The gate never launches VMs, calls models, or reads credentials.

Private captures, traces, runtime state, and audit packets
belong in the private `storage init` data root or another explicit external
directory. Existing ignored `.local/` evidence remains supported; preserve its
absolute locators when retiring implementation worktrees.

## Implementation guardrails

- Preserve original evidence. Changed inputs, rubrics, workers, or judgments create
  new generations; never rewrite an earlier result or retroactively qualify it.
  Explicit retirement of obsolete experiments follows
  [storage retirement](docs/system/STORAGE.md#organization-and-experiment-retirement):
  preserve retained dependencies, extract reusable tooling and record what is lost.
- Keep execution validity, task quality, skill exposure/consultation, and usage
  separate. Missing telemetry is unknown. A read is not semantic application;
  authored controls are not holdouts; one pair is not a skill-effect estimate.
- Give solvers ordinary work briefs without evaluation announcements. Keep
  evaluation-only context inaccessible; operator-state isolation alone does not
  establish that boundary. Preserve faithful upstream skill bytes and old evidence.
  Model-directed commands use the unprivileged solver boundary; do not bypass it to make
  a task pass. Qualify required tools and image contents through that same route.
- Support exploratory development runs from an original work brief, faithful repo
  context and declared provisional criteria/gaps. Use `mapped-acceptance-v1` when
  criteria are ready for a frozen operator acceptance map; keep maps outside solver
  input. Independent grader audit and prespecified effect-estimation design gate
  scored or general effectiveness claims, not each exploratory pair. Preserve earlier
  evidence and label post-outcome changes; never treat map validation as approval.
- Grade outcomes independently of the skill's instructions. Preserve alternative
  valid solutions, novel findings, failures, disagreements, and unresolved verdicts.
  Scored correctness requires independent grader audit and applicable admission.
  New repair/document packets should select `execution-context-v3`; retain older
  policy bytes. Command chronology is supporting evidence, not a final-candidate
  check, and missing output does not erase a retained completion.
- Validate actual task environments, including ordinary context, tests, required
  history, tools, identity, failures, and skips. Matching declared exceptions is
  not a clean suite. Isolation needs effective controls with known-positive cases.
  Reuse relevant checks and retained evidence when their inputs and boundaries
  still apply; add controls for consequential gaps or changed behavior. Collection
  scope should accommodate ordinary repository work; judge requested feature scope
  separately from which files can be retained.
- Run imported code only inside the VM. Runtime/model work is explicitly opt-in;
  never hide it in ordinary imports, tests, or setup. Keep credentials on the host.
- Keep runtime, image, catalog, and execution source stable while a VM is active;
  cleanup/recovery checks those identities too. Preserve the original route until
  recovery finishes, then prepare new receipts for changed execution code.
- Preserve legacy evidence schemas and packet bytes when adding task families.
  The shared writable-file mode remains `repair`; packages identify document tasks.
- Maintain the vendored gateway's exact provenance and MIT notice. Record external
  source revisions and applicable notices before copying or adapting material.
- Keep the project standalone and public-readable. No sibling checkout dependency,
  private paths, raw captures, or unpublished references in tracked artifacts.
  Original work uses Apache-2.0; imported material retains its own terms.

## Testing

Use behavioral red/green tests for substantial features and refactors. The red
step must fail on the behavior being changed, not a missing symbol. Prefer real
filesystem/process dependencies; run relevant existing checks for smaller edits.
Subprocess fixtures cannot establish real VM isolation or semantic judge quality.
The gate runs tests in parallel processes (`PYTEST_WORKERS=0` runs serially), so
tests must not share fixed host state: use `tmp_path` or unique temporary names,
ephemeral ports, and test-scoped environment changes.

The gate typechecks `frontend/app.ts` and verifies its committed browser asset;
run `npm run build:ui` after edits. UI changes also need the explicit browser
control in [RESULTS.md](docs/system/RESULTS.md), plus the real server route when
transport changes. No browser or package download is hidden in the gate.

After changing a field or contract, inspect every producer and consumer: schemas,
CLI parsing, recording/replay, report selection, docs, and parallel task families.
Retain failures when fixing live workflows, and replay unchanged evidence when
possible instead of calling the model again. Avoid unrelated requalification.

## Working Agreement

- **Clarify before building** when a request is vague, contradictory, or skips a
  key decision; clear, well-scoped requests need no interview.
- **Fix small issues now; backlog the rest.** Fix simple, quick, or blocking
  issues in this repo as part of the current change and call them out. Record in
  `docs/project/BACKLOG.md` only what can't be finished now or needs the
  maintainer's decision; don't open a branch or PR just for a backlog entry.
  Keep backlog entries future-only and roadmap priorities current.
- **Keep docs current** when a change makes a documented contract, boundary,
  procedure, or direction stale. Detailed delivery history belongs in Git; do
  not grow a session diary in docs.
- **Plan and hand off work.** Reuse clear task context. Save planning docs when
  decisions, acceptance, or coordination need persistence. Leave a brief
  handoff only when needed to resume unfinished work. Commit shared docs and
  keep status current. Before archiving, preserve lasting decisions and open
  work in maintained docs and repair links.
- **Comments.** Don't add code comments. The one exception is a decision record:
  start it with `WHY:` and state a non-obvious reason the code can't express.
  Never justify a shortcut in a comment; fix it or record it in
  `docs/project/BACKLOG.md`. `python3 scripts/check_comments.py` enforces a
  ratchet: after deleting comments run it with `--update`, and never raise a
  count in `.comment-baseline.json`.
- **Commit and push.** Commit coherent chunks, staging by explicit path and
  inspecting the staged set. Push your task branch and open a PR once the work
  is complete and the local gate passes; don't push directly to `main`. Never
  include private `.local/` evidence. Releases and package publication happen
  only when the maintainer asks.
- **Merge** only under the standing merge authority in the maintainer's
  workspace instructions, or when the maintainer asks. Without those
  instructions in context, leave the PR for the maintainer.
- **Git:** [GIT_HISTORY_POLICY.md](docs/project/GIT_HISTORY_POLICY.md) owns
  the merge strategy (merge commits by default) and required checks. `main`
  requires an up-to-date branch even for administrators, so update a behind
  branch before merging. Request Codex review on PRs when available and resolve
  actionable threads. Merging deploys and publishes nothing.
- Public CI uses hosted runners and synthetic inputs, pinned actions/toolchains,
  least permissions, and the deterministic gate. No personal runners, credentials,
  private datasets, or model calls in fork-triggered workflows.
- Use a persistent terminal session for long-running or multi-process work and
  retain logs outside tracked source. Clean up owned sessions when finished.
- **Re-ground after compaction** or a handoff in source, this file, the owning
  docs, and recent commits. A summary and your own earlier proposals are not
  user approval. Keep work scoped to this project.
