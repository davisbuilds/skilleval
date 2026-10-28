# Development study orchestration

The study controller runs explicit native review comparisons while independent
grader audit remains pending. It never grants scored admission or converts an
execution status into a correctness score. Judge reliability controls and native
assessment recording remain separate workflows.

This document describes the implemented natural-availability controller. The
selected next scope is explicit-use ON/OFF evaluation with joint quality and
efficiency reporting; see [the roadmap](../project/ROADMAP.md). Current usage fields
are retained observations, not validated aggregate token accounting or prices.
Existing plans and runs keep their original treatment identity.

## Freeze and execute

`schema study-spec` owns the exact input contract. Start from
`examples/studies/development.template.json`, supplying frozen native task
snapshots, their separately retained digests, a captured skill bundle, and the
explicit runtime/image/catalog paths. Source images and task bundles stay outside
run directories. Task prompts are identical across the two arms. The native route
currently pins GPT-6.1 Sol/high; the observed Codex version remains in each outcome.
Duplicate task snapshots are repetitions, not independent task IDs.

```sh
uv run skilleval study prepare study-spec.json --output study-plan.json
uv run skilleval study run study-plan.json --sha256 PLAN_SHA256 \
  --output .local/study-run --auth-file /explicit/host/auth.json --development
uv run skilleval study report study-plan.json --sha256 PLAN_SHA256 \
  --output .local/study-run
```

Planning does not launch a VM or read credentials. It freezes prompt/task/skill
identities, runtime and control-source fingerprints, image identity, native
budgets, retry limit, and expanded dispatch order. Task/repetition blocks are
shuffled with the declared seed; each adjacent pair has randomly ordered absent
and available arms. Execution is sequential. Natural skill availability is the
only treatment; forced consultation is outside this comparison.

Retain the returned plan digest separately. Changed inputs or code require a new
plan. Before every attempt, including retries, the controller rechecks the frozen
inputs and current execution identity. If the spec includes an admission path and
digest, it also revalidates the seal at that boundary and checks the selected
runtime/image/catalog route. A bounded-control seal still does not qualify a new
task, grader or scored campaign. Omitting the seal is permitted only for explicit
development runs. There is no scored override or automated human-audit approval.

The preparation receipt reports reserved attempt and provider-request ceilings.
Each attempt has the native execution timeout and gateway limits. VM setup and
teardown have their existing individual limits; this does not impose a separate
whole-study wall-clock deadline or infer a token/currency budget from missing
provider telemetry. Output parent directories must already exist.

## Attempts, interruption and recovery

One local controller holds a nonblocking process lock on a study output. Different
study outputs are not coordinated by a global scheduler. A complete dispatch
record is published and synced before native execution starts. Attempts have
separate fresh directories, never overwritten; retries consume the prespecified
per-cell limit and retain earlier failures. Unpublished temporary reservations
cannot have launched a VM and are not counted as attempts.

SIGTERM or keyboard interruption stops further dispatch. A crashed controller
leaves its attempt unfinished, and resume refuses to silently repeat it. The
existing native recovery ticket owns VM cleanup. Stop the old controller, recover
any retained VM, then reconcile:

```sh
uv run skilleval qualification recover NATIVE_ATTEMPT_DIRECTORY
uv run skilleval study reconcile study-plan.json --sha256 PLAN_SHA256 \
  --output .local/study-run
```

Reconciliation inspects terminal cleanup evidence without launching a VM or
reading credentials. It appends an outcome or a recovery sidecar; original native
evidence and earlier outcomes stay unchanged. Unknown elapsed time after a crash
stays null. Run the same `study run` command to continue eligible remaining cells.
Recovery still requires the original runtime/control-source identity. Neither the
lock nor reconciliation is a host-loss watchdog or automatic recovery service.

## Reporting and assessment policy

Primary comparisons always use the first attempt for each cell. A successful
retry cannot replace an earlier failure in that comparison. All later attempts,
unstarted cells and incomplete cleanup remain visible. Paired elapsed differences
require completed first attempts, known elapsed values and matching observed
harness identities; elapsed includes setup and teardown. Usage is retained with
its reported fields, and missing usage remains null.

Task quality and quality differences remain unassessed. Native completion, a skill
read, a parseable judgment or a stable judge is not correctness or skill benefit.
The human audit packet remains frozen. Before scored studies, extend admission to
the actual task/treatment/dispatch route, resolve independent grader validation,
and freeze duplicate/severity/coverage and assessment-selection policies. Known
defect probes do not measure exhaustive review recall.
