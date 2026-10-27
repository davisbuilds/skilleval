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
