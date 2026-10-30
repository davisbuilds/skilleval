# Repair artifacts, checks and semantic assessments

Repair mode asks the agent to change a frozen source tree. It uses the same native
Codex/VM route as reviews, with a separate task-mode identity. It does not require
a review JSON response or trust a claimed solution, score or guest Git diff.
The [study controller](STUDIES.md) freezes explicit-use ON/OFF prompts and reports
task-agent consumption. Repair checks are a separate, credential-free operation.

## Freeze the task and checks

`schema repair-spec` owns the input contract. A task binds its source snapshot and
digest, shared prompt, exact editable paths, a Python observation worker and its
digest, named requests/expected observations, deadline and source provenance.
`repair prepare SPEC --output TASK` verifies those inputs, resolves paths and
publishes a task identity without launching anything. Retain its digest separately.
The initial patch adapter accepts portable ASCII paths containing letters, digits
and `_./@+-`; spaces and other encodings are unsupported. Preparation checks all
retained source paths, including unchanged context, before a native attempt.

Keep the worker, expected observations, reference solutions, earlier attempts and
task contract outside solver input. Only the frozen source and task prompt go to
the solver. Preserve ordinary guidance, tests, contracts and usable tooling; a
large repository export can omit unrelated datasets only with a recorded reason
and a workload fidelity check. Synthetic baseline Git history may be included;
later source history must not reveal the repair. Follow the
[task-fidelity contract](ENVIRONMENT.md#task-capture-and-baseline-fidelity) before
promoting a capture: ordinary test dependencies may include data directories and
historical objects named inside fixtures. Preserve failures and skips when comparing
untouched and reference source in the actual solver profile.

The [receipt-admission example](../../examples/repair/receipt-admission/README.md)
provides a public brief, observation adapter and contract template. It remains an
exposed development seed. Its independent fixtures exercise real receipt logic
with controlled task inspection and Docker replay observations. It does not
establish production Docker grading behavior or task difficulty. Use its versioned
current worker: the original fixture omitted production replay identity fields
and falsely rejected one valid repair. Preserve old task/check generations when
correcting a fixture; a semantic judge may identify a checker defect.

## Run and collect

Use `mode: repair` in a schema-3 study task, with the same input digest and shared
prompt as the repair contract. Native review/judge workflows remain supported.
Direct development invocation also accepts `native qualify --mode repair`.

```sh
uv run skilleval repair collect TASK --task-sha256 TASK_DIGEST \
  --run NATIVE_RUN --output COLLECTED_RESULT
```

Collection requires matching native input and prompt identities and completed VM
cleanup. It accepts the shared prompt or its minimal explicit selected-skill
prefix. Failed native execution can still produce an inspectable artifact; its
execution status stays in provenance and does not become a successful attempt.
If native cleanup failed, successful explicit recovery can make collection
eligible. Collection binds that recovery record and retains both the original
cleanup flag and the observed recovered state without editing the native receipt.

The collector verifies the native receipt and archive, rejects task links, and
compares collected source bytes/modes with the original. It publishes a candidate
snapshot, a host-generated binary-capable Git patch and a result identity. Guest
`.git`, `__pycache__` and `.pytest_cache` metadata are excluded explicitly. Ordinary
source/configuration/test changes remain visible. Host Git hashes exact blobs
without attributes or filters and compares separate trees. It uses isolated
settings and does not execute imported code or consume candidate Git configuration.
The candidate snapshot owns exact bytes; the patch is a review artifact.

Out-of-scope edits are retained as `invalid-scope` and cannot be checked as an
eligible result; the CLI exits 1. Original inputs and earlier results remain
unchanged. Source, checker or contract drift requires a new preparation; results are never overwritten.

`repair control TASK --task-sha256 ... --candidate SNAPSHOT --candidate-sha256 ...
--output RESULT` collects an explicitly authored control through the same byte
comparison. Its provenance states that it is not a model attempt. Use this for
untouched, reference, alternate valid and deliberately defective implementations.

## Observe in a fresh VM; compare on the host

```sh
uv run skilleval repair check TASK --task-sha256 TASK_DIGEST \
  --result COLLECTED_RESULT --result-sha256 RESULT_DIGEST \
  --runtime RUNTIME --image-archive IMAGE --image-sha256 IMAGE_DIGEST \
  --output CHECK_RUN
```

This command creates a fresh offline guest with no inference socket, host
credentials or host mounts. A root supervisor launches the observation process
as UID/GID 65534 with cleared groups, no privilege gain and bounded resources,
logs and time. Source belongs to root and is read-only to the worker; a separate
temporary directory is writable. Checker files are under a root-only directory.
The supervisor passes the pinned worker program and request data to its child.
Neither the task contract's expected values nor prior results enter the guest.
The worker's Python process shares candidate imports, so its observations remain
untrusted. Candidate code can inspect its process; this is not a claim that
arbitrary hostile code cannot infer or game finite probes.

The worker reads `{"requests": [...]}` on stdin and receives the source root as
its first argument. It returns exactly `{"observations": [...]}` in request order.
It must not emit grading verdicts. The host rejects missing, extra, duplicate-key
or malformed output and compares each observation with the frozen expected JSON.
Boolean and numeric values remain distinct. The worker's text is enclosed in a
supervisor result; it cannot replace host-computed comparison fields.

A completed check reports per-probe observations and `accepted_by_checks`. A
completed rejection exits 1; acceptance exits 0. Output must stay outside the
candidate, original input and runtime distribution inventories. A
process/protocol/runtime failure leaves acceptance unknown, not a fabricated
clean pass or reliable negative control. Cleanup failure also withholds acceptance.
The report binds the task, candidate, worker, runtime/source and image identities.
Recovery and automatic staging retention use the existing
[lifecycle contract](ENVIRONMENT.md). No native model calls occur in these executable checks;
its costs are separate from task-agent accounting.

The shared [task package workflow](TASKS.md) adds baseline/reference environment
validation around this artifact contract. `artifact` is a compatible CLI alias;
document tasks reuse the same byte collection and offline check boundary with a
separate semantic rubric.

## Assess the repair independently

`schema repair-assessment-spec` describes an explicit selection of a frozen repair
result and one or more hash-pinned check reports. Checks must describe the same
candidate, original input and task prompt. Preparation revalidates their worker,
source, recovery evidence and host comparisons. Incomplete checks retain their
unknown state and diagnostics.

```sh
uv run skilleval repair prepare-assessment assessment-spec.json --output PACKET
# Capture PACKET and run the returned prompt in native judge mode.
uv run skilleval repair record-assessment assessment-spec.json \
  --packet PACKET --run JUDGE_RUN --output ASSESSMENTS
```

The prepared packet contains original and candidate source, the generated patch,
the shared task instruction, the solver's retained final statement, bounded command
observations and separately collected worker observations. It withholds structured
arm/skill identity, host provenance, consumption, expected answers and check pass
labels. The same filter withholds harness/skill-access commands in both arms;
individual commands/outputs and the list have explicit truncation indicators.
Original raw traces stay retained outside the packet. Free-form prose or source
can still reveal treatment cues: this is metadata masking, not proven independent
blinding. The judge can question check fixtures rather than inherit their grades.

The rubric separates `task-contract`, `scope-and-regressions` and
`verification-claims`. Each permits `satisfied`, `violated` or `unresolved`, with
missing criteria kept missing. Regression attribution must distinguish baseline
or environment failures from candidate changes. Solver command observations may
precede later edits; only separately collected checks bind the final candidate.
A claimed test pass without its supporting evidence remains unresolved.

Investigative mode permits bounded reproductions against copies inside the judge
VM. Static mode prohibits investigation by policy. Both bind the exact packet,
prompt, mode and model. Size the native artifact budget for both supplied source
and investigation copies, including export overhead: large before/after packets
can exceed the default 64 MiB. Set `native qualify --max-artifact-bytes` and
`--max-artifact-files` explicitly when needed. The current maximum is 256 MiB
and 10,000 files.
A collection-limit failure retains diagnostics, not a usable judgment; any retry
uses a fresh directory and keeps the failed attempt.

Resolved judgments need substantive original source,
check or tool citations; final statements cannot substantiate themselves.
Investigation artifacts under `task/investigation/repair/` supplement original
citations, whose bytes and line spans are verified. This validates evidence
location and identity, not whether the model's reasoning is correct.

Recording retains the native result and artifacts, copied packet, selected source
specification, judgment and summary in an append-only result-ID directory. A
separately retained record digest pins every retained file. Read-back regenerates
the packet and validates the native result and bindings. Preserve the selected
source, check and original native-input captures as external dependencies. A
revision has a fresh ID and explicit `supersedes`; it must assess the same repair
evidence and never replaces the original. Failed judge execution stays unusable,
even if its partial response is parseable. Independent audit remains pending.

The [authored assessment controls](../../examples/repair/assessment-v1/README.md)
exercise valid, defective and unsupported-claim judgments. Labels stay outside
judge packets. These tiny examples test the grader path, not frontier-agent
ability or representative task difficulty.

## Select outcomes for a study report

`schema repair-outcomes` freezes a study plan digest and explicit cell/attempt
selections. Each selection supplies check references and optionally one assessment
record path and digest. Reporting validates the original native attempt receipt,
candidate and exact check selection. It never chooses the latest or most favorable
judgment automatically.

```sh
uv run skilleval study report PLAN --sha256 PLAN_DIGEST --output STUDY_RUN \
  --outcomes SELECTION --outcomes-sha256 SELECTION_DIGEST
```

The report preserves all attempts, original native outcomes and task-agent usage.
Missing selections remain null and checked-but-unjudged repairs remain unassessed.
Primary repair outcomes follow the existing first-attempt eligibility policy;
a later successful repair cannot replace an earlier failure. The selected judge record's consumption appears separately; this is not a complete
ledger of failed or unselected judge invocations. Functional results and semantic verdicts are separate fields,
with no invented scalar quality score or uncertainty estimate.

Polarity controls must show that checks accept intended repairs and reject
plausible wrong results. Neither finite checks, model agreement nor structurally
valid citations establish exhaustive correctness, skill benefit or an audited
grader. Human calibration, representative task selection and full-workload
admission remain prerequisites for scored studies. A bounded environment seal and
a passing repair check do not grant that admission.
