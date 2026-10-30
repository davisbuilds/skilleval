# skilleval

Local-first evaluation of explicit skill ON/OFF on relevant agent tasks. Measure
quality and outcomes alongside tokens, cost, and time. Python owns the CLI,
execution, evidence, and grading; native task tools own executable observations.
A TypeScript review UI and pi adapter are later work.

## Documentation map

Read the owner for the decision at hand; CLI help, schemas, and source own exact
interfaces. Do not load every reference for a small change.

- [README.md](README.md): public introduction, setup, and workflow entry points.
- [VISION.md](docs/project/VISION.md), [ROADMAP.md](docs/project/ROADMAP.md),
  [BACKLOG.md](docs/project/BACKLOG.md): principles, selected priorities, and future work.
- [ARCHITECTURE.md](docs/system/ARCHITECTURE.md): data flow and trust boundaries.
- [TASKS.md](docs/system/TASKS.md), [REPAIRS.md](docs/system/REPAIRS.md): task packages,
  environment validation, file artifacts, executable checks, and outcome selection.
- [STUDIES.md](docs/system/STUDIES.md), [ACCOUNTING.md](docs/system/ACCOUNTING.md):
  paired plans, attempts, reporting, and usage/cost/time semantics.
- [ENVIRONMENT.md](docs/system/ENVIRONMENT.md), [GATEWAY.md](docs/system/GATEWAY.md),
  [NATIVE.md](docs/system/NATIVE.md): qualification, recovery, inference, and native runs.
- [CALIBRATION.md](docs/system/CALIBRATION.md), [RELIABILITY.md](docs/system/RELIABILITY.md):
  judge contracts, authored controls, stability checks, and independent audit limits.
- [PILOT-CANDIDATES.md](docs/project/PILOT-CANDIDATES.md),
  [SOURCES.md](docs/project/SOURCES.md): task selection and external provenance.
- [CONTRIBUTING.md](CONTRIBUTING.md): contribution scope and review expectations.
- [GIT_HISTORY_POLICY.md](docs/project/GIT_HISTORY_POLICY.md): hosting, required
  checks, review, and merge policy; [CI tools](.github/ci-tools/README.md): tool renewal.

## Command quickstart

Use `uv sync --locked` and `uv run skilleval --help` from the source checkout.
**Pre-push: `scripts/check`.** Use uv >= 0.9.17, preserve the lockfile and seven-day
release quarantine. The gate never launches VMs, calls models, or reads credentials.

`src/skilleval/` owns implementation; `tests/` owns deterministic checks;
`examples/` contains authored fixtures and templates; `environments/` contains
opt-in image recipes. Private captures, traces, runtime state, and audit packets
belong in an explicit external directory or ignored `.local/`.

## Implementation guardrails

- Preserve original evidence. Changed inputs, rubrics, workers, or judgments create
  new generations; never rewrite an earlier result or retroactively qualify it.
- Keep execution validity, task quality, skill exposure/consultation, and usage
  separate. Missing telemetry is unknown. A read is not semantic application;
  authored controls are not holdouts; one pair is not a skill-effect estimate.
- Grade outcomes independently of the skill's instructions. Preserve alternative
  valid solutions, novel findings, failures, disagreements, and unresolved verdicts.
  Scored correctness requires independent grader audit and applicable admission.
- Validate actual task environments, including ordinary context, tests, required
  history, tools, identity, failures, and skips. Matching declared exceptions is
  not a clean suite. Isolation needs effective controls with known-positive cases.
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

After changing a field or contract, inspect every producer and consumer: schemas,
CLI parsing, recording/replay, report selection, docs, and parallel task families.
Retain failures when fixing live workflows, and replay unchanged evidence when
possible instead of calling the model again. Avoid unrelated requalification.

## Working agreement

- Clarify material ambiguity; proceed with authorized, bounded work.
- Stage explicit paths, inspect the staged set, and commit coherent changes. Push
  or publish only when requested. Never include private `.local/` evidence.
- GitHub policy is merge + rebase, defaulting to merge commits, with merged
  branches deleted and required checks on `main`. Verify live hosting/settings
  against the repository policy before relying on them.
  Request Codex review on PRs when available; resolve actionable review threads.
- Update the owning reference when a contract, boundary, procedure, or direction
  changes. Keep roadmap priorities current and backlog entries future-only.
  Detailed delivery history belongs in Git; do not grow a session diary in docs.
- Re-ground in current source, docs, and commits after a handoff. A summary or
  earlier proposal is not user approval. Keep work scoped to this project.
- Use a persistent terminal session for long-running or multi-process work and
  retain logs outside tracked source. Clean up owned sessions when finished.
- Public CI uses hosted runners and synthetic inputs, pinned actions/toolchains,
  least permissions, and the deterministic gate. No personal runners, credentials,
  private datasets, or model calls in fork-triggered workflows.
