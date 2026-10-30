# Explicit-use pilot candidates

## Current selection direction

As of **2026-10-30**, prioritize ordinary engineering work using skills from
[Addy Osmani](https://github.com/addyosmani/agent-skills),
[Matt Pocock](https://github.com/mattpocock/skills),
[Emil Kowalski](https://github.com/emilkowalski/skills),
[Cursor pstack](https://github.com/cursor/plugins/tree/main/pstack), and
[gstack](https://github.com/garrytan/gstack).
Defer new task selection involving blacktwist, charlie947, coreyhaines31, and
google-design-md. Existing writing captures, judgments, and pending audit
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

## Single-skill development candidates

Shortlist dated **2026-10-30**. All three candidates below are **uncaptured and
unqualified**: no task package, accepted outcome adapter or skill-effect result is
established by this inspection. Recommend **Matt's debugging skill first**, then
**Emil's UI stress test after browser qualification**. Addy's performance task
needs complete reference packaging and a reproducible workload first. These are
bounded development proposals, not a selected evaluation population. They all use
AgentMonitor; that convenience does not establish repository or language diversity.

Preserve the task snapshot's ordinary guidance and dependencies in both arms.
Inspect references to unavailable external guidance before capture; supply the
necessary portable context equally rather than importing operator instructions.
Keep historical repairs, their added tests and later history outside the task
workspace. The maintainer-facing provenance below is not the solver's brief.
Preserve each skill's MIT license and complete required resources at its pinned
revision. No candidate requires a second skill or delegation.

### 1. Diagnose missing completed sessions

**Real historical seed; first recommendation.** AgentMonitor's
[repair f700961](https://github.com/davisbuilds/agentmonitor/commit/f7009616a272eba3ff96bcb851457cc2b8e4b66c)
restored access to compressed Codex rollouts across discovery, import, session
sync and related readers. Candidate starting snapshot:
[5f681e2](https://github.com/davisbuilds/agentmonitor/tree/5f681e21c8af7766bb21efe60d15bae33c4ac68b).
This is an exposed development repair, not an unexposed holdout.

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
but is not inherently needed here. This inspection ran no imported code.

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
