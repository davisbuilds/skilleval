# Vision

Skilleval should answer a practical question: on realistic tasks where a skill is
relevant, how does explicitly using it change a capable agent's task quality,
outcomes, token consumption, cost and time compared with the same agent without
it? Its first audience is people choosing, maintaining or evaluating skills for
work they actually delegate.

This is a standalone evaluator for independently supplied skills from any source.
No particular skill collection is privileged, and comparing an adaptation with
its upstream version is outside the initial scope. The initial experiment is
one pinned skill ON versus OFF. Source popularity can motivate selection; it
does not establish quality or effectiveness.

Start with a single independently usable skill, preserving its own references,
scripts, assets and required resources. Prefer packages that can be evaluated
faithfully without other skills or a larger orchestration workflow. If a package
requires that broader workflow, defer it rather than removing required behavior
and calling the result an evaluation of the original skill. Multi-skill and
workflow comparisons come later as separately declared treatments.

The unit of work is a reproducible task with an explicit environment and evidence
contract. The unit of comparison is a matched experiment with a vanilla baseline,
a precisely identified skill treatment, repeated attempts, and separately
versioned assessments. Preserve execution failures, missing observations, novel
findings, and inconclusive comparisons. ON explicitly requests use of the selected
skill through the harness's supported invocation route; OFF supplies the same
task without that skill or request. Keep the task brief, ordinary repository
guidance, environment, model/effort, capabilities and budget policy comparable.
Freeze the minimal invocation difference and complete skill package. If supplied
helpers change capabilities, declare that package treatment rather than attributing
its whole effect to prose. Record consultation failures without discarding them.
Natural discovery and selection are a later, separately identified experiment.

Operator machine state is outside the experimental input. Host parent-directory
and global instructions, personal skills, memories, plugins, hooks, configuration,
shell state and prior sessions must not enter either arm. The captured task
repository's ordinary instructions remain part of the shared input; inspect any
dependencies on unavailable external guidance before selecting the task. Record
the effective harness context and validate contamination controls on the actual
execution route, including a known-positive case. A fresh directory or an
unmatched text search alone does not establish isolation.

Review tasks supplied the first implementation and exposed historical cases
developed the instrument. The evaluation population must now follow each selected
skill's intended work and plausible contribution. Choose meaningful tasks and
independent outcome criteria before looking at treatment results; do not turn
skill instructions into a checklist that rewards their own use. Small authored
grader controls are sanity checks, not representative task evidence. Task selection must
describe the target population, repository and language mix, difficulty, natural
versus seeded defects, and exposure history. A convenient case collection alone
does not establish representativeness.

Use development pilots to identify ceiling effects, infeasible tasks and grader
gaps, then freeze separate evaluation tasks. Do not select the final population
only from vanilla failures or skill wins. Measure ordinary and challenging work
separately where relevant; difficulty alone is not relevance. Support the task
and grader adapters required by selected skills rather than forcing every task
into a review report.

Quality and efficiency are joint outcomes. Report task-specific quality, accepted
outcomes, failure rates, token categories, cost and elapsed time separately, with
paired differences and uncertainty. Include skill loading and subsequent context
overhead in task-agent usage. Keep evaluation/judge spend separate; retain failures
and retry costs under the declared attempt policy. An early cheap failure is not
an efficiency win, and higher quality at higher cost is an explicit tradeoff.

Validate usage semantics at the observed harness/provider version before summing
or pricing them; cached tokens, per-turn observations and totals can overlap.
Preserve missing fields as unknown. Distinguish provider-reported charges from
estimates using a dated, source-backed rate schedule; a price-equivalent estimate
for subscription-backed execution is not an observed bill. Separate agent time
from environment setup, cleanup and grading. Compare under the same budget ceilings
while retaining actual consumption, cache behavior and all attempts.

Success means a maintainer can inspect why a run was valid, why a judgment was
made, and what a comparison can support. A result can be useful when it shows no
clear difference. Generic methodological overlap with vanilla is a legitimate
outcome, not a reason to change the scoring after seeing results.

The project owns experimental contracts, task/environment packaging, evidence,
grading, and analysis. Skills remain independently versioned inputs. Repositories
providing task evidence remain independent projects. Import useful existing logic
through explicit, documented interfaces; do not depend on sibling checkouts or
carry forward their entire runners.

Executable checks establish objective outcomes where possible. Semantic judges
assess claims against source and observations, with evidence citations and an
unresolved option. Human adjudication handles disagreement, novelty, and calibration
samples. No lexical matcher or model judge alone is the authority on review
quality. Judge reliability must itself be measured before a score is used to
rank skills.

Initial non-goals: a universal benchmark leaderboard, automatic skill optimization,
multi-tenant hosted execution, an agent framework, or a new virtualization engine.
Python owns the experiment core; TypeScript is reserved for a later evidence and
review UI; native task tooling remains in its original language.
