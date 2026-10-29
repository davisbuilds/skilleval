# Receipt-admission environment qualification

This exposed development seed uses the public
[baseline](https://github.com/davisbuilds/openbench/tree/2afa7b355a48f8aa6f81eb071db9114ffdbca02b)
and [reference repair](https://github.com/davisbuilds/openbench/commit/47e49af284218f3cb9c116ca2aa20bbbe30f7afa).
The task is Python receipt validation; imported benchmark execution and production
Docker grading are separate capabilities. The
[environment contract](../../../docs/system/ENVIRONMENT.md#task-capture-and-baseline-fidelity)
owns the general capture and comparison requirements.

## Capture boundary

Retain ordinary source, instructions, tests, `data`, `experiments`, scripts and
gateway-probe fixtures. The development capture excludes only
`benchmarks/imported/` from the working tree. Its Git object inventory still
contains the pinned baseline closure; working-tree exclusion is not a secrecy
boundary or a claim that imported benchmark CI can run.

Keep the exact historical object used by Harbor core-task tests, plus the verifier
commits declared in gateway bundle manifests. Tests use those objects for native
Git archive and verifier-provenance checks. For this revision the dependencies
include one core-task commit and five distinct gateway verifier commits. A shallow
checkout's base history alone does not supply them all. Fetch a missing object by
explicit identity into an isolated evidence repository; do not alter a sibling
checkout or copy unrelated future history.

The validated capture retains 2,667 working-tree files and 4,719 Git objects, with
original included bytes and executable flags. It verifies a clean baseline Git
view, readable required commits and absence of the later repair commit and its
three changed blobs. The reference control changes only the declared implementation,
tests and documentation on the same baseline Git context. Reference code never
enters the solver's baseline capture.

## Observed validation, 2026-10-29

Fresh offline guests used the same pinned image as the development solver:
Linux/aarch64, Python 3.12.15 and root identity. The unit command used the solver's
observed Bash login-shell form:

```sh
/bin/bash -lc 'python3 -m unittest discover -s obench/tests -v'
```

| Source | Tests run | Failures | Errors | Skips |
| --- | ---: | ---: | ---: | ---: |
| Untouched baseline | 2,006 | 2 | 0 | 8 |
| Reference repair | 2,010 | 2 | 0 | 8 |

The four added reference tests are receipt-regression tests and all pass. Both
variants have the same failure and skip identities. Complete command logs were
retained below their output limits, no command timed out, and source inventories
remained unchanged. Separate restored gateway/site controls pass all 26 tests in
each variant. These observations establish bounded baseline equivalence, not a
fully green CI environment.

Both failures expect literal `root.txt` in checker evidence. The repository's
scrubber substitutes the current username, producing `<USER>.txt` under root.
Fresh narrow controls on the same readable source reproduce two failures in the
14-test checker-evidence module under root and pass all 14 under non-root. They
use the same supplied environment and command; Bash derives identity-specific PATH
values, with the exercised tools resolving identically. This validates the observed
identity interaction, not an unprivileged Codex execution profile. Historical tests
and the current solver identity remain unchanged.

The eight unit skips are six opt-in Docker tests, a missing-tmux test and a
permission-denial test that root cannot exercise. Non-imported core validation
passes all ten tasks. Local validation exits zero but skips all five tasks because
Node dependencies, PyYAML or Go are unavailable; it validates zero local tasks.
Imported benchmark validation and upstream Python 3.11/3.13 CI parity were not
tested. Those capabilities must not be inferred from the unit result.

The corrected observation worker also runs against this capture in separate offline
guests: it rejects the untouched baseline and accepts the reference on all ten
probes. This validates the repair adapter's integration with the new capture, not
task difficulty, exhaustive correctness or skill effectiveness.

## Evidence generations and next boundary

Original captures and agent attempts remain unchanged. The first expanded capture
revealed additional missing verifier commits, and its first non-root control failed
to import source because the directory was inaccessible. Those failures are retained;
the corrected generation adds the required objects and gives both narrow identity
controls readable source. No failed attempt is recast as a pass.

Captured source, manifests, complete raw logs, run scripts and comparison records
remain in an explicit local evidence directory. They are development inputs, not
publicly shipped benchmark data. The reusable capture CLI and general task-package
format remain future work; this case does not select them prematurely.

Before scored use, choose and qualify the solver user profile, declare the required
tool-dependent validation lanes and check the larger repair-judge packet against
artifact limits. Copying both complete before/after trees for investigation can
exceed the current 10,000-file collection ceiling. Existing ON/OFF runs and semantic
judgments belong to their earlier captures; no new solver or judge run is implied
by these offline controls. Human audit and scored admission remain pending.
