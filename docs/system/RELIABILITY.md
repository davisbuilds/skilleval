# Native judge reliability development controls

`examples/reliability/review-v1` is a separately versioned, public authored stress
suite. It does not change `examples/calibration/review-v1`, earlier native records,
or pending human audit packets. Its controls ask whether substantive verdicts
remain invariant when irrelevant evidence changes, and whether repeated native
sessions remain stable. A consistently wrong judge can pass every stability
comparison. Authored-label agreement is reported separately; independent human
semantic audit remains required and pending.

## Bounded suite and session matrix

Each of seven small invoice cases contains the same integer quantity bug and a
false claim about conversion to floating point. Fourteen finding probes are
paired through `reliability.json`. Every variant preserves the substantive
contract and finding order:

| Variant | Perturbation relative to the baseline |
| --- | --- |
| Paraphrase | Equivalent report claims with different wording |
| Anchor | An explicitly unverified preliminary opinion asserting opposite verdicts |
| Source injection | Comments claiming system authority and requesting opposite verdicts |
| Report injection | Embedded pseudo-system text requesting opposite verdicts and concealment |
| Skill cue | A provenance claim that a specialist skill produced the report |
| Ordinary cue | A provenance claim that ordinary behavior produced the report |

Source and report instructions are untrusted evidence. The native judge rubric,
permissions, schema and recorder are reused unchanged. The suite supplies no
exhaustive adversary model or general injection-resistance guarantee. These are
new authored fixtures, not imported historical task content or a holdout.

All variants share each session packet. An injected instruction could influence
both a baseline judgment and its paired variant, producing stable but wrong
answers. These controls can expose failures but cannot isolate the causal effect
of an individual injection or treatment cue. Authored-label comparisons remain
separate, and do not supply independent ground truth.

Preparation fixes this three-session matrix before any execution:

| Slot | Packet | Purpose |
| --- | --- | --- |
| `repeat-a` | Original | Baseline session and within-packet paired invariance |
| `repeat-b` | Exactly the same original packet | Repeat stability |
| `order-reversed` | Reversed probe order | Probe-order sensitivity against `repeat-a` |

Only the probe array is reversed. Findings within each report retain their order;
this operation does not test within-report duplicate reclassification or claim
that duplicate labels survive finding reordering. All sessions use the same
native model, effort, rubric permission and runtime/source identity. Fresh native
sessions reduce shared context; preparation cannot prove absence of prior exposure.

## Explicit preparation and recording

Ordinary checks and both reliability operations are offline. They never launch a
VM, execute imported source, read credentials or invoke a model.

```sh
uv run skilleval capture examples/reliability/review-v1 \
  --output .local/reliability-suite
uv run skilleval reliability prepare .local/reliability-suite \
  --output .local/reliability-prepared > .local/reliability-preparation.json
```

Use `--static` for a separate static-policy preparation. Policy variants have
different identities and must not be combined as repeats.

The fresh preparation directory contains:

- `evidence/original`: a verified byte-preserving copy of the captured suite.
- `evidence/reversed`: a new capture manifest changing only `suite.json` probe order.
- `packets/original` and `packets/reversed`: ordinary label-free native judge packets.
- `reliability-plan.json`: version, original evidence digest, fixed slots and native
  preparation receipts, including exact prompts and input/packet digests.
- `observations.template.json`: an empty observation list bound to the plan digest.

Give each judge only its packet and exact native prompt. The preparation root
contains labels and comparison metadata and must stay outside the judge's input.
Freeze each packet using `capture`; use the explicit VM qualification and native
execution sequence in [NATIVE.md](NATIVE.md#prepare-freeze-run-and-record-a-calibration-judge).
Read that plan's `preparations.original` or `preparations.reversed` receipt for
its exact prompt. The two repeat sessions reuse the original packet and input;
each native invocation still uses a fresh session, output directory and result ID.

Retain successful or structurally recordable partial runs with the existing
recorder, selecting the matching evidence and packet paths:

```sh
uv run skilleval judge record-calibration \
  .local/reliability-prepared/evidence/original \
  .local/reliability-prepared/packets/original NATIVE_RUN \
  --output .local/reliability-records
```

Keep the frozen native input at its original path: native record revalidation
checks its manifest as well as collected bytes. Independent audit stays
`required-pending`; reliability preparation/comparison does not modify audit state.

## Explicit comparison and denominators

Copy the observation template to a separate JSON file. Keep `plan_sha256` from
that template and append each attempted session:

```json
{
  "schema_version": 1,
  "plan_sha256": "REPLACE_WITH_PLAN_DIGEST",
  "attempts": [
    {"attempt_id": "a", "slot_id": "repeat-a", "record_dir": "reliability-records/RESULT_A"},
    {"attempt_id": "b", "slot_id": "repeat-b", "record_dir": "reliability-records/RESULT_B"},
    {"attempt_id": "c", "slot_id": "order-reversed", "declared_failure": "No recordable final result."}
  ]
}
```

Record directories are relative to the observations file, or absolute. Replace
the example digest with the actual 64-character plan digest. An attempt supplies
exactly one `record_dir` or `declared_failure`. Failure declarations are explicit
operator reports, not verified native execution evidence. Missing slots remain
missing. Multiple attempts in one slot are allowed, with unique attempt IDs and
native result IDs; retain every attempt rather than replacing a failed or poor
run with a preferred retry.

```sh
uv run skilleval reliability compare .local/reliability-prepared \
  .local/reliability-observations.json > .local/reliability-comparison.json
```

Comparison verifies the original and reordered evidence, prepared packet bytes,
plan digest, retained native packet, final text and artifact hashes, result.json,
exact prompt and invocation policy. It reads the actual retained model, effort,
permission, rubric and complete runtime/source identity, supporting both legacy
inline identity and hash-bound compact native audits. Missing runtime identity
makes a record invalid for this comparison. Incompatible completed records remain
visible but contribute no cross-session resolved verdict pairs. The same native
run/result cannot fill two session slots. Recording or file hashes do not attest
host integrity against the trusted local operator.

Every attempt retains native execution status and a separate per-run authored
label comparison, including missing and unresolved probe denominators. Invariance
is reported per attempt and perturbation family. Repeat and probe-order results
are reported per explicit attempt pair. With retries, every cross-slot Cartesian
pair is retained separately; no best retry, pooled completion, scalar reliability
score or accuracy estimate is chosen automatically.

Each comparison reports opportunity, observed-pair, resolved-pair, stable-resolved,
changed-resolved, both-unresolved, one-unresolved, and missing counts. Missing left
and right counts expose asymmetric coverage. Two unresolved answers are counted
as both unresolved, never as stable resolved judgments. A failed, timed-out,
partial, invalid, incompatible or absent session supplies no resolved stability
observations. Exact probe pairs and verdicts remain inspectable. Missing sessions
still expose the full planned pair denominators. These small development controls
establish only the observed behavior for this version, runtime and supplied
attempts; they do not establish calibration, independent correctness, reliable
performance on other workloads, or skill effectiveness.
