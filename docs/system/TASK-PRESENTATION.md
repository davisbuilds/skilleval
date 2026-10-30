# Task presentation and solver visibility

Give the task agent an ordinary work request and the context needed to do it.
Keep study arms, scores, rewards, hidden checks, reference solutions and evaluation
machinery outside its accessible context. ON adds only the selected skill's native
invocation; OFF receives the shared brief. Do not tell either arm it is under
evaluation, ask it to maximize a score, or instruct it to ignore an evaluator.
Do not add a false claim that it is not being evaluated.

This is separate from [operator-state isolation](NATIVE.md#operator-state-and-effective-context).
A clean home and correct skill discovery do not hide a controller running with
the same guest privileges. It is also separate from masking treatment information
from the semantic judge. The judge necessarily receives assessment instructions;
those instructions must never become solver input.

## Current audit

Source and native-route inspection on **2026-10-30** found the following. The
native probe used Codex 0.164.0 and smolvm 1.32.3 with synthetic Responses through
the production runner. It used no credential or model inference. Original
requests, tool output, native audit and cleanup evidence were retained privately.

| Surface | Observation and disposition |
| --- | --- |
| Shared prompt and ON/OFF rendering | `study._invocations` passes the shared brief unchanged to OFF and adds only `Use $NAME for this task.` to explicit ON. The native runner adds no evaluation preamble. Caller-supplied prose still needs inspection. |
| Repair and document example briefs | Both said the evaluator collects file bytes; the repair also mentioned a claimed score. Removed those disclosures from the templates. Existing frozen inputs and results retain their original bytes. |
| Native provider configuration | The readable profile named `qualification-broker` / `skilleval qualification`. New profiles use `local-gateway` / `Local gateway`, retaining the custom provider's protocol behavior. |
| Skill notice packaging | New captures place the supplied root notice at `.source-notices/ROOT_LICENSE`. Old `.skilleval-notices/ROOT_LICENSE` captures remain loadable without mutation; neutral presentation requires a new capture. Upstream skill files and license text remain unchanged. |
| First native request | Inspected the native instructions, tool catalog, skill catalog, task instructions and a normal brief on the patched route. No evaluation announcement was found in that request. Matches for “evaluates” in the JavaScript tool description described executing code. This is one inspected input, not a general absence detector. |
| Guest files and processes | **Still exposed.** A native tool ran as UID 0 and read the controller, context guard, settings, tooling archive, provider config and `/opt/skilleval/openbench_gateway.py`. It also read the controller command line through `/proc`. The full-access solver and controller share authority. |
| Declared versus effective capabilities | The native permission message says network access is enabled, while the enclosing VM disables general networking. Align the native profile and task-visible capability description with actual permissions when qualifying the solver boundary. This mismatch is not itself an evaluation announcement. |
| Historical review exporter | **Still exposes preparation context.** It preserves `.openbench-review.json` and constructs Git commits authored by `Captured review`. This is a development importer, not a neutral presentation route. Do not rewrite its pinned history or silently remove files. |
| Task snapshots and skill trees | The host capture manifest is excluded from native task transfer. Payload files, ordinary guidance, Git objects and the selected skill tree are copied as supplied; there is no general semantic filter for evaluation cues. A source-only snapshot is not automatically a suitable solver view. |
| Qualification and judge fixtures | Synthetic context controls and the legacy calibration demo intentionally disclose their control purpose. Judge packets intentionally contain rubrics. Neither is a representative solver task. |

The probe successfully read the intended task source and the control files, then
cleaned up its VM and runtime home without identity drift. It demonstrates the
access gap, not that a real model noticed or acted on it. **The current route is
not qualified to conceal evaluation infrastructure.** The earlier 56 context
controls concern operator-state exclusion and matched skill exposure; they do not
establish this property. Neutral wording alone does not close the gap.

## Boundary required before new comparisons

Keep the controller, evaluator sources, staging archives, study records and grader
artifacts outside the solver's readable namespace. Use a dedicated unprivileged
solver identity and an enforced filesystem/process boundary, or place the
controller outside the solver container. A separate directory, a permission mode
while both processes are root, or a prompt asking the agent not to look is
insufficient. Preserve the inference transport and ordinary task capabilities;
renaming an evaluation path does not establish separation.

Verify on the actual native route, including subprocesses and indirect reads:

- The solver can read task source, ordinary instructions, required history,
  selected skill resources and dependency/tool files, and can edit/test the task.
- It cannot read controller/evaluator files, original staging archives, hidden
  labels or alternate solutions through direct paths, symlinks, `/proc`, process
  arguments, inherited descriptors, mounts or image layers accessible in the guest.
- Both arms receive the same ordinary context and capabilities. Record UID,
  effective permissions, image/configuration identity and actual native requests.
- Known-positive sentinels show the detector sees an intentionally exposed file;
  access refusals must occur while the allowed work still succeeds. Keep model
  self-reports separate from tool-access evidence.

Review each task's brief, filenames, ordinary guidance, full accessible Git
history, fixtures, dependency metadata, skill resources and expected tool errors
before capture. Keep the grader, its tests and historical repair outside the
solver view. Existing project tests, genuine product requirements and an upstream
skill's own examples remain legitimate context; do not remove every occurrence
of “test”, “evaluation” or “benchmark”. If faithful upstream material itself
discloses evaluation, record the limitation or select another task/skill instead
of silently editing the treatment.

No setup can prove what the model infers about its situation. The acceptance
claim is narrower: inspected task presentation contains no evaluation disclosure,
and tested access paths cannot reach evaluation-only context. Record remaining
inference cues such as offline tools or constrained interaction; do not call them
proof of awareness or its absence. Changed profiles and packages need new evidence
generations. Existing attempts are not retroactively blinded.
