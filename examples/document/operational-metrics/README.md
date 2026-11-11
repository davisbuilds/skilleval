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
`docs/specs/` output grant, the exact `docs/project/BACKLOG.md` path and a byte-pinned
worker outside solver input. The backlog allowance accommodates an ordinary spec
link/status update required by repository guidance; it does not permit unrelated
code edits or replace the specification deliverable. Arbitrary nonempty UTF-8
`.md` and `.markdown` filenames under `docs/specs/` are accepted. The worker observes
readability and presence; it cannot decide whether prose is complete or correct.
Question-only, blocked and unfinished files deliberately pass that structural
check and still require semantic assessment. Out-of-scope edits remain visible.

Earlier frozen tasks retain their original scope and results. Any post-outcome
diagnostic recollection needs new task, package and acceptance-map identities,
applied equally to both arms with unchanged candidate bytes and explicit
post-outcome provenance. It does not retroactively qualify the original results.

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

Prepare all eight authored candidates against the same frozen ordinary baseline
and acceptance map without inventing a writer attempt:

```sh
uv run python examples/document/operational-metrics/prepare_controls.py prepare \
  --task /path/to/task.json --task-sha256 TASK_SHA256 \
  --acceptance-map /path/to/acceptance.json --acceptance-map-sha256 MAP_SHA256 \
  --runtime /path/to/smolvm --image /path/to/image.tar --image-sha256 IMAGE_SHA256 \
  --output /path/to/fresh-controls
```

The returned `controls.json` pins the authored expectations before judgment and
lists eight explicit `repair check` argument arrays. Run those guest checks only
after freezing the execution source, runtime and image. Preparation itself runs
no application code, worker, VM or model. The missing-output control deliberately
has no document; its completed worker observation can fail the artifact probe.
An incomplete check is an execution problem, not that control's expected result.

After all checks complete, finalize into fresh native assessment packets:

```sh
uv run python examples/document/operational-metrics/prepare_controls.py finalize \
  --prepared /path/to/fresh-controls/controls.json --prepared-sha256 CONTROLS_SHA256 \
  --output /path/to/fresh-control-assessments
```

Finalization validates the real pinned check and cleanup receipts. It creates
`assessment-spec.json`, `packet/` and `prepared-packet.json` per control for the
ordinary native judge/record route. It does not call the judge. Packets use the
versioned requirement policy and keep seven requirement judgments separate from
the four broad categories. Authored labels and their rationales stay outside
packets; comparison must preserve disagreements and unresolved judgments. The
authored-control provenance has no model attempt or native writer transcript.

Development judgments recorded for all eight controls cover all seven requirements
and four broad criteria. Both positive specifications satisfy every requirement;
the intended metric/health defects and incomplete delivery are distinguished.
Five authored-label disagreements remain visible across overlapping broad criteria,
interaction completeness and missing-output semantics. A separate source review
also questioned interaction and scope labels. One failed original invocation and
its fresh retry remain separate. These observations support further development,
not independent semantic reliability or a skill-effect estimate.

The [downstream contract](controls/downstream-contract.json) preserves the
original need as authority for UI/API and regression assessment. Its
[executable observer](downstream/README.md) drives the actual built console with
an operator-reviewed affordance plan, controlled responses, raw DOM/accessibility
and network evidence, screenshots, store/API comparisons and source inventories.
Two authored integrations and three defect mutations now have
[maintainer-observed guest and semantic controls](downstream/README.md#qualification-controls).
Changed machinery still requires fresh qualification; it does not award
correctness by lexical checks or by following a flawed generated spec.
No ON/OFF outcomes have been established by this
preparation. Use a new generation when requirements, prompts or graders change.


### Prepare and judge a downstream candidate

After selecting a writer artifact with `handoff prepare`, bind the implementation
contract to that exact handoff and the original source package:

```sh
uv run python examples/document/operational-metrics/prepare_implementation.py prepare \
  --handoff /path/to/handoff --handoff-sha256 HANDOFF_SHA256 \
  --source-package /path/to/writer-package.json --source-package-sha256 PACKAGE_SHA256 \
  --output /path/to/fresh-implementation-contract
```

The generated repair-family task permits retaining ordinary source changes for
review, including out-of-scope backend changes; collection permission does not
approve their scope. Its six semantic requirements come from the original need
and repository contracts. The generated spec remains input, not an answer key.
The small `source-present` worker establishes only availability of key source
files. Use the normal native collection and `repair check` route for its evidence.

Inspect the final candidate and write a neutral affordance driver as described in
[the observer guide](downstream/README.md). Bind it before observation:

```sh
uv run python examples/document/operational-metrics/prepare_implementation.py observe \
  --task /path/to/implementation/task.json --task-sha256 TASK_SHA256 \
  --result /path/to/collected-result --result-sha256 RESULT_SHA256 \
  --package /path/to/implementation/package.json --package-sha256 PACKAGE_SHA256 \
  --driver /path/to/operator-driver.json --output /path/to/observation-package.json
uv run skilleval task-package validate-environment /path/to/observation-package.json \
  --sha256 OBSERVATION_PACKAGE_SHA256 --runtime /path/to/smolvm \
  --output /path/to/fresh-observations
```

This observation package deliberately has no equality answer key for its dynamic
browser envelope. Its `unexpected`/aggregate `incomplete` comparison status is
not a feature verdict. Inspect retained process completion, bounded export,
actual browser scenarios and cleanup separately. The package's `reference` case
role is an existing schema slot for the candidate, not a reference-solution label.
Do not use this package as a passing study environment-qualification receipt.

Use `schema repair-assessment-spec` with the implementation task/result, its
completed source check, exact implementation map and requirement policy. Add this
pinned observation reference:

```json
{
  "observations": [{
    "package": "/path/to/observation-package.json",
    "package_sha256": "OBSERVATION_PACKAGE_SHA256",
    "report": "/path/to/fresh-observations/task-validation.json",
    "report_sha256": "OBSERVATION_REPORT_SHA256",
    "case_id": "candidate",
    "lane_id": "browser-observation"
  }]
}
```

Assessment preparation verifies the package/task/candidate/image, raw lane bytes,
recomputed observation and retained cleanup. It exports bounded decoded files to
`case/observations/observation-001/artifacts/`, alongside original source, candidate
and diff. Expected host values, control labels and treatment metadata are withheld.
Failed or partial observations remain evidence limitations; the judge must not
infer a missing feature merely from a broken driver. Run the ordinary investigative
judge and record route. Retain per-requirement judgments and citations alongside
broad repair criteria; no mechanical or semantic aggregate defines an automatic
winner. Use the [dated pricing scenario](../../pricing/README.md) for explicit
API-equivalent costs, with judge overhead separate from both task stages.
