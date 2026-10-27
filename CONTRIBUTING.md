# Contributing

## Welcome and scope

Bug reports, focused fixes, documentation improvements, and evidence-backed
proposals are welcome. Discuss substantial features, new dependencies, new
integrations, and public-interface changes before major implementation. Public
availability does not promise a response time or acceptance of every proposal.

The [vision](docs/project/VISION.md) and [roadmap](docs/project/ROADMAP.md) explain
the current scope. This is a foundation under development, not a published stable
benchmark or a claim that skills improve outcomes.

## Understanding and agent use

Agent-assisted contributions are welcome. The submitter remains responsible for
understanding the purpose, important behavior, tradeoffs, and verification limits
of the change. No prompting diary or human rewrite is required.

Keep execution validity, task quality, skill use, and cost separate. Tests of
record structure or a subprocess fixture do not establish semantic grader quality
or effective VM isolation. Preserve failed and inconclusive results.

## Choosing work

Start with a concrete bug/reproduction or an entry in the
[backlog](docs/project/BACKLOG.md). An issue is useful for sustained discussion or
coordination; there is no required issue-to-PR progression. A candidate in the
backlog is not automatically a roadmap commitment.

Consult the [source register](docs/project/SOURCES.md) before adopting external
components. Record exact provenance and retain applicable notices. Do not copy
private tasks, traces, credentials, machine paths, or unpublished operational
context into a contribution. Prefer minimal authored fixtures.

## Delivering a change

Follow [setup and verification](README.md#start-locally) and run `scripts/check`.
Use behavior-focused red/green tests for substantial changes, and real local
filesystem/process checks where practical. External services, VMs, and model
calls are opt-in and outside the ordinary gate.

Keep a change focused and update the owning docs when a contract or boundary
changes. Explain the problem, resulting behavior, checks and their limits,
compatibility, and material risks. Identify changed tests or weakened guards.
The [PR template](.github/pull_request_template.md) provides the review shape;
omit empty sections and scale detail to the change.

The intended history policy is merge + rebase, recorded in [AGENTS.md](AGENTS.md).
Hosting and release automation are not configured yet; do not infer a release or
public settings from local conventions. License selection precedes publication.
