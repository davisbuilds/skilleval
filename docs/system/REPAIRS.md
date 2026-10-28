# Repair artifacts and offline checks

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
later source history must not reveal the repair.

The [receipt-admission example](../../examples/repair/receipt-admission/README.md)
provides a public brief, observation adapter and contract template. It remains an
exposed development seed. Its independent fixtures exercise real receipt logic
with controlled task inspection and Docker replay observations. It does not
establish production Docker grading behavior or task difficulty.

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
[lifecycle contract](ENVIRONMENT.md). No native model calls occur in grading;
its costs are separate from task-agent accounting.

## Evidence limits and next boundary

Polarity controls must show that the checker accepts intended repairs and rejects
plausible wrong results. Always-accept and always-reject controls distinguish a
working behavioral check from a blanket rule. An executable check cannot establish
its own coverage, independence or representative task difficulty.

`task_quality` and `semantic_assessment` remain unassessed. Repair-specific blinded
semantic assessment is still needed for contract preservation, unsupported claims,
regressions outside these checks and acceptable alternate implementations. Human
calibration and full-workload admission remain prerequisites for scored studies.
A bounded environment seal and a passing repair check do not grant that admission.
