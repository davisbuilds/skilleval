# Explicit-use pilot candidates

## Current selection direction

As of **2026-11-08**, prioritize a routine `write-spec` comparison on outstanding
maintainer work that deserves a specification and could yield a useful contribution.
Inspect the backlog against current source before selecting a bounded feature;
an entry alone does not prove the work is still missing or needs a new spec.
Freeze the source and ordinary context, qualify isolated execution, and assess
both the specifications and their implementation by fresh vanilla agents. Keep
the selected skill in the spec stage and record the complete handoff and costs.
The operational-metrics brief is selected; its task package and acceptance
criteria still need preparation and validation.

Debugging expansion is deferred. The broader engineering source priorities from
**2026-10-30** remain
[Addy Osmani](https://github.com/addyosmani/agent-skills),
[Matt Pocock](https://github.com/mattpocock/skills),
[Emil Kowalski](https://github.com/emilkowalski/skills),
[Cursor pstack](https://github.com/cursor/plugins/tree/main/pstack), and
[gstack](https://github.com/garrytan/gstack).
Defer new task selection involving blacktwist, charlie947, coreyhaines31, and
google-design-md. Existing writing captures, judgments, and historical audit
materials remain retained; this changes expansion priority, not earlier evidence.

Select concrete jobs a maintainer would actually delegate, with normal project
context and a useful deliverable. Debugging, feature implementation, change-impact
review, UI interaction work, performance, and release QA are candidate families,
not yet a selected or frozen suite. Establish skill fit and independent outcome
criteria for each actual task before implementation. Preserve package dependencies
and qualify required harness/tools; a shortened prompt or unsupported delegation
path cannot silently stand in for the upstream package.

For the first comparisons, select one independently usable skill with its own
required resources. A collection's priority does not make its whole plugin or
workflow the treatment. Defer candidates requiring other skills or substantial
orchestration until the single-skill comparison is established. Qualify isolation
from operator instructions and machine state before interpreting the new pairs;
the [vision](VISION.md) owns that boundary.

The earlier shortlist below records development provenance. It does not determine
the next population: A remains an exposed development seed, B's narrow repair is
a weak fit for a design skill, and C's source collection is now deferred for
expansion. Ordinary tasks may be straightforward for a capable baseline; equal
quality with different consumption is still informative. Select cases before
seeing treatment outcomes, not only cases where vanilla fails.

## Workflow-skill backlog candidates

Source inspection on **2026-11-08** selected the operational-metrics console
view in [AgentMonitor's backlog](https://github.com/davisbuilds/agentmonitor/blob/0dd253129d143b5811224a0f80554a64eb3f4da0/docs/project/BACKLOG.md#operational-metrics-ui-surface-follow-up-to-the-shipped-ingestion).
At that revision, operational metrics have API and CLI readers but no frontend
consumer. The backlog's older claim that operators must use the API by hand is
stale; the missing console view is still real.

A bounded read-only view would make existing consolidation outcomes and skipped
work visible without changing ingestion, storage, provider configuration or
usage accounting. The interface still needs decisions about placement, filters,
refresh, unknown attributes, and empty/error/stale states. Those are meaningful
specification questions; neither an inferred health score nor a particular layout
is a predetermined answer. Ground acceptance in the maintainer's intended use and
existing API semantics before dispatch.

The existing isolated SQLite and real-app browser fixtures make this a plausible
routine task. Its pinned Node/pnpm/native dependencies and supported Chrome still
need VM qualification; no private session database or provider credentials belong
in the capture. The accepted [shared brief and source-selection policy](../../examples/document/operational-metrics/README.md)
withhold existing and archived spec directories equally from both arms while
retaining ordinary source and agent guidance. Archived plans remain a declared
source of related workflow examples under that policy. Source capture is prepared;
the task package, clarification policy, linked handoff and validated grader are
still outstanding. Pass the generated spec, original request and same selected
repository baseline to each fresh implementer.

[Dojo's version-aware skill-listing backlog](https://github.com/davisbuilds/dojo/blob/979b42a7366d4331ad65dd85f59b90b4efdb6737/docs/project/BACKLOG.md#installupdate-workflows-should-understand-skill-versions)
also contains useful specification work, but its normal source includes the
`write-spec` package. Defer it for this first comparison rather than deleting
ordinary source to create an apparently clean OFF condition. A later experiment
could explicitly study invocation when the skill is already available in-repo.

## Single-skill development candidates

Shortlist established **2026-10-30**, updated **2026-11-02**. Matt's debugging
task now has complete captures, ordinary environment validation and independent
outcome controls. Emil's UI stress test remains proposed pending browser
qualification. Addy's performance task needs complete reference packaging and a
reproducible workload. No skill-effect estimate is established. These cases all
use AgentMonitor; that convenience does not establish repository or language
diversity.

Preserve the task snapshot's ordinary guidance and dependencies in both arms.
Inspect references to unavailable external guidance before capture; supply the
necessary portable context equally rather than importing operator instructions.
Keep historical repairs, their added tests and later history outside the task
workspace. The maintainer-facing provenance below is not the solver's brief.
Preserve each skill's MIT license and complete required resources at its pinned
revision. No candidate requires a second skill or delegation.

### 1. Diagnose missing completed sessions

**Selected development slice.** AgentMonitor's
[repair f700961](https://github.com/davisbuilds/agentmonitor/commit/f7009616a272eba3ff96bcb851457cc2b8e4b66c)
restored access to compressed Codex rollouts across discovery, import, session
sync and related readers. Candidate starting snapshot:
[5f681e2](https://github.com/davisbuilds/agentmonitor/tree/5f681e21c8af7766bb21efe60d15bae33c4ac68b).
This is an exposed development repair, not an unexposed holdout. The
[capture and observer](../../examples/repair/completed-sessions/README.md) preserve
the full ordinary source and selected skill resources. The baseline backlog
already names the compressed-file defect and suggests a fix. Keep that context
equal across arms: this task measures repair completeness and verification,
not discovery of a concealed cause.

**Proposed shared brief.** Some completed Codex sessions are missing from the
session browser and usage history, even after a forced sync, while active sessions
still appear. Diagnose and repair this using the supplied synthetic session
directory. Preserve existing session identity, messages and usage when files
change after a session finishes; repeated imports must not duplicate them.
Provide a patch and verification evidence. Supply representative files and the
ordinary product contract, without naming the faulty predicate or prescribing a
decompression helper.

**Skill package.** Matt Pocock's
[diagnosing-bugs at a496f55](https://github.com/mattpocock/skills/tree/a496f5513d4a4cf889bc48e189e579f4b0bdf6aa/skills/engineering/diagnosing-bugs).
Entrypoint: `skills/engineering/diagnosing-bugs/SKILL.md`. Preserve its
`scripts/hitl-loop.template.sh` and `agents/openai.yaml` too. It has no mandatory
companion-skill calls. Its proposed contribution is a discriminating reproduction
and a repair verified against the original symptom, not a required solution shape.

**Independent outcomes.** Exercise real temporary files and production readers:
first-seen compressed rollouts, plain-to-compressed transitions, both forms
present, repeat import/sync, project repair and session-total checks. Verify
message/usage fidelity, stable identity, deduplication and ordinary plain-file
behavior. Audit failure handling against existing contracts. Accept alternate
implementations; matching the historical patch or following diagnostic phases
earns no credit. Freeze the first repair's scope before adding later disk-size or
unchanged-file efficiency requirements.

**Environment and interaction.** The parent pins Node 24.22.1 and pnpm 11.28.6;
qualify its lockfile, native SQLite dependency, zstd support and existing tests in
the task VM. Use synthetic files and a temporary database, never a real session
store. An unattended reproduction should suffice. The upstream skill shows
ranked hypotheses without requiring a reply; its HITL fallback stays available
but is not inherently needed here. Capturing inputs does not qualify their
execution; the selected image, resource profile and ordinary commands require
fresh VM evidence.

### 2. Stress-test the Sessions table

**Authored engineering commission on real product source.** At AgentMonitor
[823f57d](https://github.com/davisbuilds/agentmonitor/tree/823f57de1ef7cad0d7f3f2fa9cd2f14121c375c6),
`frontend/src/lib/components/sessions/SessionsPage.svelte` renders the session
list and expanded subagent rows through the shared `ui/DataTable.svelte`.
No historical request or particular UI defect is asserted.

**Proposed shared brief.** Stress-test the Sessions list and expanded subagent
rows with realistic extreme titles, project names, dates, counts and collection
sizes. Deliver a reproducible local preview and evidence-backed findings with
source locations and proposed fixes. Preserve normal behavior and leave product
fixes for a separate decision. A result with no confirmed breaks is valid.

**Skill package.** Emil Kowalski's
[break-ui at a81dbca](https://github.com/emilkowalski/skills/tree/a81dbca07f2217b7103e510c0f13f8d5b017afc9/skills/break-ui),
containing `skills/break-ui/SKILL.md` and `CATALOG.md`. References to other skills
describe scope boundaries, not required invocations. Preserve the complete catalog.

**Independent outcomes.** Assess realistic, type-correct fixtures; reproducibility;
retained baseline behavior; content readability and action reachability at declared
widths and enlarged text; and whether findings correctly identify observable
failures and source causes. Check consequential misses and false positives using
independent browser observations, allowing novel findings and unresolved cases.
Assess empty, single and large collections where supported. A toggle is a useful
deliverable, not proof of UI quality or skill benefit.

**Environment and interaction.** Qualify the snapshot's Node/pnpm lockfile,
Svelte build, isolated API/database fixture, browser and screenshots. Its
`e2e/sessions-search.spec.ts` demonstrates temporary database/server setup; it does
not establish this task's coverage. Render the actual component through its data
boundary. Retain root/frontend guidance, design tokens and assets. Upstream
requires a dev-only dataset toggle, a report, then a stop before product fixes;
this commission ends there faithfully. Browser evidence is required for this
candidate even though upstream permits explicitly labeled CSS inference. Repair
or design-selection follow-ups would need separately declared interaction rules.

### 3. Improve filtered Usage latency

**Real historical performance seed; workload qualification pending.** AgentMonitor
[repair 7353bfd](https://github.com/davisbuilds/agentmonitor/commit/7353bfd6e6142c2fcdd557d68f5fa090022a3668)
improved date-windowed, agent-filtered usage reads. Candidate starting snapshot:
[17ecd20](https://github.com/davisbuilds/agentmonitor/tree/17ecd20ddaca5a7274978fb8df451feea263681d).
Historical timings came from a particular store and are not transferable budgets.

**Proposed shared brief.** Usage overview is slow when a large retained history
is filtered to a recent date range and a common agent. Improve that scenario
without changing exact current/previous-period results or materially regressing
rare-agent and unbounded-history queries. Deliver a patch and repeatable
before/after evidence. Do not prescribe an index, query hint or cache.

**Skill package.** Addy Osmani's
[performance-optimization at eb07f19](https://github.com/addyosmani/agent-skills/tree/eb07f19f52c2669f2364897aaa293e2aaddd7b1c/skills/performance-optimization).
Preserve `skills/performance-optimization/SKILL.md`, its
`references/optimization-patterns.md`, and the repository-root
[references/performance-checklist.md](https://github.com/addyosmani/agent-skills/blob/eb07f19f52c2669f2364897aaa293e2aaddd7b1c/references/performance-checklist.md).
The root reference sits outside the skill directory: retain the relative layout
so both upstream links resolve. Copying only the skill directory is incomplete.
No companion skill is required; retain frontend/field-monitoring guidance and
state its applicability to this backend assignment rather than deleting it.

**Independent outcomes.** First construct a fixed synthetic history with realistic
agent/date skew and establish that the parent exhibits the symptom. Freeze exact
rollup parity checks across supported filters, measurement conditions, repeated
timings, variance and acceptable regression budgets before attempts. Require an
improvement beyond noise with correctness preserved, including the built HTTP
path; assess write/storage/rebuild costs if a solution changes them. Query plans
can explain a result but are not an exclusive answer key. The historical repair
and its plan tests remain external reference evidence.

**Environment and interaction.** The parent pins Node 24.13.0 and pnpm 10.29.3.
Qualify its lockfile, native SQLite build, benchmark/profiling tools and isolated
server in the VM. No human interaction is inherently required. No synthetic
workload or performance budget has been captured or validated yet.

### Deferred package boundaries

Cursor pstack's
[blast-radius at 4dd1d05](https://github.com/cursor/plugins/blob/4dd1d05818d19792e7cd6e818f1667f95f7e1dbe/pstack/skills/blast-radius/SKILL.md)
explicitly consumes `why` and `unslop`, with `arena` for wide changes. Defer it
rather than deleting those calls. gstack's
[qa-only at 723b73e](https://github.com/garrytan/gstack/blob/723b73ea897057a931c2576ca92f4ffe44fd8bc7/qa-only/SKILL.md)
requires shared preamble/lifecycle/question helpers and, for browser work, setup
and templates from sibling `qa`. Its complete support closure and runtime need
separate qualification; `qa-only/` alone is not a faithful package. Emil's
`prototype` also pauses for human variant selection, so it is not the first
unattended candidate.

## Debugging expansion shortlist

Source-inspected on **2026-11-05**, before any treatment runs for these cases.
Keep the complete pinned `diagnosing-bugs` package above as the common treatment.
These are proposed development tasks, not captured, qualified or unexposed cases.
Their difficulty is a hypothesis. Select ordinary and challenging work before
looking at ON/OFF outcomes; two tasks from one repository remain clustered.

| Proposed task | Independent outcome | Main preparation gap |
| --- | --- | --- |
| fetchmd: reject private hosts expressed as IPv4-mapped IPv6 | Apply the existing address policy consistently to URL literals, DNS answers and redirects while preserving allowed public addresses | Separate frozen dependencies; ordinary build/render-test coverage |
| Promptfoo: negative sample counts unexpectedly return tests | No extra samples for nonpositive counts; preserve valid counts, input data and a real caller's behavior | Frozen npm workspace, native dependencies and ordinary checks |
| Promptfoo: overlapping jobs change a shared tracing receiver | Stable first-start configuration, per-job policy, actual ingestion and recoverable concurrent start/stop/failure | Qualified local HTTP/store fixture and Node toolchain |
| Dojo: valid quoted versions appear unknown in an installed-skill audit | Decode supported single-line scalars without changing drift/sync decisions or accepting malformed versions | Resolve repository-skill exposure; review terms before redistributing a capture |

This historical shortlist is deferred behind the workflow-skill pilot. Source
review on **2026-11-08** classified fetchmd as a small repair with useful
verification edges; no solver trials established its difficulty or skill benefit.
Keep the Dojo case as a reserve until its ordinary repository skill content can
be characterized without weakening the single-skill comparison. Local source
inspection does not require publishing captured code. No extra tasks have been
run or selected for favorable skill results.

Pinned source and reference repairs:

- **fetchmd:** baseline [141cf8b](https://github.com/davisbuilds/fetchmd/tree/141cf8bcf015b23df06373e86bd9d36876efc53e),
  repair [4214628](https://github.com/davisbuilds/fetchmd/commit/421462878829dd93b812845cb730474dd3c7670d).
  Preserve the existing private-address contract in `docs/system/ARCHITECTURE.md`.
  Use deterministic DNS/fetch seams and synthetic addresses; no contact with real
  private services. Reject block-all-IPv6 and literal-only shortcuts. The Node
  range permits the existing runtime, but pnpm 11.5.2 and this lockfile need their
  own dependency image. Preserve the baseline's build-before-E2E prerequisite.
- **Promptfoo sampling:** baseline [d68b07d](https://github.com/promptfoo/promptfoo/tree/d68b07d51b7a92c109f4e367efaa644ac2fed7c4),
  repair [1a19309](https://github.com/promptfoo/promptfoo/commit/1a193092dd602e3d7732d87251d76a7f3a5e9381).
  Inspect `src/util/generation.ts` and dataset-generation callers. This ordinary
  repair may saturate quality; equal quality with different consumption remains
  useful. Preserve root, utility and test guidance; use a fixed local dataset.
- **Promptfoo tracing:** baseline [e8cef0e](https://github.com/promptfoo/promptfoo/tree/e8cef0e2bf9c4cc36316f193b4d28261d083458d),
  repair [8f6b847](https://github.com/promptfoo/promptfoo/commit/8f6b847a48acab5a064edb1f849ff54b3e019530).
  The ordinary tracing docs already promise startup-setting stability; retain
  that context. Test real local requests and stored synthetic spans across
  concurrent lifecycle transitions, not only mocked method counts. Both Promptfoo
  snapshots need their Node 24.22.2/npm 11 environment and substantial workspace
  dependencies qualified; the current AgentMonitor image is insufficient.
- **Dojo:** baseline [0ef16c7](https://github.com/davisbuilds/dojo/tree/0ef16c71a454863269920d9b0b5eeb074bd931f3),
  repair [d11a05d](https://github.com/davisbuilds/dojo/commit/d11a05dceec320dc45a3b2c4a7a3146b5676f00b).
  Exercise the public audit command with disposable canonical/installed roots,
  preserving Python 3.12 and the scalar-format contract. Include malformed inputs
  and unchanged sync behavior. No installed personal skill tree belongs in input.

Historical fixes and their added tests stay outside solver workspaces. Build
independent checks that reject plausible incomplete repairs and accept alternate
solutions, then demonstrate baseline/reference polarity before comparisons.

## Earlier development shortlist

Shortlist dated **2026-10-28**, not a frozen task suite or launch authorization.
Candidate A now has a captured development snapshot, repair adapter and live
polarity controls. C has a captured source packet and document adapter; B remains
an uncaptured proposal. None is an admitted
evaluation task or established representative sample.
Each comparison uses one complete pinned skill package explicitly ON versus OFF;
there is no upstream-versus-adaptation comparison. Public repository visibility
and linked revisions were checked through GitHub. Source and history inspection
establish candidate provenance. The separate development controls below have a
bounded scope; they do not establish grader accuracy or skill effectiveness.

The initial development route started with **A**. It
fits the existing Python/Linux direction without browser or Apple tooling. B and
C deliberately broaden the population beyond code review. The existing native
review workflow cannot define their deliverables: repair tasks need collected
patches and executable outcomes; writing needs a document and semantic assessment.
The [vision](VISION.md) owns treatment and measurement principles.

## A. Python repair: validate evidence before accepting a receipt

**Real historical seed.** OpenBench's [repair
47e49af](https://github.com/davisbuilds/openbench/commit/47e49af284218f3cb9c116ca2aa20bbbe30f7afa)
changed receipt admission to recompute control verdicts rather than trust saved
pass/score claims. Its parent,
[2afa7b3](https://github.com/davisbuilds/openbench/tree/2afa7b355a48f8aa6f81eb071db9114ffdbca02b),
is the candidate starting snapshot. The change touches production validation,
tests, and contract documentation. This is a repair assignment, not a request to
find issues in the fixed diff.

**Skill.** [Dojo diagnose 2.0.1 at
0b892b4](https://github.com/davisbuilds/dojo/tree/0b892b4543c12837b7cdbcd603abd0572954522d/skills/diagnose).
Its plausible contribution is a faithful failing reproduction, discrimination
between source identity and behavioral evidence, and a repair checked against
the original failure. These are hypotheses, not grading requirements.

**Snapshot and task.** Export the parent source, ordinary guidance and existing
tests without later Git history. Derive a symptom-led brief from the public
contract: edited saved verdicts must not admit failing controls; valid evidence
must remain usable. Do not reveal the historical patch or prescribe its algorithm.
Retain the fix and later tests outside the agent workspace. The original reduced
capture remains retained. The current dependency-restored generation preserves
ordinary data, experiment trees, gateway fixtures and required historical Git
object closures. Imported benchmark working-tree payloads remain outside the
declared validation scope. The reference repair and its changed blobs remain
outside the baseline capture. See the
[environment record](../../examples/repair/receipt-admission/environment.md).

**Outcome checks.** Independently author hidden acceptance checks for forged
verdicts, changed control declarations, legitimate receipts, fresh returned
observations and relevant error behavior. Exercise real temporary files and the
production validation path; isolate only the external grading dependency with a
controlled replay. Demonstrate that checks reject the parent defect, accept the
historical repair and reject an always-fail/always-pass shortcut. Preserve ordinary
regression results. An arm-blind semantic assessment checks scope, contract
preservation and whether the claimed verification matches the evidence; patch
similarity and following the skill's steps earn no credit. Semantic recording and
outcome selection are implemented; original solver/judge evidence retains its
earlier capture identity and independent audit remains pending. The newly authored
[observation adapter](../../examples/repair/receipt-admission/README.md) passed
live polarity controls: the historical repair passed all ten checks, the parent
failed six, always-accept failed all ten and always-reject failed the valid-receipt
check. Counts describe related controls, not independent task samples.

**Environment and limits.** The parent declares Python >=3.11 and a stdlib-only
runtime. The Python/Linux route ran focused project tests on both parent and
historical repair in the exact guest image. Qualify changed tools and inputs;
execute no imported source on the host. Controlled replay can assess receipt
decision semantics, not real Docker grading or isolation. A claim about the full
production grading boundary would require additional runtime capabilities and
controls. Writable repair collection and separate offline checks are now
implemented. Live worker controls covered allowed reads/scratch writes and denied
source writes, checker reads through direct/link/subprocess routes, and a tested
external connection. The restored capture reproduces the same two root-sensitive
failures and eight skips in baseline/reference unit runs. These are explicit
exceptions, not a clean CI result. Required tool-dependent lanes, full-workload
admission and representative grader reliability remain open; the bounded
environment seal does not grant them.

## B. Browser implementation: preserve user settings across failure states

**Real historical seed.** The Luminal [repair
366e813](https://github.com/davisbuilds/oneshots/commit/366e81304e298d9203c81d97d954fec4346bbdaf)
fixed disabled flashes that still rendered and startup failure when storage was
blocked. Candidate parent:
[b91ed0e](https://github.com/davisbuilds/oneshots/tree/b91ed0e77054a6f93c44a89324ad7f588159ec3e).
The source under `runs/luminal/` includes the game and browser verifier.

**Skill.** [Dojo frontend-design 2.2.0 at
0b892b4](https://github.com/davisbuilds/dojo/tree/0b892b4543c12837b7cdbcd603abd0572954522d/skills/frontend-design).
Its plausible contribution is treating preference/error states as part of the
experience and checking the rendered interaction. It may offer little incremental
value for this narrow defect; development should test that assumption.

**Snapshot and task.** Preserve the parent game's normal assets, tooling and
instructions. Ask for a bounded implementation that honors the existing flash
opt-out and reduced-motion defaults even when persistence is unavailable. Keep
the repaired source and added verifier assertions outside the workspace. This
historical source is available; an evaluation snapshot is not yet captured.

**Outcome checks.** Independent browser checks cover denied storage, fresh and
saved preferences, disabling an active flash, death/transition effects, and
ordinary enabled behavior. Retain browser observations and rendered evidence;
checking only internal state cannot establish suppression of the rendered effect.
Use existing gameplay checks for regression coverage. Blind semantic assessment
checks preserved user control and scope, accepting alternate implementations.

**Environment and limits.** The parent uses Node >=24, npm 11.6.2 and Playwright
1.63.0; the game requires WebGL2 and browser audio support. Qualify those exact
dependencies, browser rendering and offline assets in a new Linux task image.
The current Python image does not establish this environment. No macOS or live
human interaction is inherently required; visual evidence and its assessment are
still unqualified. Do not run a full film/render workflow merely to grade this fix.

## C. Source-grounded marketing writing

**Expansion deferred as of 2026-10-30.** Preserve this completed development
generation and its unaudited assessments; prioritize the engineering sources above
for new tasks.

**Real product source; authored commission, not a historical case.** AgentMonitor's
[README at
fe6f461](https://github.com/davisbuilds/agentmonitor/blob/fe6f46138cad1191606ad0df5985f8fd3b5eb42d/README.md)
describes a local, single-user dashboard for coding-agent sessions, usage and
estimated costs, with important differences in provider coverage and optional
features. Use that actual product snapshot to commission a compact product-page
hero and introduction for developers. No historical writing request or customer
response was established by this inspection.

**Skill.** [Corey Haines copywriting 2.3.1 at
e85f3cb](https://github.com/coreyhaines31/marketingskills/tree/e85f3cbc2e4a79bf33b2a12e9bdd9087d414735b/skills/copywriting).
Plausible contributions include preserving an existing voice, concrete benefits,
supportable claims and a clear action. Supply the same settled audience, action,
format and factual source packet to both arms; avoid injecting the skill's drafting
procedure into the shared brief.

**Snapshot and assessment.** Capture the source packet and brief, not a running
installation or personal session data. Objective checks cover requested sections,
length bounds and artifact integrity. Independent, arm-blind semantic assessment
checks every material product claim against the packet, preservation of important
limits, audience usefulness, clarity and action specificity. Use cited judgments,
an unresolved option and human adjudication. Do not treat keyword/style compliance,
an author's preferred draft, or a judge's taste as conversion evidence.

**Environment and limits.** Text/files only: Linux can support this task without
browser, credentials, customer access or external requests. The
[document adapter and authored controls](../../examples/document/product-page/README.md)
are implemented using the shared task/environment package. Independent writing
rubric calibration remains pending. The task tests artifact quality, not observed
conversion or commercial impact.

## Selection, headroom and independent evidence

Before any development run, freeze source/skill bytes, ordinary guidance, task
brief, package exposure, budget, attempt policy and outcomes. Inventory guidance
already overlapping the skill. Keep tools/capabilities equal or explicitly declare
a complete-package capability treatment. Record failed consultation without
excluding the run. For A/B, the historical fix is evidence about intended behavior,
not an exhaustive answer key or the only acceptable solution.

Use these exposed seeds to check task complexity and headroom: does ordinary
capable-agent work require meaningful reasoning, can checks distinguish plausible
wrong results, and do budget/quality outcomes avoid universal success or failure?
Record mundane and challenging work separately. A's small patch and C's already
strong source copy make ceiling effects plausible. If a seed is saturated, retain
that observation and reconsider the population; do not make it artificially harder
by withholding normal context or selecting only baseline failures/skill wins.

Development results cannot establish skill benefit. Before an evaluation, select
separate unexposed cases using declared repository/date/task-type rules, independent
of treatment outcomes; freeze acceptance criteria and audit representative labels
before examining arm results. Separate holdouts by source case/defect lineage;
mutations, paraphrases and related checks of one seed are controls, not independent
tasks. If an independent held-out population cannot yet be assembled, report only
development evidence. Tiny authored controls establish neither task difficulty nor
agent ability.

Report task quality, accepted outcomes, failures and uncertainty alongside actual
tokens, cost and elapsed time. Include skill/context overhead and failed-attempt
costs, separate grading from task-agent consumption and setup from agent time,
preserve unknown usage, and distinguish estimated prices from observed charges.
The [environment](../system/ENVIRONMENT.md), [native
workflow](../system/NATIVE.md) and [study contract](../system/STUDIES.md) retain their
existing limits; this shortlist admits no scored work.
