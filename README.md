# skilleval

Measure what an agent skill changes: task quality, outcomes, tokens, cost, and
time compared with the same agent without the skill.

Skilleval is a local-first evaluation toolkit for people choosing, maintaining,
or studying agent skills. It starts with explicit skill ON/OFF comparisons on
captured tasks, preserves each attempt, and combines executable checks with
source-grounded LLM judgments and human review.

**Status: experimental.** Review, repair, and writing development workflows run
end to end. Independent grader calibration, a representative task population,
and scored-study admission remain open. The examples exercise the instrument;
they do not establish that a skill helps or that the grader is generally reliable.

## What it does

- **Freeze the comparison.** Capture task context and complete skill packages;
  pin the model, harness, environment, prompt difference, and budget policy.
- **Validate the task environment.** Run baseline/reference checks in fresh VMs;
  keep known failures, skipped coverage, and missing capabilities visible.
- **Retain what happened.** Preserve prompts, outputs, file changes, failures,
  retries, usage, and cleanup evidence in separate records.
- **Grade outcomes.** Combine task-specific executable observations with static
  or investigative semantic judgments, citations, and unresolved verdicts.
- **Compare quality and consumption.** Link delivered artifacts and judgments
  with task-agent usage, conditional cost estimates, and timing. Keep judge spend
  separate and missing telemetry unknown.

The current execution route uses native Codex in fresh smolvm guests and a bounded
host inference gateway. The selected skill is the treatment; ordinary task
context and capabilities stay comparable. Natural skill discovery is a separate,
later experiment. See the [vision](docs/project/VISION.md) for the comparison
principles and [roadmap](docs/project/ROADMAP.md) for priorities.

## Start locally

Use a source checkout with Python 3.12+ and [uv](https://docs.astral.sh/uv/) 0.9.17+:

```sh
uv sync --locked
uv run skilleval --help
scripts/check
```

The deterministic gate runs lint, format checks, type checks, tests, and whitespace
checks. It does not launch VMs, read credentials, or call models. Native execution
has been exercised on Apple Silicon macOS with Linux ARM64 guests; this is not a
claim of runtime portability. Windows is unsupported. See
[environment requirements](docs/system/ENVIRONMENT.md).

### Try an offline example

This small authored review fixture demonstrates evidence capture and packet
preparation. It requires no model account or VM. Run once with fresh output paths:

```sh
mkdir -p .local/quickstart
uv run skilleval import-review examples/review/legacy --case demo \
  --output .local/quickstart/evidence > .local/quickstart/receipt.json
uv run skilleval inspect .local/quickstart/evidence
uv run skilleval review prepare .local/quickstart/evidence \
  --output .local/quickstart/review
```

Inspect the generated review packet and receipt. The original historical report
is retained separately from the task source; preparation does not run or grade an
agent. Keep the receipt's digest separately from its bundle, and use a new output
directory for each preparation. See [evidence boundaries](docs/system/ARCHITECTURE.md#evidence)
for the integrity and privacy limits.

## Run an evaluation

Live evaluation currently requires explicit local setup: a pinned smolvm runtime,
a prepared dependency image, a compatible Codex binary and model catalog, and a
selected existing host login. These are not supplied by the offline example.
The native profile currently selects `gpt-6.1-sol` with high reasoning; observed
harness versions and exact execution identities are retained per run.

1. [Capture and validate a task package](docs/system/TASKS.md). Preserve the task's
   ordinary context and declare unsupported environment capabilities.
2. [Prepare the runtime and inference route](docs/system/ENVIRONMENT.md), using
   the [image recipe](environments/python-review/README.md) and
   [gateway contract](docs/system/GATEWAY.md).
3. [Freeze and run a development study](docs/system/STUDIES.md). New plans compare
   explicit skill use with the same task without that skill; every attempt is kept.
4. [Collect and check artifacts](docs/system/REPAIRS.md), then record a separate
   semantic judgment using the applicable review, repair, or document rubric.
5. [Inspect the comparison](docs/system/TASKS.md#inspect-quality-and-consumption),
   including delivered files, citations, failures, and
   [usage/cost/time accounting](docs/system/ACCOUNTING.md).

The shared writable-file engine is named `repair` in CLI records; `artifact` is
its command alias, and task packages distinguish repair and document families.
CLI `--help` and `schema KIND` own the exact interfaces. JSON data goes to stdout,
diagnostics to stderr. Exit codes are 0 for success, 1 for a partial/failed
qualification, 2 for invalid input or an operational error, and 130 for interruption.

## Examples and limits

| Example | What it exercises | What it cannot establish |
| --- | --- | --- |
| [Review calibration](examples/calibration/review-v1) | Supported/unsupported findings, duplicates, coverage, and unresolved judgments | Representative review difficulty or independent label truth |
| [Receipt-admission repair](examples/repair/receipt-admission) | Captured repository context, exact edits, baseline/reference controls, and semantic assessment | Broad environment coverage or a skill effect |
| [Product-page writing](examples/document/product-page) | A public product source, complete upstream skill, document checks, and semantic criteria | Conversion gains or a representative writing population |
| [Judge reliability](docs/system/RELIABILITY.md) | Repeat, order, and within-packet perturbation controls | General judge reliability or prompt-injection resistance |

A completed run is an execution observation. A recorded judgment is an assessment
with an audit status. Neither is automatically a correctness claim. Small exposed
controls and single pairs cannot rank skills; representative tasks, independent
human audit, repeats, and uncertainty analysis are still required.

Imported code runs inside the guest. Credentials stay on the host behind the
bounded inference gateway. Prompt restrictions are not command-level enforcement,
and a changed runtime/image/source identity invalidates prior admission. Use the
[recovery and retention procedure](docs/system/ENVIRONMENT.md#recovery-owns-the-whole-vm)
for interrupted runs. Never publish raw task captures or traces without reviewing
them: they can contain private code and context even when stored under `.local/`.

## Contributing

Focused fixes, documentation improvements, and grounded evaluation proposals are
welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md), the
[architecture](docs/system/ARCHITECTURE.md), and the
[backlog](docs/project/BACKLOG.md). [AGENTS.md](AGENTS.md) contains the working
agreements and documentation map for coding agents.

Python owns the CLI, execution contracts, evidence, and grading. A TypeScript
review UI and a pi adapter are later work. Interfaces are provisional; no stable
release or package-registry publication is claimed.

## License and sources

Original project work is licensed under [Apache-2.0](LICENSE). The adapted
OpenBench gateway and associated adapted tests retain their
[MIT notice](src/skilleval/_vendor/OPENBENCH_LICENSE); [NOTICE](NOTICE) records
that boundary. Captured tasks and skill packages keep their own licenses.

The design draws on Tessl, SkillsBench/BenchFlow, Harbor, Anthropic's skill-creator,
adewale's skill-eval-harness, gstack, Dojo, OpenBench, and Engram, alongside the
skill-evaluation papers and runtime/harness references in the
[source register](docs/project/SOURCES.md). That register distinguishes copied
implementation, independent design adoption, and candidates for future use.
