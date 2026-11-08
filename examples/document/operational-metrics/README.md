# Operational-metrics specification pilot: source preparation

This is an ordinary feature request for AgentMonitor's operational-metrics console
view. The shared [brief](prompt.txt) asks for a useful specification; the proposed
ON arm adds only `Use $write-spec for this task.` and the intact skill package.
Fresh comparable vanilla agents would implement each generated specification.
No implementation plan skill is part of this treatment.

The source-preparation slice is usable. Task packaging, a controlled clarification
policy, linked implementation handoff, acceptance/grader controls and actual VM
qualification remain outstanding. These files do not define a runnable study or
establish specification quality, judge reliability or skill benefit.

## Freeze source with selective exclusions

Use a local checkout of public [AgentMonitor](https://github.com/davisbuilds/agentmonitor).
The inspected source revision is `0dd253129d143b5811224a0f80554a64eb3f4da0`.
The capture command reads committed objects without executing repository code or
changing the checkout, even if its working tree differs from that revision.

```sh
uv run skilleval source capture /path/to/agentmonitor \
  --revision 0dd253129d143b5811224a0f80554a64eb3f4da0 \
  --exclusions examples/document/operational-metrics/source-exclusions.json \
  --output /path/to/fresh-selected-capture
```

Keep an unfiltered capture of the same revision separately by omitting
`--exclusions`. Retain both receipts and their manifest/provenance digests. Supply
only the selected `baseline/` to task preparation, identically for both arms;
neither the unfiltered capture nor exclusion metadata belongs in solver input.

The checked-in policy removes `docs/specs` and `docs/archive/specs`: six tracked
entries at the pinned revision, comprising three archived specs, two historical
baseline references and one empty placeholder. The baseline references concern
storage and an importer protocol; their removal is recorded even though they are
not examples of the selected skill. The operational-metrics contract remains in
current project/system docs, source and tests. Required reference coverage must
still be checked during task/environment qualification.

The eight tracked archived plans remain under this policy. Some repeat readiness
metadata and related specification conventions or link to the withheld specs.
Thus this policy withholds existing specification files; it does not establish
absence of related workflow examples. Widening the policy requires a fresh
capture, not modification of this generation. Neither variant removes the
model's prior knowledge of common specification conventions.

Default ignore-aware file searches miss these archived documents: Git tracks
them despite `**/archive/` in `.gitignore`. Source capture uses the pinned Git
inventory, applies exact selectors, and creates only a new synthetic baseline
commit so removed contents cannot be recovered from original Git history.
The [source-capture contract](../../../docs/system/SOURCE-CAPTURE.md) describes
object controls, supported links, restrictions and operator-only provenance.

Selection is a declared context intervention for this pilot. Preserve ordinary
`AGENTS.md`, implementation, tests and current product documentation. A later
comparison with the repository's full ordinary documentation would answer a
different deployment-context question.
