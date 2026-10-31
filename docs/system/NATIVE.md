# Native development workloads

This reference owns the explicit native review, repair and judge procedure. These
runs use a fresh VM, the bounded host inference gateway, and retained evidence. They
remain development-only: `native qualify` never grants scored admission, and a
completed native turn does not establish a correct review or calibrated judge.
The [task-presentation reference](TASK-PRESENTATION.md) owns the enforced solver
boundary, tested access paths and limits of native permission wording.
Task/skill/history content and new image capabilities still require inspection;
the controls do not establish absence of every possible evaluation cue.
The [environment reference](ENVIRONMENT.md) owns runtime admission and recovery;
the [gateway reference](GATEWAY.md) owns authentication and provider limits.
The [repair reference](REPAIRS.md) owns editable scope, patch collection and offline
checks. Repair mode receives a normal implementation brief and leaves file edits;
review findings schemas and semantic review recording do not apply to it.
Document tasks use this writable-file mode with a separate document package and
rubric; [TASKS.md](TASKS.md) owns their preparation, grading, and readable reports.

## Inputs and boundaries

The production adapter runs `codex app-server` over stdio inside the solver
boundary. A controller outside that boundary initializes a fresh ephemeral thread,
then starts one turn with approvals disabled and
`externalSandbox` / `networkAccess: restricted`. The controller requires Codex's
effective-settings notification to confirm that policy. The VM and solver
boundary enforce it; the RPC declaration is not an additional sandbox.

The ordinary task brief is unchanged. ON still adds the selected skill's native
text invocation and installed files. App-server uses Codex's native tools and
skill discovery, but its transport and session source differ from `codex exec`;
qualification compares actual requests and tool behavior rather than assuming
byte equivalence. Model and reasoning effort still come from the pinned profile.

`app-server-rpc.jsonl` retains bytes read from native stdout, and
`app-server-input.jsonl` retains bytes successfully written to native stdin.
Either can end with a partial line after failure. `events.jsonl` contains derived
canonical events under adapter `app-server-v2-to-canonical-v1`; its harness is
`codex-app-server-jsonrpc-v2`. Legacy exec captures remain readable unchanged.
Final text comes from a successful, matched terminal exchange. Missing replies,
malformed records, unexpected approval requests, output limits and incomplete
drains prevent completion. The process deadline terminates the namespace;
turn-interrupt acknowledgments are not used as proof of termination.

Usage uses the latest matched cumulative snapshot, never the sum of snapshots;
missing telemetry stays unknown. [ACCOUNTING.md](ACCOUNTING.md) owns provenance
and reconciliation. Closing stdin after the matched terminal lets app-server
exit; collection still requires confirmed namespace teardown.

Use a prepared, hash-pinned image containing native Codex and the task's existing
tools/dependencies. Keep its selected runtime, catalog, image, runner source and
frozen task input stable for the run. Explicitly select an existing host login;
the broker reads it on the host and the guest receives only the inference socket.
Do not put credentials in task exports, prompts or skill bundles. Ordinary checks
and packet preparation do not read auth, launch VMs or call a model.

The native runner accepts a generic `capture` snapshot and a separately retained
manifest digest. It transfers payload files, excluding the host manifest. For a
review, export only the intended task source; a historical evidence bundle also
contains grading inputs and must not be handed directly to the reviewer. For a
judge, use the allowlisted packet from `judge prepare-*`, then capture that packet.
The judge must receive its exact preparation receipt's `prompt`, including the
packet digest. Keep the original evidence, raw packet and frozen native input
outside the guest and available through recording.

Native Codex and imported source execute only in the VM. The host validates and
retains outputs without extracting or executing guest archives. The runner
enforces overall time, inference and artifact bounds and records cleanup. The
native staging area uses `/var/tmp/native` on the guest disk, outside the task
tree. The qualified image's `/run` filesystem has only 64 MiB; retaining input
archives, transcripts and output there exhausted capacity on the repair workload.
Input transfer also uses guest disk storage. These paths disappear with VM cleanup.
The investigative rubric additionally asks for small reproductions, each limited to
30 seconds and 64 KiB of recorded output, without installs, external services or
persistent processes. These per-probe instructions are prompt policy, not an
independent command-level sandbox.

The guest can modify its copy of the input. The host rechecks its canonical
snapshot and records collected guest modifications separately. Judges cite
original evidence from the host packet and place writable clones, reproduction
scripts and output under `investigation/CASE_ID/` inside the task directory.
The packet's `source-modes.json` records captured executable flags; source copies
preserve those flags. Captures do not retain arbitrary original POSIX permissions
beyond the executable bit, so they cannot establish other mode-sensitive behavior.
Collected files are cited as `task/investigation/CASE_ID/...`; review judges use
`review` as the case identifier. Guest source copies, logs, process reports and
transcripts remain untrusted evidence. Their hashes establish consistency, not
semantic authenticity or independent correctness.

### Operator state and effective context

The production runner creates fresh guest `HOME` and `CODEX_HOME` directories,
writes its own provider configuration, and launches Codex with an explicit process
environment. VM creation declares no host directory mounts; task, tooling and the
selected skill are explicit copied inputs. The host runtime also gets separate
HOME/XDG state. Task-repository instructions remain frozen, shared task context.
Codex runs through the [solver boundary](TASK-PRESENTATION.md#solver-boundary).
The controller and relay remain outside its filesystem and process view; only
the local inference endpoint is shared. A fresh home alone is not this boundary.

Before native execution, the copied context guard checks the exact initial Codex
home and empty user home, rejects ambient instructions/configuration in the known
ancestor, user and system discovery roots, and inventories task instructions.
All review, repair and judge inputs reject embedded skills and `.codex` state,
including hooks and managed configuration; they retain ordinary `AGENTS.md` and
`AGENTS.override.md`. Hooks, plugins, memories and shell snapshots are disabled in
the generated profile. Version probes use disposable homes too: even
`codex --version` can create launcher state, which must not remain in the image's
default home. The [image recipe](../../environments/python-review/README.md) owns
build preparation.

Every broker request records bounded message, top-level instruction and tool
definition identities, native instruction-source headers and advertised skill
names/paths. Inline message images have separate position/content identities;
they are not parsed as instruction prose, and gateway image validation still applies.
Unsupported observations and unexpected instruction/skill roots are
refused. Codex's auto-installed built-in skills remain declared harness defaults:
the runner retains their post-start file inventory and rejects a selected skill
name that collides with an advertised builtin. Installed bytes, advertised
metadata, successful full-body reads and semantic use remain different evidence.

Run the opt-in production-route controls without credentials or real inference:

```sh
uv run python scripts/qualify-context.py \
  --runtime NATIVE_RUNTIME --image-archive IMAGE_ARCHIVE \
  --image-sha256 IMAGE_SHA256 --catalog MODEL_CATALOG --output FRESH_OUTPUT
```

The control creates synthetic operator instructions, configuration, skills,
memories, hooks, shell initialization and prior sessions outside the copied task.
It runs OFF, ON, fresh OFF, a deliberately exposed instruction marker, and a
system-context refusal through `run_native` and the production guest runner.
It verifies intended task instructions, selected metadata/full-body exposure,
successful reads, shared base instructions and tool definitions, identical
advertised/on-disk builtins, the explicit invocation difference, fresh sessions,
and cleanup. Per-run tool-record IDs are retained in original requests but excluded
from capability comparisons. Synthetic receipts have the distinct kind
`skilleval-native-context-control`; task/judge recorders and studies reject them.

Retain the summary, original synthetic requests, native audits/artifacts and
recovery tickets. Evidence binds the actual source, runtime, image and harness
identities. The 2026-10-30 control passed all 56 assertions across five fresh VMs
on Codex 0.164.0 and smolvm 1.32.3, after removing image-build launcher residue,
and passed again with the neutral provider profile. Each generation retains its
exact configuration identity rather than granting evidence to later profiles.
Earlier failed generations remain retained. This establishes the tested context
boundary and comparison, not model behavior, general resistance to malicious
guest code, every future discovery path, or skill effectiveness. It does not
retroactively qualify earlier attempts or grant a scored-study seal; full-workload
admission still needs treatment-specific evidence and independent grader audit.

## Prepare, freeze, run and record a calibration judge

The following uses the public authored development controls. Choose new output
paths for every preparation and native invocation. Set `NATIVE_RUNTIME`,
`IMAGE_ARCHIVE`, `IMAGE_SHA256`, `MODEL_CATALOG` and `AUTH_FILE` to explicitly
selected local paths/identities; do not put auth contents in shell variables.
The image, runtime and inference route need applicable qualification evidence.

```sh
umask 077
mkdir -p .local/native-calibration
uv run skilleval capture examples/calibration/review-v1 \
  --output .local/native-calibration/suite \
  > .local/native-calibration/suite-receipt.json
uv run skilleval judge prepare-calibration .local/native-calibration/suite \
  --output .local/native-calibration/packet \
  > .local/native-calibration/preparation.json
uv run skilleval capture .local/native-calibration/packet \
  --output .local/native-calibration/input \
  > .local/native-calibration/input-receipt.json

uv run python - <<'PY'
import json
from pathlib import Path

root = Path('.local/native-calibration')
prepared = json.loads((root / 'preparation.json').read_text())
captured = json.loads((root / 'input-receipt.json').read_text())
assert prepared['input_digest'] == captured['manifest_sha256']
(root / 'prompt.txt').write_text(prepared['prompt'])
PY
INPUT_SHA256=$(uv run python -c \
  'import json; print(json.load(open(".local/native-calibration/input-receipt.json"))["manifest_sha256"])')

uv run skilleval native qualify .local/native-calibration/input \
  --sha256 "$INPUT_SHA256" --prompt .local/native-calibration/prompt.txt \
  --mode judge --judge-policy investigative \
  --runtime "$NATIVE_RUNTIME" --image-archive "$IMAGE_ARCHIVE" \
  --image-sha256 "$IMAGE_SHA256" --catalog "$MODEL_CATALOG" \
  --auth-file "$AUTH_FILE" --output .local/native-calibration/run
uv run skilleval judge record-calibration .local/native-calibration/suite \
  .local/native-calibration/packet .local/native-calibration/run \
  --output .local/native-calibration/records
```

Preparation withholds authored labels and structured history. It does not establish
independent blinding: source/report prose or prior exposure can reveal identity.
Give the native runner only the captured packet, never the original labeled suite
or this calibration reference's expected answers.

For a static variant, prepare a fresh packet with `--static`, freeze it separately,
and invoke `native qualify` with `--judge-policy static` and that preparation's
exact prompt. The case evidence is unchanged; rubric, policy metadata, packet
digest and native input digest differ. Static forbids execution in the declared
prompt policy. The native tool capability remains available, so this is a policy
comparison rather than a separately enforced no-execution runtime.

The [calibration reference](CALIBRATION.md) owns verdict semantics and both result
contracts. Native calibration records can remain partial; missing judgments and
unresolved answers retain separate denominators. Recording embeds its authored
label comparison in `audit.json`. It does not turn that agreement into independent
truth or aggregate different judges' partial answers into one complete run.

## Review a task, then judge its findings

For supported historical review captures, first use `workspace prepare` with the
retained original manifest digest. It exports source and reconstructs the native
base-to-candidate Git diff. Freeze that resulting workspace with `capture` before
passing it to `native qualify --mode review`. Preserve ordinary instructions,
tests, contracts and dependencies; oracle, historical reports, scores and treatment
assignment data stay outside the reviewer's input.

Preserve the task's original review instructions and report schema. Request the
same findings JSON as the native final response so it can be bound to the run.
The recorder requires a `findings` array with nonempty `title` and `body` strings;
locations, original priority/severity fields and an optional summary supply
additional context. Do not replace a task's numeric priority convention with an
invented severity scale. `[]` is valid report content; a missing or malformed
findings list is not a clean report. Keep the task brief fixed across arms; the
[study controller](STUDIES.md) freezes the minimal explicit-use invocation
difference. Natural-availability comparisons use identical prompts. Review mode
omits `--judge-policy`.

Once the review run has stopped and its evidence is retained, prepare the judge
against the original captured evidence and the native final report:

```sh
uv run skilleval judge prepare-review ORIGINAL_REVIEW_BUNDLE \
  REVIEW_RUN/final.txt --output FRESH_JUDGE_PACKET > judge-preparation.json
uv run skilleval capture FRESH_JUDGE_PACKET \
  --output FRESH_JUDGE_INPUT > judge-input-receipt.json
```

Extract the exact `prompt` from `judge-preparation.json` and check the input digest
as in the calibration example. Run the frozen input with `native qualify --mode
judge --judge-policy investigative` using the same explicit runtime/image/catalog
and auth selections. Then record it:

```sh
uv run skilleval judge record-review ORIGINAL_REVIEW_BUNDLE REVIEW_RUN/final.txt \
  FRESH_JUDGE_PACKET NATIVE_JUDGE_RUN --output REVIEW_JUDGE_RECORDS
```

The review packet contains original instructions/source and selected report
fields, excluding original historical reports and the oracle. Every native
finding must receive exactly one supported, unsupported, duplicate or unresolved
judgment. Novel valid findings do not need an earlier reference. Resolved findings
require original case citations; reproduction artifacts are separately checked
supplements. Missing tools or contracts can leave a judgment unresolved.

Keep the review's `native-run.json` and artifact files beside its `final.txt`.
Preparation accepts its execution status only after checking the final bytes
against collected, hash-bound artifacts. Editing the report invalidates that
binding. A manually supplied report without native execution evidence has unknown
execution status. Failed review execution, judge execution and finding quality
stay separate even when useful report content survives a failure.

## Skill availability and consultation

Capture one explicitly selected skill tree with its source and revision. Supply
the actual root notice when applicable; omission is recorded as not provided,
without inventing a license or permission claim:

```sh
uv run skilleval treatment capture SELECTED_SKILL_TREE \
  --source-url UPSTREAM_URL --source-revision PINNED_REVISION \
  --license ROOT_NOTICE_FILE --output FRESH_SKILL_BUNDLE
```

The capture preserves all files in that tree, including references, scripts and
existing notices, plus executable flags. It does not install a host skill or run
its scripts. A supplied root notice is added at `.source-notices/ROOT_LICENSE`,
with its location recorded in host metadata. Older captures without that metadata
keep their original `.skilleval-notices/ROOT_LICENSE` bytes and location. Do not
rename pinned inputs in place. For the available review input, supply `--skill-bundle` and
`--skill-sha256` together to `native qualify`. Omit both for the absent selected
skill. Judge mode rejects skill bundles. CLI help owns the exact invocation.

The runner installs the selected skill only into the fresh guest
`CODEX_HOME/skills/<name>`. Ordinary task instructions remain intact; baseline
checks reject embedded skill and Codex configuration paths. The Python
`treatments.prepare_pair` operation prepares equal task/prompt bytes and separate
absent/available Codex homes for natural-availability controls. The separate
[study controller](STUDIES.md) defaults new plans to explicit native skill
invocation and freezes each arm's prompt. Neither workflow admits scored work
or guarantees successful skill application.

Distinguish these observations:

| Observation | What it establishes |
| --- | --- |
| Captured/installed tree | The selected skill bytes were available at the declared path |
| Matching native request metadata | The actual request exposed the selected skill's name, description and path |
| Complete body in a native request or successful read output | The pinned skill content was exposed through that observed surface |
| Semantic application or better task outcome | Requires separate substantive evidence |

Detectors deliberately recognize a narrow set of successful reads and complete
known content. Failed commands, truncated reads, self-reports, unrecognized tools,
missing telemetry and absent matches remain unknown or unmeasured. They do not
prove that consultation never happened. Keep nonconsulting runs visible in later
analysis; availability and observed consultation answer different questions.

## Results and independent audit

Inspect the bounded `native-run.json` together with its hash-bound
`native-audit.json`, `events.jsonl`, `final.txt`, retained prompt and artifact
inventory. The process record includes complete context inventories and is read
up to the smaller of the run's artifact budget and the 32 MiB full-audit ceiling.
Overall audit and compact-receipt limits still apply; overflow remains partial
evidence, not successful recording. Complete command outputs stay in the raw events/archive; compact
metadata exposes hashes, counts and explicit sample omissions. Completion requires the observed process, native
turn, provider requests, solver setup/namespace teardown and cleanup to agree;
it leaves task quality unassessed. The trusted boundary receipt records identity,
privilege removal and timings. Agent time starts after namespace setup and ends
when native PID 1 is reaped; launcher time is retained separately. Collection
does not proceed when namespace termination is unconfirmed.
A timeout, partial collection or failed execution is not an empty clean report.
Missing/invalid final JSON is refused by judge recording rather than filled in.

Recording verifies the original frozen input, packet bytes/digests, exact prompt,
native model and declared policy, report/final bindings, artifact hashes, the referenced full audit and
original versus artifact citations. Originals must still be present for those
checks. An original citation may use the canonical packet path or a single
`task/` prefix whose collected bytes exactly match that allowed host original;
the recorder preserves the supplied path. Investigation artifacts remain a
separate citation class. A new result ID creates a fresh directory; explicit
supersession retains earlier generations. Calibration records also retain authored labels outside the
judge's input; review records retain the reviewed native run.

Each audit starts with `independent_audit: required-pending`. A human independent
of the model judgment must inspect original evidence, verdict reasoning and probe
limits before a scored correctness claim. The recorder validates structure and
identity and cannot supply this audit. Native model output cannot name itself a
human assessor. Label agreement, hash consistency or another model pass does not
establish independent human review, general judge reliability, or a skill benefit.
