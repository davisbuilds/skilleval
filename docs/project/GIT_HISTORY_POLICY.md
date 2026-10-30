# Repository and CI policy

Skilleval uses a public GitHub repository with `main` as its default branch.
Merge commits are the default; rebase merges are allowed, squash merges are
disabled, and merged branches are deleted. Releases and package-registry
publication are separate from ordinary source changes.

Changes to `main` go through pull requests. Require up-to-date branches and these
stable check names, including for administrators:

| Required check | Scope |
| --- | --- |
| `Checks (ubuntu-24.04)` | Locked install, deterministic gate, package build |
| `Checks (macos-15)` | Same gate and build on hosted macOS |
| `Workflow audit` | Locked, isolated zizmor workflow audit |
| `Secret scan` | Controlled scanner build and complete fetched-history scan |

Require resolved review conversations; block force pushes and branch deletion.
The sole maintainer can merge after checks without another human approval.
Request Codex review when available and resolve actionable findings; its absence
is not a substitute for maintainer review. Check names and branch protection
must change together. Query live GitHub settings when verifying enforcement;
this document defines policy rather than proving service state.

The workflows in [`.github/workflows`](../../.github/workflows) own execution.
They use hosted runners, read-only repository permissions, pinned actions and
toolchains, and no private runners or model credentials. Linux/macOS deterministic
checks do not qualify native VM execution on either platform. Actions cannot
approve pull requests. Repository security settings enable secret scanning,
push protection, dependency alerts, and automated security-update proposals
where supported; these complement the required scanner.

The local pre-push gate is `scripts/check`. For workflow/tool changes, also run
the [CI-tool checks](../../.github/ci-tools/README.md). Exact toolchain inputs need
manual maintenance; Dependabot owns the configured uv and action updates. Keep
the eight-day update cooldown aligned with the seven-day installation quarantine.

Before a first publication, review the intended tree, history, notices, and any
published artifacts. Later changes use proportionate diff and publication-boundary
review. Raw captures, runtime files, model traces, and human-audit packets remain
outside tracked source. A green CI run does not authorize publishing those files
or establish skill effectiveness or grader reliability.
