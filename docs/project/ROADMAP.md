---
status: in-progress
updated: 2026-10-30
---

# Roadmap

Build a useful evaluator for independently supplied skills. The first comparison
is explicit skill ON/OFF on relevant tasks, measuring task quality and outcomes
alongside actual consumption. The [vision](VISION.md) owns the principles;
[backlog](BACKLOG.md) owns concrete unresolved work.

## Current foundation

The CLI supports immutable captures, task/environment packages, native Codex
execution, review/repair/document artifacts, independent offline checks, and
static or investigative semantic judgments. Development studies freeze paired
inputs and budgets, preserve every attempt, and produce readable artifact and
usage comparisons. [Task packages](../system/TASKS.md) and
[study orchestration](../system/STUDIES.md) own those contracts.

Runtime controls cover bounded filesystem/network behavior, task fidelity,
termination, recovery, and cleanup on the tested smolvm route. Admission is tied
to exact execution identities. Native context controls now exercise production
OFF/ON/fresh OFF, declared defaults, contamination detection and preflight refusal;
their [scope](../system/NATIVE.md#operator-state-and-effective-context) remains
separate from scored admission. Judge controls cover authored labels and selected
repeat/order/perturbation cases. **Independent semantic audit and scored-study
admission remain pending.** Development workflows can progress while those gates
are open; they cannot become scored trials by relabeling their results.

The [source register](SOURCES.md) records adopted ideas and implementation:
SkillsBench/Harbor packaging, Tessl fixtures and explicit variants,
Anthropic/Engram assessment patterns, gstack's separation of validity and benefit,
and the adapted OpenBench gateway. External skills remain experimental inputs.

## Now: useful engineering tasks and independent judgment audit

1. **Broaden the skill-relevant population.** Start with the engineering source
   priorities in the [candidate register](PILOT-CANDIDATES.md#current-selection-direction)
   and concrete work maintainers actually delegate. Discuss task briefs, skill fit,
   deliverables and independent outcome checks before capturing the next cases.
   Start with one independently usable skill and its required resources; defer
   packages that require a larger multi-skill or orchestration workflow.
   Repair and writing have exposed development captures; new engineering cases
   remain unselected. Preserve selection reasons and exclusions. Do not retain
   only vanilla failures or wins for the skill.
2. **Audit representative judgments.** Review actual artifacts, source support,
   novel findings, alternative solutions, checker defects, and disagreements.
   Tiny authored label controls test the grader machinery; their difficulty does
   not define the task population. Preserve the pending audit generation.
3. **Close task-specific environment gaps.** Choose and qualify the solver user
   profile and required tools. Before interpreting new ON/OFF pairs, verify the
   actual harness context excludes operator instructions and machine state, with
   contamination canaries and working positive controls. Keep baseline failures,
   skipped lanes, unsupported platforms, and artifact-budget limits explicit.
   Capture corrections do not upgrade earlier attempts.

These tracks can progress independently. Add an adapter when the selected task
requires it, rather than expanding harnesses or infrastructure in advance.
Maintain the public foundation and [hosted checks](GIT_HISTORY_POLICY.md) as this
work develops; publication is complete and does not qualify evaluation outcomes.

## Next: repeated, interpretable comparisons

Freeze task selection, exposure history, complete skill bytes, model/harness,
environment, budgets, primary outcomes, retry policy, and assessment selection
before dispatch. Keep ordinary context and capabilities comparable and vary only
the declared treatment. Randomize adjacent matched runs and retain unsuccessful
attempts and missing observations.

Use pilot variance and the intended decision to choose repetitions and sample
size. Report paired quality, failure, token, cost, and timing differences with
uncertainty that respects task/repository clustering. Judge spend stays separate;
conditional API-equivalent estimates are not observed subscription charges.
Inconclusive or saturated comparisons are useful outcomes.

Scored dispatch requires both applicable full-workload environment evidence and
independently supported grader reliability. A bounded runtime seal, parseable
judgment, model agreement, or successful skill read cannot satisfy those gates.

## Later

Expand to multi-skill/workflow treatments, natural discovery, the pi harness, and
a TypeScript evidence/review UI when the evaluation workflow demonstrates a need.
Protected holdouts, additional task families, and automation follow the same
evidence and accounting contracts.
A universal leaderboard and automatic skill optimization are outside the initial
scope.

## Development observations informing this sequence

These are maintainer-observed development results through 2026-10-29. Raw runs
remain outside the public source tree; this summary is not a reproducible public
benchmark release or an effectiveness claim.

- The repair capture reproduces 2,006 baseline and 2,010 reference tests, each with
  two declared root-sensitive failures and eight skips. This restored missing
  context/history while leaving environment coverage gaps visible. See the
  [task environment record](../../examples/repair/receipt-admission/environment.md).
- Semantic investigation found an observation-fixture defect in the first repair
  comparison. Corrected checks accepted both repairs; original workers, results,
  and judgments remain separate. The earlier apparent difference was not a valid
  functional advantage.
- Two distinct supported writing drafts and a format-valid false-cost claim
  matched ten prespecified authored judge expectations. One subsequent ON/OFF
  pair passed mechanical checks and all four document criteria in both arms.
  ON read the skill and used 98,644 total tokens and 49.10 agent seconds; OFF used
  58,335 tokens and 38.04 seconds. This rubric recorded no quality advantage in
  that pair; independent audit and repeat evidence remain missing.

These observations support better fixtures, representative task selection, and
independent audit as the next work. Detailed implementation history remains in Git.
