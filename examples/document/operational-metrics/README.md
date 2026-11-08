# Operational-metrics specification pilot

This ordinary AgentMonitor feature request asks for a useful read-only console
specification. The [shared brief](prompt.txt) delegates product choices within
the existing application. OFF receives that brief; ON receives the same brief
prefixed only with `Use $write-spec for this task.` and the intact skill package.
No implementation-plan skill or live Q&A is part of this treatment.
The brief asks for one self-contained Markdown spec, so dropping other generated
documents at handoff is an explicit shared constraint, not a hidden disadvantage.

Each selected generated specification then goes to a fresh vanilla implementer
with the original request and baseline. Assess both the specification and the
implemented outcome, alongside writer, implementer and combined consumption.
Judge overhead stays separate. The [handoff contract](../../../docs/system/HANDOFFS.md)
owns exact artifact selection, lineage, incomplete outputs and accounting.
This is one exposed development task, not a representative sample or a skill
effect estimate. Authored controls and structural validation grant no scored
admission or independent semantic reliability.

## Source and treatment

Use a checkout of public [AgentMonitor](https://github.com/davisbuilds/agentmonitor)
at `0dd253129d143b5811224a0f80554a64eb3f4da0`. Capture reads committed objects
without executing code or changing that checkout:

```sh
uv run skilleval source capture /path/to/agentmonitor \
  --revision 0dd253129d143b5811224a0f80554a64eb3f4da0 \
  --exclusions examples/document/operational-metrics/source-exclusions.json \
  --output /path/to/fresh-selected-capture
```

Retain an unfiltered capture separately by omitting `--exclusions`. Supply only
the selected `baseline/` identically to both arms; receipts and the unfiltered
capture stay outside solver input. The policy removes 14 tracked entries:
`docs/specs`, `docs/archive/specs` and `docs/archive/plans`. These comprise three
archived specs, two historical baseline references, an empty placeholder and
eight plans that repeat related conventions. Ordinary agent guidance, source,
tests and current product documentation remain. Removing historical baseline
references is an explicit collateral effect, not a claim that they are specs.

Git tracks these archives despite `**/archive/` in `.gitignore`; ignore-aware
file searches miss them. [Source capture](../../../docs/system/SOURCE-CAPTURE.md)
uses the pinned inventory and a new parentless `Project snapshot` commit on
`main`, without upstream history or remotes. This prevents recovery of excluded
objects through that history. It does not erase prior model knowledge of spec
formats or establish absence of every related convention. Selection is a
declared context intervention; comparison with full ordinary documentation would
answer a different question. Preserve earlier capture generations unchanged.

The selected treatment is Dojo `write-spec` 3.0.0 at
[`979b42a`](https://github.com/davisbuilds/dojo/tree/979b42a7366d4331ad65dd85f59b90b4efdb6737/skills/write-spec).
Capture the whole skill directory with `treatment capture`, including references,
templates, scripts, command metadata and authored eval resources. Record any
existing notices and the root-notice availability honestly; do not invent one.
These resources are upstream package contents, not current experiment labels.
The skill is an external captured input, not redistributed in this example.

## Freeze the task and environment

```sh
uv run python examples/document/operational-metrics/prepare_task.py \
  --input /path/to/selected/baseline --sha256 BASELINE_SHA256 \
  --output /path/to/operator/task.json
```

The helper trusts the operator's selected capture and freezes the common prompt,
`docs/specs/` output grant and a byte-pinned worker outside solver input. Arbitrary
nonempty UTF-8 `.md` and `.markdown` filenames are accepted. The worker observes
readability and presence; it cannot decide whether prose is complete or correct.
Question-only, blocked and unfinished files deliberately pass that structural
check and still require semantic assessment. Out-of-scope edits remain visible.

Build the [opt-in image](../../../environments/operational-metrics/README.md) with
the pinned source's exact manifests and Chrome for Testing. The
[environment map](environment/README.md) supplies six lanes: effective tools,
ordinary checks, root tests/build, frontend tests, metrics contracts and built
console/API browser interaction. Imported application code runs only in the VM.
Synthetic disposable state replaces real sessions, provider credentials and the
operator database. An existing-console browser smoke does not test a feature
that has not yet been implemented.

`prepare_package.py` consumes an operator-reviewed expectations object keyed by
those six lane IDs, each containing `exit_code` and `observed`. It embeds exact
wrapper/fixture bytes in operator commands and leaves them outside task input.
For initial characterization, explicit empty expected objects produce mismatches;
inspect retained raw logs, failures and skips before freezing a new expectation
generation. Never automatically bless whatever the baseline did.

```sh
uv run python examples/document/operational-metrics/prepare_package.py \
  --task /path/to/operator/task.json --task-sha256 TASK_SHA256 \
  --image /path/to/image.tar --image-sha256 IMAGE_SHA256 \
  --expected /path/to/reviewed-expectations.json --output /path/to/package.json
uv run skilleval task-package validate-environment /path/to/package.json \
  --sha256 PACKAGE_SHA256 --runtime /path/to/smolvm --output /path/to/validation
```

Matching declared baseline failures or skips is not a clean suite. Full raw
observations, screenshots and budgets remain retained alongside stable comparison
fields. The actual source, image, runtime and execution fingerprints must match
before a packaged study can dispatch.
The [qualified baseline record](environment/README.md#qualified-development-baseline)
documents the completed six-lane pass and its explicit test exceptions. A frozen
two-cell development plan is prepared; the pilot itself has not run.

## Acceptance and judgment

Bind [acceptance-map.template.json](acceptance-map.template.json) to the real task
and package identities before a comparison. It covers completed delivery,
reported recency, outcomes/skips, aggregate meanings, useful interactions,
read-only scope and verifiable handoff. Layout, navigation location, time window,
refresh policy, document length and skill-specific headings are not answer keys.

Use [assessment-spec.template.json](assessment-spec.template.json) with
`rubric_profile: "specification-v1"` and `execution-context-v3`. The
[control manifest](controls/manifest.json) includes two different valid drafts,
misleading count/health claims, questions, a blocked note, an unfinished outline
and missing output. Labels are authored expectations, open to disagreement;
automated review is development evidence, not human audit. Keep all control
artifacts, labels and maps outside both solver stages.

[Downstream observation planning](controls/downstream-contract.json) preserves
the original need as authority for later UI/API and regression assessment.
Those planned feature checks are not executable coverage. Inspect actual browser
behavior and supported semantics after implementation; merely obeying a flawed
generated spec is insufficient. No ON/OFF outcomes have been established by this
preparation. Use a new generation when requirements, prompts or graders change.
