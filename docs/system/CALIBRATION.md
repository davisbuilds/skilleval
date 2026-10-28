# Semantic grader calibration

`examples/calibration/review-v1` is a public, newly authored development corpus.
Its source, reports, questions, labels, rationales, and evidence citations were
created for this repository; they contain no copied private task content.
Labels are inspectable author judgments, not independently established ground
truth. Ordinary checks validate storage and comparison behavior; they do not
execute the fixture source or call a model.

The twelve controls require semantic reasoning about three short source trees:

| Probes | Authored target |
| --- | --- |
| p01, p12 | Supported quantity bug and an independently reported paraphrase |
| p02 | Duplicate of an earlier finding within the same report |
| p03 | Supported threshold bug absent from the earlier reference |
| p04 | False positive about integer arithmetic becoming floating point |
| p05 | Refund bug omitted by a report that finds other real bugs |
| p06 | Quantity bug represented in the report |
| p07 | Valid completed clean report with an empty findings list |
| p08, p09 | Absent refund/threshold defects in the corrected source |
| p10 | Insufficient remote-client contract evidence |
| p11 | Invalid report whose findings field is a string |

For example, the buggy checkout charges a 1200-cent item ordered three times as
1200 cents before shipping. It also charges shipping at a 5000-cent inclusive
free-shipping threshold. Refunds totaling 800 cents followed by a request for
300 against a 1000-cent payment pass the buggy guard. The corrected source
rejects that refund and excludes equality from the paid-shipping branch. These
are static, source-grounded reasoning probes; no host execution is needed.

## Explicit offline workflow

First freeze the authored tree with `skilleval capture` into a fresh
external or ignored location. The resulting manifest digest identifies the
complete suite, including labels. `calibration.prepare_calibration(bundle,
destination)` verifies that snapshot and creates a fresh private packet.

The packet includes only each case's instructions, source, selected report fields,
neutral probe identifiers/questions, packet metadata, and `ASSESS.md`. It excludes
`suite.json` labels/rationales/citations, structured treatment arms, historical
scores, earlier-reference metadata, and arbitrary files outside the allowlist.
The report field allowlist is `report_id`, `outcome`, `run_completed`, `findings`,
and `scope_drift`. Report/source prose can still reveal identity or contain hostile
instructions; this export does not establish independent blinding or injection
resistance. Give an unexposed judge only this packet and the preparation receipt.
Do not give it the original suite or these descriptions of expected answers.

The preparation receipt includes `evidence_sha256` and `packet_sha256`. The latter
is SHA-256 of the canonical JSON, with two-space indentation and a trailing
newline, mapping sorted exported relative paths to each file's SHA-256. It covers
all exported payloads, including the exact rubric. Its value is supplied through
the receipt because a file cannot contain its own hash. Keep that receipt with
the actual judge invocation. Model, effort, execution surface, packet receipt,
actual instructions, prior exposure, and unobserved procedure limits belong in
the retained run evidence; preparation cannot assert how the judge was invoked.

Run the judge explicitly through an appropriate native harness. The offline
`calibration.py` module has no provider client, credentials, subprocess execution,
or model-call shortcut.
A built-in agent evaluating an isolated packet is a development observation; it
does not qualify a separate guest/provider runtime.

## Native source-informed judging

The separate `judge` workflow prepares packets for the explicit native VM runner.
It reuses these twelve authored controls; no new independent labels or holdout
population are implied. Follow [NATIVE.md](NATIVE.md#prepare-freeze-run-and-record-a-calibration-judge)
for the exact prepare, snapshot, `native qualify`, and record sequence. The older
offline workflow and its `calibration-review-v1` records remain supported.

| Packet | Default investigative rubric | Static rubric (`--static`) |
| --- | --- | --- |
| Authored calibration | `calibration-agentic-v1` | `calibration-static-v1` |
| One native review report | `review-agentic-v1` | `review-static-v1` |

Static and investigative calibration packets contain the same case evidence and
questions. Their versioned rubric, policy metadata and packet identity differ.
The investigative rubric allows the judge to choose bounded reproductions only
inside the disposable VM, using writable clones and retained artifacts. The
static rubric forbids source execution as a declared prompt policy; tools remain
available in both variants. Neither preparation nor this policy comparison proves
injection resistance or command-level enforcement.

The judge gets source, selected report fields, neutral probes and an explicit
result schema. Authored labels, rationales, expected citations, structured arms,
scores and historical references stay outside its input. A new review packet
contains the selected native report plus original instructions/source, excluding
historical reports and the oracle. Prose and prior exposure can still reveal
identity. Treat source, reports, commands embedded in comments, and probe outputs
as untrusted data throughout.

`schema agentic-calibration-result` and `schema agentic-review-result` expose the
native result contracts. Native records require `assessor_kind: llm`, actual model
identity and `investigation_status`: `not-permitted` for static packets, or
`not-needed`, `completed`, or `blocked` for investigative packets. The rubric lets
the judge decide when tools add evidence; it does not reward a tool sequence or
penalize a justified unresolved answer. Native invocation status is recorded
separately from the judge's investigation status and semantic judgments.

Finding and coverage verdicts retain the meanings below. Native calibration
judgments use `probe_id`; review judgments use each zero-based `finding_index`
exactly once. Novel true findings receive credit on their merits. Resolved native
judgments require citations to original evidence from their own case, verified
against the retained host packet. Native recording also accepts exactly one
`task/` prefix on an otherwise allowed original path when that collected guest
member exists and its bytes equal the host original. The raw citation is retained;
the alias does not admit other cases, modified files or out-of-range lines.
Offline calibration still requires canonical packet paths. `artifact_citations`
can additionally name readable retained files under `task/investigation/CASE_ID/`; review artifacts use
`task/investigation/review/`. Those citations are checked against collected bytes
and line ranges. Modified clones or reproduction logs cannot replace original
source citations or become evidence of a missing contract.

`judge record-calibration` and `judge record-review` verify the original evidence,
packet, exact prepared prompt, native model/policy, final text and artifact hashes.
The frozen native input must remain available for this check. A review's native
execution status is accepted only when its report bytes match the hash-bound
collected final artifact; an edited report cannot borrow an older completed
status. Without native execution evidence, review execution stays unknown.
Missing or malformed reports, and failed or partial executions, never imply a
clean report.

Recording creates `<result_id>/result.json`, `audit.json`, the exact packet and
native evidence. Calibration audit data includes the authored labels only after
the judge run and a per-run confusion matrix with expected, supplied, missing and
unresolved denominators. Review audit data retains the reviewed native run.
Use these embedded comparisons for native records; `calibration compare` accepts
the older offline calibration-result contract. Revisions require a new result ID
and explicit `supersedes`; earlier records remain intact.

Every native audit starts as `required-pending`. An independent human must inspect
the original evidence, substantive verdicts and reproduction limits before a
scored correctness claim. No native result, hash check, authored-label agreement,
or additional model pass supplies that independent human audit automatically.
The current workflow implements preparation, execution and retention; live
workload success and general judge reliability require their own evidence.

## Offline result contract and recording

`CalibrationResult.model_json_schema()` exposes the strict version-1 result
contract. Unknown fields, duplicate JSON keys, nonfinite values, unknown probe
IDs, duplicate probe judgments, identity drift, incompatible verdict classes,
and citations outside the permitted packet files or line ranges are rejected.
Citation line numbers refer to exported evidence, including sanitized report JSON.
Resolved verdicts require at least one citation to their own case. `ASSESS.md` and
`probes.json` may supplement case evidence; packet identity metadata, hidden
labels, and other cases cannot be cited. Unresolved may omit citations.

A result names `result_id`, `suite_id`, `evidence_sha256`, `packet_sha256`,
`rubric_version: calibration-review-v1`, `assessor_kind`, `assessor`, `judgments`,
and optional `limitations`. An LLM result also requires `model`. Use the actual
model/effort and invocation surface in identity/limitations, and disclose prior
exposure. Each judgment names `probe_id`, `verdict`, `reasoning`, and `citations`
with normalized `path`, inclusive `line_start`, and `line_end`.

Finding verdicts are supported, unsupported, duplicate, or unresolved. Report
verdicts are valid-report, invalid-report, or unresolved. Coverage verdicts are
covered (real and represented), missed (real and omitted), absent (claim disproved),
or unresolved. A paraphrase in its own report can be supported; duplicate applies
to earlier findings in the same report.

`record_calibration(bundle, result_file, output)` verifies evidence and packet
identity and exclusively creates `<result_id>.json`. Existing records cannot be
overwritten. Partial records are allowed; omitted judgments remain missing. A
reassessment uses a new ID and an explicit analysis choice about which records
to compare. Recording establishes structural acceptance, not semantic correctness.

## Offline comparison and limits

`compare_calibration(bundle, result_files)` compares each supplied run separately.
It never combines different judges' partial coverage into an apparent complete
run. Duplicate result IDs are rejected. Invalid inputs are reported separately
with their zero-based input index and a value-free reason; they contribute no
invented judgments. The original captured suite is always verified first.

Each valid run exposes the complete expected-by-observed confusion matrix,
agreement/disagreement counts, probe/judgment/resolved/unresolved/missing counts,
missing probe IDs, and per-label expected/missing denominators. Probe count is the authored opportunity denominator;
judgment count is the actual supplied denominator. Expected unresolved labels can
agree with an unresolved response while still contributing to unresolved count.
No scalar quality score or implicit zero for missing coverage is produced.
When there is no valid result, `unassessed_probe_count` equals the suite size;
with valid results it is null because missing coverage belongs to each run.

Coverage probes explicitly offer defect hypotheses, including negative controls.
They test whether a judge can distinguish known missed, represented, and absent
defects. They do not measure unaided discovery or exhaustive review recall.
Agreement on this small, public, exposed development set is a calibration receipt
with specific limits. It establishes neither general grader reliability nor an
incremental benefit from using a skill. Author-label disputes need documented
adjudication and a newly captured suite, preserving the earlier generation.
