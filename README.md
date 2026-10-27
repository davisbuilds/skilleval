# skilleval

Local-first experiments measuring whether a skill improves an agent's work on
real tasks. Compare matched skill and vanilla runs, preserve what happened, and
grade outcomes using executable checks, semantic assessment, and human review.

The foundation currently supports **offline evidence and assessment workflows**.
It also contains a candidate smolvm adapter and a Codex event parser. It cannot
yet launch a scored campaign or call an LLM judge. No runtime has been admitted
for scored use.

Python owns the core and CLI. A TypeScript review UI and a pi adapter are later
work. See the [roadmap](docs/project/ROADMAP.md),
[vision](docs/project/VISION.md), and [architecture](docs/system/ARCHITECTURE.md).

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

The checked-in example is an intentionally tiny calibration fixture. It is not
representative task evidence. Run from this repository; output paths must be new.

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

## Runtime qualification

`qualification plan` binds an imported case, immutable OCI digest, resources, and
explicit command argv arrays. Planning does not execute anything. Supply a
prepared image that already contains the task's tools and dependencies, and a
commands file such as `[["python3", "-m", "pytest"]]`.

```sh
uv run skilleval qualification plan BUNDLE \
  --image 'docker.io/YOUR_REPO@sha256:YOUR_ACTUAL_MANIFEST_DIGEST' \
  --commands controls.json --output plan.json
uv run skilleval qualification run plan.json --output qualification-result
```

The second command explicitly creates a VM and may acquire an image from a public
registry. The candidate adapter requires smolvm **1.32.3**, permits execution only
for explicit `docker.io/` image references, supplies isolated runtime directories,
and requests no guest network, mounts, or secrets. Other digest-pinned registry
references can be planned but cannot run yet. The restriction avoids the pinned
CLI's automatic packed-image policy import; it is not evidence of effective
isolation. See [runtime boundaries](docs/system/ARCHITECTURE.md#runtime-and-harness).

Only task source enters the guest. Commands run there, with bounded captured logs
and best-effort stop/delete cleanup. A failure retains diagnostic state and the
machine name. `offline_passed` means these explicit controls exited successfully;
every result still has `admitted: false`. Native Git reconstruction, host/network
boundary controls, provider access, and the real Codex loop remain unqualified.

## CLI contract

Data commands emit JSON to stdout; diagnostics use stderr. Exit 0 means the
requested operation succeeded, 1 means qualification was partial/failed, 2 means
invalid input or an operational error, and 130 means interruption. Help/version
are plain text. `schema KIND` exposes provisional version-1 records; `validate
KIND FILE` checks structure, never experimental validity or truth.

There is no automatic retry, resume, assessment selection, remote submission, or
campaign execution. Runtime execution is opt-in through `qualification run`.

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
No implementation from those projects is vendored in this foundation. Preserve
upstream licenses and notices when a future change actually incorporates code.
