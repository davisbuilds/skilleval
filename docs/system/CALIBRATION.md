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

Run the judge explicitly through an appropriate native harness. This module has
no provider client, credentials, subprocess execution, or model-call shortcut.
A built-in agent evaluating an isolated packet is a development observation; it
does not qualify a separate guest/provider runtime.

## Result contract and recording

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

## Comparison and limits

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
