# Environment admission and recovery

Environment admission binds a set of observed controls to exact local inputs.
It covers the bounded native control route, not a scored study, a calibrated
grader, skill consultation, or longer review workloads. Each underlying probe
continues to report `admitted: false`; only the separate admission command can
assemble and check the required evidence.

The [native development runner](NATIVE.md) shares this lifecycle and records its
own complete workload evidence. `native qualify` never consumes a bounded seal
as permission for scored work, and successful reviews or judges do not expand
that seal's scope. Source changes invalidate prior receipts; preserve those
generations and qualify the changed route with fresh outputs.

## Identity and invalidation

Admission requires new offline task, authenticated native, and adversarial
lifecycle receipts. Each captures the same host, complete unpacked smolvm
distribution, and relevant control source identities before execution and checks
them again afterward. The distribution includes the launcher, binaries,
libraries, guest agent root filesystem, and disk templates. Symlinks in the
guest root filesystem are inventoried as link text and never followed into host
files. Sparse templates use a fingerprint of their logical size, allocated
extent offsets, and allocated bytes; even allocation-layout changes invalidate
the evidence conservatively.

The task evidence, image archive, reconstructed Git workspace, commands, catalog,
observed harness version, model/effort, gateway, rendered guest configuration,
and retained evidence artifacts must agree. The exact observed Codex version is
bound even when a newer stable minor or patch version is eligible for fresh
qualification. Old receipts missing these captures remain historical evidence;
they cannot be upgraded by assigning current hashes afterward.

This is a trusted local operator and filesystem evidence boundary. Hashes detect
changes relative to a retained digest; they are not signatures or proof against
an operator who can replace both evidence and its expected digest. These opt-in
controls currently require the complete source checkout and unpacked runtime.
They do not claim installed-wheel or cross-host runtime qualification.

## Task capture and baseline fidelity

The [task-package contract](TASKS.md) makes these requirements explicit for repair
and document tasks. Its validation command runs declared checks in fresh offline
VMs and records expected exceptions, incomplete observations and coverage gaps.
Packaged development dispatch rechecks the selected validation and execution
identity. This is task-specific evidence, separate from scored runtime admission.

Qualify the task's ordinary commands separately from runtime isolation and checker
polarity. A source tree that imports successfully or passes one focused test can
still omit ordinary assets, historical objects or tool-dependent coverage. The
[receipt-admission example](../../examples/repair/receipt-admission/environment.md)
records a concrete baseline/reference comparison and its remaining limits.

Freeze source at an explicit revision and retain bytes, executable modes, required
assets and usable Git context. Inspect dependencies reached through tests **and
fixtures**: a manifest can name a historical verifier commit that the test source
does not mention. A shallow checkout's reachable history may omit those objects.
Record any excluded working-tree paths and the exact validation lanes that remain
unsupported; an entire directory called `data` or `experiments` is not inherently
unrelated to the task.

Construct fresh Git metadata from a constrained object inventory. Preserve the
required commit/tree closures and shallow boundaries without copying unrelated
refs, reflogs, configuration, alternates or the full source repository object
store. Establish known-positive object reads before asserting that a later repair
commit or its distinctive blobs are absent. Keep reference repairs outside the
solver capture, in a separate control generation.

Run untouched and reference source under the same pinned image, shell invocation,
working directory and declared environment. Record the effective UID/GID, username,
Python/platform and tool resolution. A launcher environment is not necessarily a
tool-shell environment: a login shell can change PATH. If an identity comparison
is needed, first establish that both identities can access the same source and
actually execute the target tests; an import error is not a passing control.

Retain complete, bounded stdout/stderr, command exits, deadlines, log hashes, test
identities, failures and skips. Verify source before and after execution and compare
the common test population separately from reference-added tests. A command that
exits zero with every task skipped validates no tasks. A diagnostic controller's
successful collection is not a successful test suite. Retain shared baseline
failures rather than patching historical tests or silently excluding them.

Capture corrections create new inputs, contracts and evidence generations. Old
agent attempts keep their original input identities; improved environment evidence
does not retroactively qualify them. A full pipeline also needs artifact-budget
checks: before/after source packets plus investigation copies can exceed native
collection limits even when a solver's single working tree fits.

## Run and check

Use a pinned local Docker-save image containing the task and harness dependencies.
The current native control profile uses two vCPUs and 2 GiB of guest memory.
The prepared task plan must include Git reconstruction and explicit task checks.
Run offline qualification with `--capture-identity`, then the authenticated
[native control](GATEWAY.md#run-the-explicit-control), retaining new output paths
and every failed attempt. The separate credential-free lifecycle probe exercises
execution timeout, detached guest work, fresh workspace/session state, and
controller interruption:

```sh
uv run skilleval qualification run task-plan.json --capture-identity \
  --runtime /path/to/smolvm --output .local/task-qualification
uv run python scripts/qualify-lifecycle.py \
  --runtime /path/to/smolvm --image /path/to/task-image.tar \
  --image-sha256 ARCHIVE_SHA256 --output .local/lifecycle-qualification
uv run skilleval admission seal --plan task-plan.json \
  --offline .local/task-qualification/report.json \
  --native .local/native-authenticated/report.json \
  --lifecycle .local/lifecycle-qualification/report.json \
  --catalog /path/to/models.json --runtime /path/to/smolvm \
  --output .local/environment-admission.json
uv run skilleval admission check .local/environment-admission.json \
  --sha256 RETAINED_ADMISSION_SHA256
```

Sealing validates the evidence before writing a new snapshot. Checking requires
the separately retained snapshot digest and rechecks its receipts, artifacts,
current source, runtime, task, image, and configuration. Missing, partial,
inconsistent, or changed evidence refuses admission. CLI help owns the exact
arguments. No command launches scored work, and ordinary checks launch no VMs.

## Recovery owns the whole VM

An execution timeout is not evidence that detached guest processes have stopped.
Whole-machine teardown owns their termination. The lifecycle control observes a
guest heartbeat over a narrowly scoped receive-only socket, checks a known
positive before termination, then pairs heartbeat silence with same-state
machine inventory absence. Fresh-machine checks first establish real workspace
and session markers, then check that another VM created from the same image
cannot see them.

Identity-capturing offline and native controls share the lifecycle recovery
implementation with development reviews and judges. It writes and fsyncs a
private `recovery.json` before create can
have effects. The ticket preserves the machine name and isolated HOME/XDG paths.
Cleanup requires a working inventory that observed the target, successful
stop/delete, and observed absence before removing the runtime HOME. Failures
retain the state and ticket. Controller termination does not imply guest cleanup;
an explicit recovery command uses the retained namespace:

```sh
uv run skilleval qualification recover .local/qualification-output
```

Recovery is idempotent after a completed cleanup. It refuses drifted identities
or malformed tickets; retain the original runtime distribution and control-source
revision for recovery, and inspect state instead of deleting directories by
guesswork. Run recovery only after the original controller has stopped. There is
no concurrent controller lock or watchdog service. SIGKILL and host failure
cannot run Python cleanup; the durable ticket supports later recovery, not an
automatic guarantee after host loss.

Do not query smolvm through a retired runtime HOME after cleanup. Even a late
`machine exec` query that returns "vm not found" can initialize an empty database
and cache directory again. Inspect the retained ticket and filesystem first;
progress observers must stop with their controller. A recreated empty namespace
does not mean the deleted VM survived, and still needs scoped cleanup.

## Image retention

Keep each reusable source image outside run output directories, preferably in a
dedicated image directory. Preparation verifies and copies it to the run's
`runtime-state/image.tar`; the runtime consumes that independent staging copy.
After whole-machine cleanup and runtime HOME removal, the controller removes the
staging copy only when both it and the retained source still match the recorded
image identity. Active runs, failed cleanup, identity drift, changed copies, and
missing or changed source images retain staging for inspection or recovery.
This policy applies to offline qualification, native controls, reviews, and judges.
Copies exist while runs are active; there is no shared mutable image file or new
global cache. Distinct source image versions remain operator-owned.

To inspect older runs or retry retained copies, name each run directory explicitly:

```sh
uv run skilleval storage prune .local/run-one .local/run-two
uv run skilleval storage prune .local/run-one .local/run-two --apply
```

The default is read-only preview; `--apply` removes only verified staging archives.
JSON output includes per-run eligibility/refusal, image identity, retained source,
and logical/allocated byte counts. Exit 1 means at least one target was refused or
an operation failed; other targets may have completed. Repeated application is
safe. Applied actions and refusals have separate `image-retention.json` receipts;
original reports, recovery tickets, logs, task artifacts and assessments are not
rewritten. Run this after the original controllers have stopped. It does not run
smolvm, recover VMs, or infer cleanup from a directory's age.

Historical recovery tickets can be inspected without the current control-source
identity matching: pruning reads their terminal cleanup evidence and checks the
retained image bytes, but neither recovers nor upgrades the old runtime evidence.
Legacy qualification reports without tickets require explicit successful
stop/delete records and an absent runtime HOME. A malformed ticket never falls
back to a legacy report. Admission checks need the original image and recorded
artifacts, not a completed run's disposable image copy. Changes to retention code
still invalidate prior admission through the control-source fingerprint.

Measure storage with allocated bytes as well as apparent file lengths. Sparse
VM templates can have large logical capacities while occupying little disk;
removing staging copies does not modify those runtime-bound templates.

Other local storage needs a separate, scoped review. Duplicate downloads and
scratch checkouts can be removed after verifying retained copies. Completed-run
`checker-input.tar`, `validation-input.tar`, and native-run `tooling.tar` copies
can be disposable, but only after checking the terminal recovery ticket, runtime
HOME absence, and all evidence readers. Qualification `tooling.tar` archives are
still rehashed by admission and must remain. `storage prune` does not remove these
other archive types. Preserve source images, canonical inputs, artifacts, traces,
judgments, failed-run evidence, and pending audits. Record the removed paths and
verify retained records still load; do not treat an old directory name or a large
apparent size as a deletion criterion.
