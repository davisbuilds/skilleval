# skilleval

Local-first experiments comparing skills with ordinary agent behavior. Python owns
the CLI, evidence, qualification, and grading; a TypeScript review UI comes later.
Native repository tools remain the authority for task-specific executable checks.

## Documentation map

- `README.md`: supported workflows and current limits.
- `docs/project/VISION.md`: audience, outcomes, and task population.
- `docs/project/ROADMAP.md`: selected phases and evidence of completion.
- `docs/system/ARCHITECTURE.md`: evidence, runtime, harness, and grader boundaries.
- `docs/system/CALIBRATION.md`: public authored controls, judge records, and limits.
- `docs/system/RELIABILITY.md`: separately versioned judge stress controls and comparisons.
- `docs/system/STUDIES.md`: frozen development matrices, attempts, recovery, and reporting.
- `docs/system/REPAIRS.md`: repair collection, offline observations, semantic records and outcome selection.
- `docs/system/ACCOUNTING.md`: native usage semantics, conditional pricing, timing, and efficiency limits.
- `docs/system/GATEWAY.md`: explicit host inference capability and vendor provenance.
- `docs/system/ENVIRONMENT.md`: evidence-bound admission, invalidation, and recovery.
- `docs/system/NATIVE.md`: native reviews, static/investigative judges, and skill inputs.
- `docs/project/BACKLOG.md`: concrete deferred work.
- `docs/project/SOURCES.md`: evaluated sources, reuse candidates, and attribution.
- `docs/project/PILOT-CANDIDATES.md`: grounded task/skill shortlist and unqualified pilot boundaries.
- `CONTRIBUTING.md`: scope, agent-assisted contributions, and review expectations.

## Command Quickstart

Use `uv sync --locked` and `uv run skilleval --help`. `scripts/check` is the
deterministic gate. It never launches VMs, calls models, or reads credentials.

Pre-push: `scripts/check`. Use uv >= 0.9.17; preserve the locked dependency graph
and seven-day release quarantine. CLI help and schemas own exhaustive interfaces.

## Implementation boundaries

- Preserve original evidence. Assessments are separately versioned; importing a
  record does not establish independent correctness or current runtime admission.
- Keep execution validity, task quality, skill consultation, and usage separate.
  Missing telemetry is unknown, never zero. Development fixtures are not holdouts.
- A valid manifest or generated command is not proof of effective isolation.
  Qualify the actual execution route before scored use, with positive controls.
- Runtime qualification and model-backed work must be explicit commands. No
  model calls, host configuration changes, or installations in ordinary checks.
- Preserve the gateway's pinned upstream identity and adjacent MIT notice.
  Keep deliberate compatibility patches reviewable; avoid unrelated formatting.
  The typed wrapper owns local policy. Renew qualification when the route changes.
- Do not execute imported code on the host. Private cases and raw traces live in
  an explicit external data directory or ignored `.local/`, never committed.
- This repository has no runtime dependency on sibling checkouts. Import through
  explicit files; retain provenance and required notices when adapting code.
- Public-facing artifacts must work without private sibling paths, datasets,
  operational identifiers, or unpublished references. Keep original private
  provenance outside tracked source; use synthetic fixtures in ordinary tests.
- Record an upstream source and revision before copying or adapting implementation;
  preserve required notices with copied material. README acknowledges influences;
  SOURCES records candidates and adoption status. Do not imply endorsement.

## Testing

Use red/green for substantial behavior changes. The red step must fail on the
behavior being changed, not missing imports or symbols. Code with no behavior to
assert can be checked after. Prefer real filesystem/process dependencies and
observable outcomes. Run relevant existing checks for smaller changes.
Subprocess fixtures do not establish real VM isolation or semantic judge quality.
VM/model/service tests remain explicit opt-in operations outside `scripts/check`.

## Working Agreement

- Clarify materially vague or contradictory requests before implementing them.
- Stage explicit paths. Commit coherent changes; push only when asked. Intended
  GitHub history policy is merge + rebase, defaulting to a merge commit. There is
  currently no remote; inspect actual hosting/settings before relying on them.
- Update owning docs when behavior, contracts, boundaries, or direction changes.
  Re-ground in source, guidance, and recent changes after compaction.
- Log durable gaps in `docs/project/BACKLOG.md`, with what, why/evidence, and a
  useful next action or revisit trigger. Fix small blocking issues inline. Keep
  the backlog future-only and reconcile affected entries when work lands. Agents
  may execute clear entries directly; issues are useful for discussion or
  coordination, not a mandatory intermediate step. Roadmap owns selected direction.
- Keep changes local to this project. No sibling mutation is implied by reuse.
- Future public CI must use hosted runners and synthetic data, with pinned
  actions/toolchains, least permissions, and the same deterministic gate. Do not
  attach personal runners, credentials, or private evidence to fork-triggered jobs.
