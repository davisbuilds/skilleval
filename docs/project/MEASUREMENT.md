# Measurement design and task acceptance

This reference owns how skilleval chooses useful work and decides what evidence
would justify a comparison. [Task packages](../system/TASKS.md) own the executable
interfaces; [studies](../system/STUDIES.md) own dispatch and accounting. An
acceptance map records a measurement argument. It cannot certify its own validity.

## Start with the decision and the work

State the decision before collecting tasks: for a pinned skill, model and harness,
what real work would someone delegate, and what improvement would make the skill
worth using? Keep accepted outcomes, serious regressions, verification quality,
tokens, time and conditional cost separate. Specify a combined utility or numeric
weights only when a real decision supplies their meaning, before seeing results.

The starting material is an **ordinary work record**, not a generated list of
assertions. Retain the original request/issue, observable symptom or desired
artifact, relevant discussion, pre-work repository revision, ordinary instructions,
contracts, examples/data and usable environment. Record what was adapted or
omitted and why. Retain reference fixes and upstream tests outside solver input;
they supply evidence about intended behavior, not the only acceptable solution.
Preserve both successful and unsuccessful candidate selections and exclusions.
Ask a task-domain maintainer what would make the result usable or mergeable and
what would require rework. Record ordinary frequency, consequences and review effort
when those properties matter to the intended decision. Separately state why the
task falls within the skill's intended scope and what outcome it could improve;
the skill's preferred procedure is not the acceptance contract.

SWE-Lancer starts from real paid tasks and uses engineers to build and review
checks of user workflows; its curation avoids restricting candidates to fixes
that already added tests. That selection lesson matters even when a task requires
semantic review. [Construction and curation](https://arxiv.org/html/2502.12115v1#S2.SS2)

Before expanding a suite, describe its sampling frame: repositories, task families,
languages, ordinary versus challenging work, time range, selection/exclusion rules,
and public exposure or prior use. Popular skills or convenient bug fixes do not
establish a representative workload. Cases from one incident or template share
provenance. Keep task development, grader validation and effect-estimation holdouts
separate; do not select the last group from observed vanilla failures or skill wins.

## Build the acceptance argument

Write what successful work means before implementing checks, then connect it to
observable evidence and situations that produce it. This adapts evidence-centered
design's claim/evidence/task relationship; it does not import educational ability
models or imply psychometric calibration. [Mislevy, Almond and Lukas](https://cresst.org/wp-content/uploads/R632.pdf)

For each requirement, record:

- The user-relevant outcome, its source and why it belongs in this task. Distinguish
  an explicit request, existing repository contract and operator-added obligation.
- Its role (`core`, `regression` or `evidence`) separately from its importance
  (`required` or `supporting`). Importance is a declared judgment, not a learned score.
- Concrete observations or a semantic question that could support or refute it.
  Give an exact probe/field route rather than relying on a composite pass flag.
- Normal, boundary, failure and state-transition cases worth inspecting, plus
  omissions and the resulting limit on the claim. Requirements without adequate
  coverage remain gaps even when every implemented probe passes.
- Valid alternative implementations and counterexamples that a grader must
  distinguish. Do not award points for following skill instructions or resembling
  the reference patch.

Audit the mapping in both directions: does each important requirement have useful
coverage, and does every check demand behavior justified by the brief or ordinary
contract? Hidden filenames, incidental ordering and reference-specific helpers can
reject valid work. A passing reference alone does not establish fair acceptance.

The [completed-session template](../../examples/repair/completed-sessions/acceptance-map.template.json)
is a retrospective example. Its ten requirements map six composite probes and
explicitly retain the uncovered cache handover, corrupt replacement and packet
coverage gaps. Neither ten nor six is a sample-size target. The original six-probe
results stay unchanged. Probe counts are diagnostics; this implementation produces
no aggregate requirement-quality score.

Measurement research separates defining the concept, building an instrument and
validating its use. Review whether the map captures the intended work as well as
whether its code executes consistently; reproducibility alone does not settle
relevance or completeness. [Wallach et al.](https://proceedings.mlr.press/v267/wallach25a.html)

## Validate the task and the grader

Use the actual production paths and frozen environment for behavioral controls.
Exercise the original defect, reviewed positive/alternative repairs, plausible
incomplete repairs, regressions and unresolved cases. Where useful, perturb one
consequential behavior, generate bounded state transitions, or check invariants
across equivalent inputs. Such related controls challenge an instrument; they do
not become independent tasks. Record expected discrimination separately from the
observed evidence and preserve unsuccessful controls.

An independent task-domain reviewer should assess brief sufficiency, scope and
acceptance conditions. A particularly strong check is an independent implementation
from ordinary task context without seeing the reference solution. SWE-Bench Pro V2
describes this check for repaired instructions. Its specific choice to retain tests
is not a rule to preserve a defective skilleval verifier: correct defects in a new
version with fresh control evidence. [Engineer review](https://arxiv.org/html/2610.24206v1#S3.SS3)

Use deterministic observations for objective behavior and semantic assessment for
claims, completeness, alternatives and consequential omissions. Audit the whole
path from raw execution evidence to judge-visible material; a judge cannot recover
facts filtered out of its packet. Calibrate model judgments against independently
reviewed representative examples. Preserve disagreements, missing evidence and an
unresolved outcome. [Agent evaluation guidance](https://www.anthropic.com/research/demystifying-evals-for-ai-agents)

Track false acceptance, false rejection, unresolved, missing and invalid results
with explicit denominators per criterion/task family. Record human-human disagreement
as well as judge agreement. Judge repetition tests stability, not truth; several
models may share a mistake. Choose error tolerances appropriate to the decision
and report uncertainty instead of declaring a universal agreement threshold. An
attached review file establishes provenance only; it is not authenticated independent
approval. Existing [reliability](../system/RELIABILITY.md) and
[calibration](../system/CALIBRATION.md) evidence retain their stated limits.

## Plan comparisons before dispatch

For explicit-use comparisons, estimate the effect of assigning the skill across
the declared tasks. Do not discard ON attempts because consultation was not observed.
Pin the skill closure, ordinary context, model/harness/environment, budgets,
first-attempt/retry rules, primary outcomes, exclusions and stopping rule. Preserve
all attempts, failures and unknown consumption. Skill/context overhead belongs in
solver consumption; grading and infrastructure spend remain separate.
This comparison estimates the effect of the supplied skill package. Claims about
which instruction or resource caused the effect require additional controls or
ablations; they are not implied by an ON/OFF difference.

Use paired same-task ON/OFF observations. Summarize repetitions within task and
account for shared repository/task-family provenance in uncertainty estimates.
Choose independent tasks and repetitions from the smallest useful effect and desired
precision/power, informed by variability. Repeating one exposed task cannot establish
transfer. No fixed number of probes, cases or repeats supplies qualification.
[Miller's statistical treatment](https://arxiv.org/html/2411.00640v1)

METR's hierarchical bootstrap across families, tasks and attempts is one concrete
precedent. It does not eliminate external-validity uncertainty or make intervals
reliable with too few independent groups. Until the population and grader support
inference, report task-level descriptive results. [METR methods](https://metr.org/blog/2025-03-19-measuring-ai-ability-to-complete-long-tasks/)

## Tools and current boundary

Use ordinary issue/PR and Git tooling to retain source work, runtime containers/VMs
to reproduce it, the repository's native tests and database/API/browser observations
to inspect behavior, and bounded mutation/property/state-transition checks where
justified. A readable evidence viewer, reviewer annotations and a versioned manifest
matter more than choosing another all-in-one benchmark runner. Statistical notebooks
or established analysis libraries are appropriate once the analysis and independent
sample units are defined; no new analysis dependency is selected here.

The implemented operator acceptance artifact pins task/package identities, validates
probe IDs and JSON Pointers, records semantic routes and open gaps, and hashes optional
control/review attachments. Mapped study plans recheck it before each dispatch.
Its output always leaves semantic validity unassessed and scored admission false.
Legacy/unmapped workflows remain available for compatibility and are labeled as such;
new repair/document comparisons should use the mapped policy. Raw result selection,
mechanical acceptance and legacy judge packets retain their existing semantics.

Before the next substantive comparison, repair the known packet omissions and establish
appropriate controls for the mapped gaps. Methodology metadata and retrospective maps
do not lift those limits. Independent grader qualification and a defensible held-out
population remain prerequisites for scored skill-effect claims; engineering work and
offline contract tests can proceed without restarting model experiments.
