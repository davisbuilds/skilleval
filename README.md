# skilleval

Local-first experiments measuring how a skill changes an agent's quality,
outcomes, token consumption, cost and time on relevant real tasks. The selected
direction is explicit-use skill ON/OFF comparisons for independently supplied
skills from any source. Preserve what happened and grade outcomes using
executable checks, semantic assessment, and human review.

The foundation supports **evidence capture, native reviews and repairs, pinned
skill inputs, and static or investigative judging**. Native Codex runs
in fresh smolvm guests through a host-only inference gateway. Live controls cover repository tests, Git
fidelity, filesystem/network boundaries, lifecycle recovery and cleanup;
environment admission binds that evidence to exact local identities.

The [judge reliability workflow](docs/system/RELIABILITY.md) prepares separately
versioned repeat/order/perturbation controls and compares retained native results.
The [study controller](docs/system/STUDIES.md) freezes development pairs, budgets
and retry policy, records every attempt, and reports first-attempt comparisons.
New plans default to explicit-use ON/OFF; earlier plans retain their frozen treatment.
[Accounting](docs/system/ACCOUNTING.md) separates normalized native usage,
conditional API-equivalent estimates, agent time and infrastructure stages. Paired
efficiency differences are descriptive; selected repair judgments remain unaudited
and uncertainty is not estimated. A development pair on 2026-10-28 verified explicit invocation,
broker-matched core token totals and separate agent timing on Codex 0.164.0.
Independent human audit and scored-study admission remain pending.

The [repair workflow](docs/system/REPAIRS.md) collects actual source changes and
checks them in a fresh offline VM, with expected answers and comparison authority
on the host. The receipt-admission development seed rejects the untouched defect
and blanket accept/reject controls while accepting the historical repair. These
finite checks remain separate from semantic judgments. Repair packets withhold
structured treatment metadata and check grades; native judges assess behavior,
regressions and verification claims with cited evidence. Explicit outcome selection
links them to attempts while preserving task-agent accounting and separate judge
consumption. Representativeness and independent grader audit remain open.

Earlier development pairs and native calibration sessions completed. Fresh judges
also traced contracts and ran reproductions for the real reports, retaining
original citations and investigation artifacts. These are development workflows:
independent human grader audit and scored dispatch remain pending, and no scored
campaign has run. The [roadmap](docs/project/ROADMAP.md) records the evidence limits.

Python owns the core and CLI. A TypeScript review UI and a pi adapter are later
work. See the [roadmap](docs/project/ROADMAP.md),
[vision](docs/project/VISION.md), and [architecture](docs/system/ARCHITECTURE.md).
The [pilot shortlist](docs/project/PILOT-CANDIDATES.md) grounds the next task
adapters in repair, browser implementation and writing; these are candidates,
not captured evaluation cases or admitted trials.

## Start locally

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/) 0.9.17+. Evidence capture uses
macOS or Linux filesystem primitives. Windows is not currently supported.

```sh
uv sync --locked
uv run skilleval --help
scripts/check
```

Checks use authored fixtures and real local filesystem/process operations. They
do not install a runtime, launch a VM, read credentials, or call a model.

## Try the offline workflow

The checked-in review example is intentionally tiny. It is not representative
task evidence. Run from this repository; output paths must be new.

```sh
mkdir -p .local/evidence .local/reviews .local/assessments
uv run skilleval import-review examples/review/legacy --case demo \
  --output .local/evidence/demo > .local/demo-receipt.json
uv run skilleval inspect .local/evidence/demo
uv run skilleval review prepare .local/evidence/demo --output .local/reviews/demo
```

The import preserves original reports and case files. The review packet includes
source, instructions, selected reports, and an assessment rubric; the oracle is
excluded unless `--include-reference` is requested. Only `case/source` is eligible
for transfer into a solver environment. Neither packet preparation nor anonymous
report IDs establish independent blinding.

Create a demonstration assessment using the receipt's evidence identity:

```sh
uv run python - <<'PY'
import json
from pathlib import Path

receipt = json.loads(Path('.local/demo-receipt.json').read_text())
record = json.loads(Path('examples/review/assessment.template.json').read_text())
record['evidence_sha256'] = receipt['manifest_sha256']
Path('.local/demo-assessment.json').write_text(json.dumps(record, indent=2) + '\n')
PY
uv run skilleval review record .local/evidence/demo .local/demo-assessment.json \
  --output .local/assessments
```

This records an authored example judgment; it does not run a judge or validate the
judgment's truth. Record revisions under a new ID with `supersedes`. Existing
records are never overwritten by the CLI.

Retain each receipt separately from its bundle. Use `inspect BUNDLE --sha256
DIGEST` for a compact integrity check, or `verify` for the full manifest. Without
an external expected digest, verification checks internal consistency only.

## Import your evidence

`capture SOURCE --output DESTINATION` copies an explicit tree. It rejects links,
special files, changes detected during each file read, and existing destinations.
The destination's parent must already exist. Limits are 10,000 files, 32 MiB per
file, and 256 MiB total. Freeze the upstream source first: capture is not an atomic
snapshot of a live repository or proof of its earlier chain of custody.

`import-review SOURCE --case ID --output DESTINATION` accepts this historical
review export format:

```text
SOURCE/
  reports.json                  # array, selected by case_id
  cases/ID/
    instruction.md
    oracle.md
    source/                     # regular task files
```

Selected reports need unique `report_id` values for review preparation. Assessment
recording accepts `valid-report` with `run_completed: true`, or
`valid-report-in-failed-run`, and requires a `findings` array. Missing or invalid
reports cannot be recorded as clean reviews. Failed execution remains distinct
from usable report content.

All historical imports are exposed development data. The complete original
`reports.json` is retained under `provenance/`, including other cases in that
document. Treat the whole bundle as private. Keep private data in ignored `.local/`
or an explicit external directory; no sibling checkout is needed at runtime.

## Calibrate a semantic judge

The separate public [calibration set](docs/system/CALIBRATION.md) contains twelve
authored positive, negative, duplicate, ambiguous, invalid-report, and defect
coverage probes. Capture it and export a packet that withholds labels and
structured history:

```sh
mkdir -p .local/calibration
uv run skilleval capture examples/calibration/review-v1 \
  --output .local/calibration/bundle > .local/calibration/evidence-receipt.json
uv run skilleval calibration prepare .local/calibration/bundle \
  --output .local/calibration/packet > .local/calibration/packet-receipt.json
uv run skilleval schema calibration-result
uv run skilleval calibration compare .local/calibration/bundle
```

The last command reports all opportunities as unassessed. It does not invent
judgments or give missing results a zero score. An explicitly invoked judge gets
only the packet and its receipt, including the exact packet digest. Once it has
produced a result conforming to `schema calibration-result`:

```sh
uv run skilleval calibration record .local/calibration/bundle judge-result.json \
  --output .local/calibration/results
uv run skilleval calibration compare .local/calibration/bundle \
  .local/calibration/results/JUDGE_RESULT_ID.json
```

Recording validates structure, identity, and citations. Comparison reports each
run's confusion matrix and explicit missing/invalid/unresolved coverage against
authored labels. It does not call a model, prove the labels independently correct,
measure unaided defect discovery, or establish a benefit from skills.

## Run a native development workload

The [native workflow](docs/system/NATIVE.md) prepares a source-only review or a
label-free judge packet, freezes it with `capture`, explicitly runs native Codex
in a fresh VM through `native qualify`, then records the retained judge result.
The runner preserves process status, native events, usage observations, exact
prompt/model identity, original-input checks, guest artifacts, and cleanup.
It never grants scored admission.

Agentic judges can inspect source and choose bounded VM reproductions. The static
variant changes the declared prompt policy; native tools remain available.
Original host evidence stays authoritative, and guest-produced artifacts remain
untrusted evidence. Label withholding does not establish independent blinding.
Recorded audit packets remain pending independent human review.

`treatment capture` freezes an explicitly selected skill tree, source revision,
and optional supplied root notice. A review run can make that skill available in
its fresh guest Codex home. Availability, metadata exposure, body consultation,
and semantic application are separate observations; missing telemetry is unknown.
See the [treatment procedure](docs/system/NATIVE.md#skill-availability-and-consultation)
before comparing runs.

## Prepare a native review workspace

For a historical review capture containing `.openbench-review.json` and a
`review.patch` with full blob identities, reconstruct a writable source tree:

```sh
uv run skilleval workspace prepare CAPTURED_REVIEW_BUNDLE \
  --sha256 RETAINED_MANIFEST_SHA256 --output fresh-review-workspace
```

This exports source only, preserves file bytes/modes, and reconstructs synthetic
`base` and `candidate` commits whose diff represents the captured change. It
retains ordinary repository instructions/tests while excluding grading inputs.
It does not recover original commit identities or arbitrary Git history. Native
Git plumbing processes the patch as data; imported programs, hooks, and filters
are not executed. Unsupported patch/path forms fail closed. The tiny review
example above has no reconstruction metadata and cannot use this command.

## Runtime qualification

`qualification plan` binds an imported case, pinned image identity, resources,
and explicit command argv arrays. Planning does not launch a VM or execute task
code. Supply a prepared image that already contains the task's tools and
dependencies, and a commands file such as `[["python3", "-m", "pytest"]]`.

```sh
uv run skilleval qualification plan BUNDLE \
  --image 'docker.io/YOUR_REPO@sha256:YOUR_ACTUAL_MANIFEST_DIGEST' \
  --commands controls.json --output plan.json
uv run skilleval qualification run plan.json --output qualification-result
```

The second command explicitly creates a VM and may acquire an image from a public
registry. The candidate adapter requires smolvm **1.32.3**, restricts registry
execution to explicit `docker.io/` image references, supplies isolated runtime
directories, and requests no guest network, mounts, or secrets. Other digest-pinned
registry references can be planned but cannot run yet. The restriction avoids the
pinned CLI's automatic packed-image policy import; it is not evidence of effective
isolation. See [runtime boundaries](docs/system/ARCHITECTURE.md#runtime-and-harness).

Only task source enters the guest. Commands run there, with bounded captured logs
and stop/delete cleanup. A failure retains diagnostic state and the machine name.
Use `--capture-identity` for complete runtime fingerprints and a durable recovery
ticket checked through same-state inventory. A short, isolated runtime HOME avoids
macOS Unix-socket pathname limits. `offline_passed` means the explicit controls and cleanup succeeded; every
result still has `admitted: false`. A prepared image has passed a captured real
task's repository tests, guest Git history and exact-diff checks, and cleanup.
The bounded native provider/tool loop also passed. The
[Python image recipe](environments/python-review/README.md) keeps task source and
credentials outside the build context.

Completed runs release their image staging copy after verifying the retained
source archive. Original evidence stays in place. Use `skilleval storage prune
RUN_DIRECTORY` to preview cleanup of older runs, and add `--apply` to perform it;
see [image retention](docs/system/ENVIRONMENT.md#image-retention) for refusal and
recovery behavior.

A local Docker-save archive is a separate supported image route. Its full
SHA-256 is required; planning inspects bounded archive structure without
extracting or executing layers. `run` copies and rechecks the pinned archive
before supplying it to smolvm. For supported review metadata, `--reconstruct-git`
prepares source plus synthetic history and verifies the expected commits, trees,
clean worktree, and Git objects inside the guest:

```sh
uv run skilleval qualification plan BUNDLE \
  --image-archive prepared-image.tar --image-sha256 ARCHIVE_SHA256 \
  --reconstruct-git --commands controls.json --output local-plan.json
uv run skilleval qualification run local-plan.json --capture-identity \
  --output local-qualification
```

These options are implemented; a prepared image and its native task/model route
still require live qualification. A structurally accepted archive is not an
attestation of safe image contents or effective runtime isolation.

The [native gateway](docs/system/GATEWAY.md) is an explicit Python interface for a
bounded host broker and credential-free guest relay. It selects an existing
ChatGPT login on the host; the guest receives only a mounted inference socket.
Its pinned model/profile, request limits, and cleanup have deterministic controls.
An explicit native control has exercised the authenticated tool loop, filesystem
and network denials, and observed cleanup. The opt-in script and credential
boundary are documented in the gateway reference; ordinary checks never start it
or read credentials.

The separate [environment workflow](docs/system/ENVIRONMENT.md) adds timeout,
detached-process, fresh-state, and interrupted-controller controls. It seals and
rechecks their evidence against the exact runtime, task, image, harness, and
configuration. A separately retained snapshot digest is required for checking.
This admits only the bounded environment control route; it does not authorize
scored dispatch or establish grader reliability. Explicit `qualification recover`
uses a retained ticket after a controller has stopped.

## CLI contract

Data commands emit JSON to stdout; diagnostics use stderr. Exit 0 means the
requested operation succeeded, 1 means qualification was partial/failed, 2 means
invalid input or an operational error, and 130 means interruption. Help/version
are plain text. `schema KIND` exposes provisional version-1 records; `validate
KIND FILE` checks structure, never experimental validity or truth.

The development study controller supports declared retries and explicit resume;
first attempts remain primary. There is no automatic assessment selection, remote
submission or scored campaign execution. Runtime execution and recovery are
explicit opt-in operations.

## Development

Use `scripts/check` for Ruff, formatting, strict mypy, pytest, and whitespace
checks. Dependency resolution observes a seven-day release quarantine; `uv.lock`
pins the development environment. There is no remote CI or publishing workflow
yet. The [backlog](docs/project/BACKLOG.md) tracks the concrete remaining work.

Bug reports, focused fixes, documentation improvements, and supported proposals
are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for scope and review expectations.
Public GitHub hosting is intended but has not been set up. License selection and
release readiness remain explicit pre-publication work; no release is claimed.

## Acknowledgements and research

The design investigation drew on Tessl's repository-derived scenarios,
SkillsBench/BenchFlow's task packaging, adewale's skill-eval-harness measurement
model, Anthropic's skill-creator review workflow, and gstack's outcome controls.
SWE-Skills-Bench and WebDev-Skills-Bench informed requirement traceability and
experimental controls. Dojo, OpenBench, and Engram supplied additional packaging,
execution, and semantic-versus-structural design precedents.

The [source register](docs/project/SOURCES.md) links all of these, plus the runtime
and harness references, and records what we may adopt and what remains unverified.
These are influences and candidates, not endorsements or a list of dependencies.
The bounded gateway is adapted from a pinned public OpenBench revision,
with its MIT notice retained in
[`src/skilleval/_vendor/OPENBENCH_LICENSE`](src/skilleval/_vendor/OPENBENCH_LICENSE).
The [gateway reference](docs/system/GATEWAY.md#provenance-and-maintenance) records
the original source/hash, local compatibility patch, tests, and wrapper boundary. Other listed
projects remain design influences or candidates unless the source register says
otherwise.
