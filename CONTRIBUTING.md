# Contributing

## Welcome and scope

Bug reports, focused fixes, documentation improvements, and evidence-backed
proposals are welcome. Discuss substantial features, new dependencies, new
integrations, and public-interface changes before major implementation. Public
availability does not promise a response time or acceptance of every proposal.

The [vision](docs/project/VISION.md) and [roadmap](docs/project/ROADMAP.md) explain
the current scope. The interfaces and evaluation workflows are experimental;
development results do not establish that skills improve outcomes.

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
Verify actual hosting settings before relying on that policy. Release automation
and hosted CI activation remain tracked in the backlog; a local check is not an
observed GitHub check.

Original project work uses [Apache-2.0](LICENSE). Contributions are supplied under
that license unless explicitly stated otherwise. Preserve the distinct MIT notice
for the adapted OpenBench gateway/tests and document other imported material in
[SOURCES.md](docs/project/SOURCES.md). Task captures and skill inputs retain their
own licenses. Never attach private raw runs to a public issue or PR.
