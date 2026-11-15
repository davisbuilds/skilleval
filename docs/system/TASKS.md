# Task packages and document artifacts

A task package binds an immutable artifact task to its environment requirements
and baseline/reference validation. Repair and document tasks share this outer
contract; native task tools still own their executable observations. It is an
independent implementation informed by SkillsBench/Harbor task packaging,
Tessl fixtures, and the evidence/assessment patterns in [SOURCES](../project/SOURCES.md).
No upstream runner is required.

For Git-derived inputs, [source capture](SOURCE-CAPTURE.md) freezes a full commit
revision with optional declared context exclusions, a new one-commit baseline,
and operator-only provenance. This is an input preparation step; it does not
package a task, validate its VM environment, or establish task quality.

## Operator acceptance maps

[Measurement design](../project/MEASUREMENT.md) owns how requirements and task
populations are selected. `schema acceptance-map-spec` defines an operator-only
sidecar bound to an existing artifact task and task package by path and digest.
It leaves those bytes and their environment receipts unchanged. A new acceptance
map is a new evidence generation; it cannot make an old run prespecified.

Requirements distinguish core outcome, regression and evidence roles, with declared
importance, source rationale, exact probe/JSON-Pointer routes, semantic questions
and explicit gaps. Every frozen probe must be mapped or expressly excluded with a
reason. Pointer resolution checks expected-object structure, not observed success.
Generic semantic criterion membership does not resolve each mapped requirement.
Optional control/review attachments are checked for exact bytes only; their relevance,
claimed provenance and independence remain unverified. No aggregate quality score or
semantic admission is produced.

```sh
uv run skilleval task-package prepare-acceptance acceptance-spec.json --output acceptance-map.json
uv run skilleval task-package inspect-acceptance acceptance-map.json --sha256 MAP_SHA256 \
  --review-output acceptance-review.md
```

Both commands are offline: they read immutable inputs as data and do not execute
imported code, launch VMs, read credentials or call models. Preparation and Markdown
publication are exclusive; choose fresh destinations. Maps, attachments and review
output must stay outside solver captures. `validate acceptance-map-spec` checks
structure only, while preparation/readback also verify references and field routes.

The [completed-session template](../../examples/repair/completed-sessions/acceptance-map.template.json)
uses placeholder task/package paths and digests. Replace them with separately
retained identities before preparation. Its retrospective status and declared gaps
are intentional. [Study plan v4](STUDIES.md) pins selected map identities and rechecks
them before dispatch without sending maps or attachments to the solver or judge.

## Freeze and validate

`schema artifact-spec` exposes the existing file-artifact contract: captured input,
shared prompt, editable/output paths and optional editable directories, pinned
observation worker, expected
observations and source provenance. `artifact` is an alias of `repair`; existing
commands and evidence formats remain supported. A document adds an output file
to an otherwise unchanged source packet. Collection preserves actual bytes and
out-of-scope changes, using the same rules as [repairs](REPAIRS.md). Directory scope permits independently
chosen helper/test filenames within declared trees; it grants no authority over
Git metadata, cache directories or capture manifests. Preparation summaries list
exact output paths and any directory grants separately.

`schema task-package-spec` adds:

- A family (`repair` or `document`) and the artifact task's retained digest.
- A pinned image, setup notes, required source/history paths, and explicit
  coverage gaps. Setup notes describe preparation; they are not host commands.
- An untouched baseline and optional reference captures, each with named
  validation commands, deadlines and frozen expected observations.
- Limitations that remain visible even when observations match.

```sh
uv run skilleval artifact prepare artifact-spec.json --output task.json
uv run skilleval task-package prepare package-spec.json --output package.json
uv run skilleval task-package validate-environment package.json \
  --sha256 PACKAGE_DIGEST --runtime /path/to/smolvm --output validation
```

Preparation verifies captured context, task/image identities, unique lanes and
baseline agreement. It launches no VM and executes no imported code. The explicit
validation command starts a fresh offline VM per capture by default and retains full bounded
command observations, source changes, invocation profile and cleanup evidence.
Select `solver-isolated-v1` to run commands through the native solver's
unprivileged filesystem/process boundary, using fresh homes per lane. The legacy
`native-root-v1` remains available for historical diagnostics; its receipts cannot
qualify the current native solver. No inference socket or credentials enter
validation VMs. Boundary setup and namespace teardown must both succeed before
observations can match; failed setup retains bounded diagnostic output.

JSON lanes compare a native command's observed object with its frozen expectation.
Unittest lanes require a nonempty completed suite and reconcile failure/error/skip
counts with their identities. Use verbose unittest output; incomplete identities,
unsupported summaries, timeouts and truncated output remain incomplete. Changed
source, changed exit status, unexpected failures and skipped coverage cannot match
a clean expectation. Disposable Python/pytest cache paths are excluded; refreshes
of `.git/index` are recorded separately as metadata changes. Other Git objects and
source bytes remain checked. The profile retains the previously tested 8 MiB
per-file ceiling; changing such limits can change ordinary test behavior.
Each stream retains at most 1 MiB, separately from that file ceiling. Truncation
prevents a match. Host JSON readers allow for escaped output across up to eight
lanes, so a bounded guest report remains readable after collection.

Known failures and skips can be declared explicitly. A `matched` report means the
declared observations were reproduced; it does not mean all tests passed or all
workloads are supported. Coverage gaps remain in the report. A successful process
exit alone cannot validate an empty or skipped suite. Task-specific JSON probes
must themselves use meaningful controls; a schema cannot establish their truth.

### Readiness reuse and invalidation

Shared-case validation receipts use schema version 2. They retain the original package
locator and digest, full execution identity and observations, plus an
`execution_contract` and its digest. Each case records a `readiness_identity`
projected from its captured full identity. The execution contract binds image,
execution profile, ordered cases and lanes, case IDs/roles and frozen input
identities, lane IDs, argv and deadlines. The named execution-source footprint is
owned by [environment identity](ENVIRONMENT.md#completed-task-environment-readiness).

Current readback fully validates the selected package and its inputs, verifies
the original package bytes against their retained digest, and replays the current
parsers and expectations against saved process observations. It does not need the
original package's historical grading worker to remain available. Original
receipt bytes, status, gaps and limitations remain unchanged. A complete original
`unexpected` observation can satisfy new expectations; incomplete execution,
truncated output, task-file mutation, failed boundary setup/teardown or incomplete
cleanup cannot support reuse. Matching declared failures or skips still retains
those exceptions.

| Change | Required action |
| --- | --- |
| Image, frozen case bytes, execution profile, or ordered case/lane IDs, roles, argv or deadlines | Run fresh environment validation and retain a new receipt. |
| Runtime/host, controller Python implementation/version, or a source in the applicable execution footprint | Run fresh environment validation. The footprint conservatively hashes whole modules. |
| Parser, expected exit status or expected observations | Replay current interpretation against complete retained process observations; execute again if those observations cannot establish the selected expectation. |
| Brief, grading worker/probes, editable scope, lane purpose, setup prose, required-path declarations, gaps or limitations | Fully validate the selected package and replay retained observations. Unchanged execution conditions do not require a new VM suite. Preserve original and selected disclosures separately. |
| Pricing, native accounting/capture, judge or report code outside the execution footprint | Reuse applicable schema-2 task observations. Prepare a new study plan when its full identity or inputs change; other qualification guards still apply. |
| Schema-1 receipt with a different package or full identity | Retain the exact legacy binding. Run one fresh schema-2 validation to use the narrower reuse contract; never rewrite or upgrade the old receipt. |

For a packaged study task, supply `package`, `package_sha256`,
`environment_validation`, and `environment_validation_sha256` together. Preparation
and every dispatch recheck selected inputs, replayed observations, cleanup, image,
applicable readiness identity and the shared prompt. New study plans and reports
retain an `environment_readiness` summary with original/current package digests,
original status and disclosures, selected disclosures and the current matched
expectation status. The original validation report remains unchanged.

Frozen study plans still require their exact full execution identity; source
changes can require a cheap new plan while task-suite observations remain reusable.
There is no age-based expiry or persistent verification cache. Existing schema-1
validation receipts keep their exact package/full-identity rule, and unpackaged
development plans remain readable. The packaged native route requires
`solver-isolated-v1`; historical root validation cannot qualify that solver.
The native writable-file mode remains `repair` for wire compatibility; logical
task family lives in the package. Readiness reuse grants no scored admission or
broader environment coverage.

### Reuse individual checks

Set `environment.validation_isolation` to `lane-v1` to execute every lane in a
fresh VM and produce a schema-3 report. The default `case-v1` keeps the shared
workspace within each case. Fresh homes and source-byte checks alone do not make
shared-case lanes independent: permissions, empty directories, ignored caches,
Git index state and kernel resources can survive earlier commands. Existing
schema-1/2 receipts cannot become independent lane evidence.

```sh
uv run skilleval task-package validate-environment package.json \
  --sha256 PACKAGE_DIGEST --runtime /path/to/smolvm \
  --reuse prior/task-validation.json --reuse-sha256 REPORT_DIGEST --dry-run
uv run skilleval task-package validate-environment package.json \
  --sha256 PACKAGE_DIGEST --runtime /path/to/smolvm \
  --reuse prior/task-validation.json --reuse-sha256 REPORT_DIGEST --output next-validation
```

Both commands use the same planner; the preview creates no files or VMs. Omit
both reuse flags to execute every lane. The caller names one pinned prior report;
there is no hidden cache or automatic search. The first independent qualification
pays for one VM startup per lane. Later validation executes only missing,
incomplete or incompatible lanes, retaining direct references to the original
`lane-execution.json` receipts for the others. Repeated reuse does not create a
recursive report chain.

Each lane binds its image, profile, case ID/role/input, lane ID, command and
deadline plus the `task-environment-lanes-v1` execution fingerprint. Changing one
command reruns that lane; changing one case input reruns that case's lanes.
Reordering independent lanes does not require rerunning them. Image, runtime or
applicable execution-code changes require all affected lanes again. Parser and
expectation changes reinterpret the same raw observation; they do not force a
new execution or silently turn an unresolved interpretation into readiness.
Build/test sequences that need shared files belong in one lane.

The aggregate records each lane's action, reason, original source, current
comparison and execution completeness. Studies replay current expectations and
identities; assessments resolve selected observations to their original producer.
Original failures, gaps and limitations stay visible. Missing or tampered evidence
is an error before execution, not a cache miss. Failed executions remain in new
receipts; incomplete cleanup stops subsequent VMs. Input drift at final readback
retains execution envelopes but prevents publication of an aggregate report.

## Grade the artifact independently

`artifact collect`, `artifact control` and `artifact check` reuse exact collection
and the fresh, credential-free observation VM. Expected answers and comparison
authority remain on the host. Mechanical checks do not become semantic grades.
Reference and deliberately defective artifacts stay outside the solver input.

Document assessment specifications declare `assessment_kind: "document"` and use
`schema document-assessment-spec`. `artifact prepare-assessment` and
`artifact record-assessment` then select the document rubric and result schema.
The four criteria are factual support, relevant limitations, audience usefulness,
and clarity/action. Each permits satisfied, violated, or unresolved; every resolved
judgment needs evidence citations. Different valid drafts may pass. Skill procedure,
style keywords and similarity to a preferred draft are not answer keys.

New specification tasks select `rubric_profile: "specification-v1"`. This keeps
the four criterion IDs while using specification-specific language about grounded
choices, implementability and observable acceptance, with versioned
`specification-agentic-v1` or `specification-static-v1` packets. The omitted/default
`document-v1` profile preserves existing document packets byte for byte. The
[operational-metrics example](../../examples/document/operational-metrics/README.md)
includes valid alternatives and defective controls; authored labels are development
evidence, not independent grader audit. A readable file is only a structural
observation. [Handoffs](HANDOFFS.md) bind exact generated artifacts to later
implementation without treating presence as semantic success.

For explicit requirement coverage, add `requirement_policy:
"acceptance-requirements-v1"`, `acceptance_map` and `acceptance_map_sha256` to the
assessment spec. This opt-in projects only frozen requirement text, source
rationale, semantic questions and gaps into the packet; controls and expected
labels remain operator-only. The exact task and package family must match.
The judge returns citation-backed per-requirement judgments alongside the four
broad criteria. Reports preserve omitted and unresolved requirements separately;
broad satisfaction alone is not requirement coverage. Repair assessments support
the same policy with their three broad criteria. See the
[requirement judgment contract and runnable route](CALIBRATION.md#requirement-level-repair-and-document-judgments).

Document judges use the existing investigative/static policies, masked treatment
metadata and append-only recording. The collector retains its existing
`task/investigation/repair/` artifact namespace. Original repair schemas, rubric
and packet generation remain unchanged, so earlier records are not reinterpreted.
For document judgments, both citation lists are validated by retained path:
original citations must match the frozen packet (including byte-checked native
aliases), and investigation citations must exist in the captured investigation.
Static judgments cannot cite investigations; every resolved judgment still needs
substantive original evidence. The recorder preserves the submitted lists.
Masking is not proven blinding, citation validation is not semantic calibration,
and authored controls do not substitute for independent human audit.

## Inspect quality and consumption

Explicit outcome selection joins artifact checks and semantic judgments to the
original attempt. The selection never chooses a favorable or latest judgment.

```sh
uv run skilleval study report plan.json --sha256 PLAN_DIGEST --output study-run \
  --outcomes selection.json --outcomes-sha256 SELECTION_DIGEST \
  --review-output comparison.md
```

The fresh Markdown evidence index links the artifacts, patches, source packets and
judgments. It preserves unsuccessful attempts and unknown usage; task-agent tokens,
timing and conditional API-equivalent estimates remain separate from selected
judge consumption. The JSON report retains precise identities and observations.
Input tokens include the cached subset. Selected judge consumption is not the
total cost of failed or unselected judge invocations; retain an operational ledger
for those. Neither report infers uncertainty, conversion gains or skill benefit.

The [product-page example](../../examples/document/product-page/README.md) provides
an authored brief, mechanical observer and different supported control drafts.
Actual captured product/skill bytes and live attempt evidence remain external.
