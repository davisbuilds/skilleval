# Repair artifacts, checks and semantic assessments

Repair mode asks the agent to change a frozen source tree. It uses the same native
Codex/VM route as reviews, with a separate task-mode identity. It does not require
a review JSON response or trust a claimed solution, score or guest Git diff.
The [study controller](STUDIES.md) freezes explicit-use ON/OFF prompts and reports
task-agent consumption. Repair checks are a separate, credential-free operation.

## Freeze the task and checks

`schema repair-spec` owns the input contract. A task binds its source snapshot and
digest, shared prompt, editable paths, a Python observation worker and its
digest, named requests/expected observations, deadline and source provenance.
`repair prepare SPEC --output TASK` verifies those inputs, resolves paths and
publishes a task identity without launching anything. Retain its digest separately.
The initial patch adapter accepts portable ASCII paths containing letters, digits
and `_./@+-`; spaces and other encodings are unsupported. Preparation checks all
retained source paths, including unchanged context, before a native attempt.

Optional `editable_directories` permits additions, edits and deletions below
declared directories without prescribing helper or test filenames. Both collection
and readback enforce the same path-ancestor rule: `src` includes `src/new.ts`,
not `src-other/new.ts` or replacement of `src` itself with a file. Directory grants
exclude Git/cache metadata and capture manifests. Omitting the field preserves
legacy contract bytes. Exact `editable_paths` remain supported.

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

`observer_resource_profile` selects a fixed offline observation budget. Omit it
for the legacy `python-v1` profile: 512 MiB address space, 128 KiB maximum file
size, 64 open files, 32 processes and no core dumps. The explicit `node-v1`
profile allows 2 GiB address space, 8 MiB maximum file size and 256 open files;
the process and core limits stay the same. These are child resource limits,
including per-process address space and per-file size, not a total disk budget.
Both profiles retain at most 128 KiB of stdout and 128 KiB of stderr. The
supervisor reads each stream file with a bounded 128 KiB + 1 byte read; reaching
the output cap makes execution incomplete and acceptance unknown. The larger
file allowance supports temporary databases without expanding retained output.
Nondefault settings and their exact limits are bound into the task, supervisor
envelope and check report, and validated again when a report is selected for
assessment. Omitted defaults preserve legacy task bytes and report compatibility.

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
labels. The selected filter applies to both arms;
individual commands/outputs and the list have explicit truncation indicators.
Original raw traces stay retained outside the packet. Free-form prose or source
can still reveal treatment cues: this is metadata masking, not proven independent
blinding. The judge can question check fixtures rather than inherit their grades.

Select `"tool_evidence_policy": "execution-context-v3"` for new repair/document
packets. It retains completed-command status and exit codes when output is null,
missing or invalid, distinguishing those states from captured empty output. Normal
application `CODEX_HOME` source/diff output is retained. Recognized harness/skill
output lines are explicitly redacted; commands containing recognized harness paths,
skill markers or direct home lookups remain withheld. Scan and masking precede
display truncation. This is bounded text matching, not program analysis or proven
blinding; indirect access and free-form text can still expose treatment cues.

V3 adds bounded chronology in original event order: command starts/completions,
native IDs/scope/cwd when supplied, and file-change paths including rename
destinations. Masked or oversized destinations carry explicit omission flags.
File diffs are omitted, and gaps/truncation are visible. A start does not prove
completion. Event order is not wall time, arbitrary shell writes may be invisible,
and the absence of an observed edit does not prove the source stayed unchanged.
Only separately collected checks bind the final candidate; chronology helps
investigate overlapping work without claiming that a test exercised final bytes.

`application-context-v2` remains available for exact historical reconstruction.
It retains simple application-home assignments, but can omit whole commands when
output contains a source-level home lookup, and drops completions with null output.
The two older policies keep their original packet instructions and bytes.

An omitted policy, or explicit `legacy-v1`, preserves the original keyword filter
and exact packet, rubric, schema and source-spec serialization. It can hide
legitimate application commands containing `CODEX_HOME`; this affected a qualified
test rerun in the completed-session task. The default stays legacy for readback
compatibility, so opt into v3 explicitly. Unknown policies are rejected. The selected
policy is bound in the source specification, packet metadata and tool evidence;
repair and document preparation, recording and readback use the same selection.

To correct an earlier assessment, prepare a fresh v3 packet from the same retained
repair and checks, run a new judgment, and select its record explicitly in a new
outcome report. Keep the old packet and judgment unchanged. Changing evidence
policy changes evidence identity, so this is a new evidence generation, not a
same-evidence `supersedes` revision. No new task-agent run is needed. Review the
retained raw trace when diagnosing a verification discrepancy.

The [supplementary lifecycle controls](../../examples/repair/completed-sessions/lifecycle-v2/README.md)
prepare a separate task identity for ordinary archive/plain cache handover and
reader-limit behavior. A snapshot replay uses an explicitly labeled control
artifact. To select supplementary checks in a native study report, use
`repair collect` against the new task and the original retained native run, then check
that fresh result. Collection enforces the original input and task prompt and
retains the original native receipt; it does not create another solver attempt.
Never relabel an existing control result or rewrite its check report to acquire
native provenance. Neither route changes the original six probes or establishes
broader correctness from two additional observations.

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

## Correct citation line ranges

Repair and document assessments support one explicitly linked correction when a
completed judge returns a structurally valid result with citations extending past
an existing UTF-8 file. Preparation verifies the original invocation, packet,
result identities and every non-range constraint before creating a fresh static
packet. Missing files, changed evidence, invalid schemas, failed execution and
already valid results are ineligible. Calibration and review judgments do not yet
use this path.

```sh
uv run skilleval repair prepare-citation-correction SPEC \
  --packet ORIGINAL_PACKET --run ORIGINAL_RUN \
  --timeout-seconds 180 --max-requests 4 --output CORRECTION_PACKET
```

Preparation makes no model call. Its JSON response supplies the exact `prompt`,
`input_digest` and declared `budget`. Save the prompt to a file, capture the
correction packet with `skilleval capture`, and check the capture digest against
`input_digest`. Run `native qualify` in `--mode judge --judge-policy static`, with
the saved prompt, captured packet and explicit `--timeout-seconds 180
--max-requests 4` matching this example. Use fresh output directories and the
usual pinned runtime, image, catalog and explicitly selected host login.

The packet contains the original raw result, exact cited file bytes and structural
diagnostics naming JSON pointers and observed line counts. It supplies no suggested
replacement range or correctness label. The correction may change only citation
line starts and ends; paths, list membership/order, result ID, reasoning, verdicts,
and original evidence/rubric identities remain unchanged. The correction invocation
has its own packet identity and static policy, while the assessment retains its
original investigation policy and artifact authority. New correction artifacts
cannot substitute for original investigation evidence.

```sh
uv run skilleval repair record-assessment SPEC \
  --packet ORIGINAL_PACKET --run ORIGINAL_RUN \
  --correction-packet CORRECTION_PACKET --correction-run CORRECTION_RUN \
  --output RECORDS
```

Both correction flags are required together. Recording validates the exact new
input/prompt/model, configured time/request ceilings, admitted request counts,
unchanged non-bound content and every corrected citation. An invalid or incomplete
correction remains retained in its run directory and produces no accepted record.
There is no automatic retry, range clamping or semantic reassessment.

Accepted records preserve the original root `final.txt`, native receipt and
artifacts, add `correction/packet/` and `correction/run/`, and bind them through
`citation_correction` metadata. `result.json` contains the corrected judgment;
read-back revalidates both invocations and the permitted difference. Legacy records
retain their original bytes and behavior. This is a correction of a previously
unrecordable result, not a `supersedes` revision of an accepted assessment.

The default correction budget is 180 seconds and four requests, with one linked
attempt. Preparing or running duplicates elsewhere is still possible; the record
does not claim to enumerate those external attempts. Selected judge consumption
includes both linked invocations as described in [ACCOUNTING.md](ACCOUNTING.md).
Structural acceptance still requires independent semantic audit.

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
