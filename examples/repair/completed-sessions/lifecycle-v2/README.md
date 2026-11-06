# Completed-session lifecycle controls, generation 2

These are exposed, retrospective development controls authored after earlier
outcomes. They supplement the unchanged [six-probe generation](../README.md).
They do not revise its tasks, scores, judgments or admission, and are not a new
holdout. No candidate implementation or retained private transcript is included.

`requests.json` contains newly authored synthetic records; `expected.json`
specifies intended behavior independently of diagnostic outputs. Preparation
freezes both into a fresh task and copies the self-contained worker beside it.
The worker never loads expected outcomes. It imports application code only in
the offline Linux repair-check VM as UID 65534, with application homes, database
and generated state in disposable scratch outside source.

The controls cover:

- `cached-sibling-handover`: ordinary unforced import followed by browser sync
  for a full archive, a shorter preferred plain sibling, removal of that sibling,
  and repetition. Exact messages and usage must go from 4 messages/1220 tokens to
  2/840 and back to 4/1220, without duplicate identities, messages or responses.
  The archive bytes remain unchanged throughout. This follows the brief's
  preservation and idempotence requirements; it does not prescribe a cache fix.
- `reader-limit`: a newer completed archive coexists with an older plain
  session. A limit of one must produce one checkable, accurate comparison, and a
  limit of two must compare both distinct sessions. Either eligible session is
  accepted at limit one. Extending discovery must preserve an ordinary usable
  plain-session comparison; recovered archives must also be checkable. This is
  a supporting reader regression control, not a requirement to sort in one
  particular order or use one decoder helper.

The static host tests establish fixture arithmetic, preparation, transport and
schema behavior only. They do not validate application behavior. Before treating
a replay as positive or negative evidence, declare its role separately: untouched
baseline (missing archive capability), retained candidate (known defect, not a
holdout), or independently authored positive control (intended valid behavior,
still requiring actual replay). A rejected baseline alone cannot establish that
the verifier accepts a valid repair. The retained application process shares the
observation worker; these finite probes do not establish adversarial robustness.

## Prepare and replay separately

From the skilleval checkout, prepare against the original retained baseline
capture, outside solver input. This operation runs no imported application code:

```sh
uv run --locked python examples/repair/completed-sessions/lifecycle-v2/prepare.py \
  --input /absolute/external/baseline-capture --sha256 BASELINE_MANIFEST_DIGEST \
  --output /absolute/external/lifecycle-v2-task.json
```

To check an existing retained candidate under the new generation, create a new
authored-control result against that candidate snapshot. Preserve the original
collected result and its native-attempt provenance separately; the new result
records control provenance, not another solver attempt:

```sh
uv run --locked skilleval repair control /absolute/external/lifecycle-v2-task.json \
  --task-sha256 NEW_TASK_DIGEST --candidate /absolute/external/retained-candidate \
  --candidate-sha256 CANDIDATE_MANIFEST_DIGEST \
  --output /absolute/external/lifecycle-v2-control
```

For supplementary checks that will be selected in a native study report, collect
from the **original retained native run** instead. A candidate snapshot alone
does not establish native-attempt provenance:

```sh
uv run --locked skilleval repair collect /absolute/external/lifecycle-v2-task.json \
  --task-sha256 NEW_TASK_DIGEST --run /absolute/external/original-native-run \
  --output /absolute/external/lifecycle-v2-native-result
```

This verifies the shared input and task prompt, including the declared treatment
wrapper when present, and retains the original native receipt. It does not rerun
the solver. Check this new result in a fresh destination and use that result/check
pair in the assessment and outcome selection. Preserve prior control results;
never edit their provenance or reuse a check report bound to a different result.

Only after freezing and qualifying the changed execution identity, run this
credential-free application replay in the VM (never on the host):

```sh
uv run --locked skilleval repair check /absolute/external/lifecycle-v2-task.json \
  --task-sha256 NEW_TASK_DIGEST --result /absolute/external/lifecycle-v2-control \
  --result-sha256 CONTROL_DIGEST --runtime /absolute/external/smolvm \
  --image-archive /absolute/external/image.tar --image-sha256 IMAGE_DIGEST \
  --output /absolute/external/lifecycle-v2-check
```

Use fresh output destinations and retain the task, worker, input and candidate
digests with each replay. Node's Zstandard support, tsx and native dependencies
must be qualified in the offline image. No new solver or judge call is required.
No replay or qualification is implied by preparation or these commands.

Remaining gaps include corrupt archive replacement after a successful import,
full watcher-service event ordering (including unlink/debounce handover), other
verification-tool readers, and the wider lifecycle space. The older corrupt-first
probe does not cover preservation during replacement. These controls do not
establish scored admission, judge reliability or a skill-effect estimate.

The exact intermediate shorter-plain expectation is a fixture policy, not proof
that the brief forbids retaining or merging longer history while both forms exist.
Challenge that expectation with legitimate alternative repairs before using it as
a general acceptance rule. The final restored-history observation can independently
expose lost history after the plain sibling disappears. Likewise, distinguish
preserving a previously checkable plain session at limit one from requiring every
existing reader to support archives; the latter needs separate scope review.
