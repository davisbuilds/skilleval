# Storage and retention

Run data outlives source branches. An evaluation attempt creates a fresh output
directory and VM; it does not create a Git worktree. Worktrees are implementation
checkouts and can be removed after their changes land and their evidence remains
readable.

## Durable locations

```sh
uv run skilleval storage init
experiment_dir="$(uv run skilleval storage path experiments debugging-v1 --plain)"
mkdir "$experiment_dir"
# Pass fresh child paths such as "$experiment_dir/study" to --output.
```

Root precedence is `--root`, then `SKILLEVAL_DATA_HOME`, then the platform default:
`~/Library/Application Support/skilleval` on macOS or
`${XDG_DATA_HOME:-~/.local/share}/skilleval` elsewhere. Overrides must be absolute.
Windows is unsupported. Initialization refuses Git checkouts, unrelated nonempty
directories, and symlink ancestors. The root and its groups are private (0700).
Repeated initialization preserves existing artifacts.

| Group | Contents |
| --- | --- |
| `experiments` | Studies, all attempts, native runs, checks and selected/unselected judgments |
| `captures` | Immutable task/skill snapshots and original source material |
| `images` | Canonical pinned images shared by runs |
| `tools` | Pinned runtime distributions and model catalogs |
| `audits` | Human-review packets, inventories and maintenance receipts |

`storage path GROUP [NAME]` returns JSON; `--plain` prints only the path. It does
not create or reserve an output, launch a runtime, or change existing `--output`
semantics. Choose a fresh experiment name for a new generation. Keep full paths
quoted in shell commands. Record outputs stay immutable; retries get new paths.

Initialization does not move old data or configure backup. These files can contain
private source and transcripts. Backup scope, access and restore verification
need an explicit operator policy; a stable directory alone provides no backup.

## Native rollouts

Future native runs request a persistent thread inside a fresh guest `CODEX_HOME`.
They do not use the operator's `~/.codex/sessions`, configuration or history.
After namespace teardown, the collector retains guest
`codex-home/sessions/**/*.jsonl` files in `artifacts.tar`; the VM home is then
removed. Raw app-server input/RPC, derived events, final text, usage and cleanup
receipts remain separate evidence. Native JSONL is Codex's selected persistence
representation and can truncate persisted command output (64 KiB in the pinned
source); it is not a lossless copy of inference traffic.

The native audit records the capture policy, matching thread/turn identity,
member hashes and byte sizes, parse status and reasoning coverage. A completed
run requires a matched rollout with terminal turn evidence. Partial or malformed
capture remains retained and cannot silently count as a completed capture.

`model_reasoning_summary = "auto"` requests summaries from the pinned model.
Coverage reports distinguish readable summary text, readable reasoning content,
and opaque encrypted fields. Requested summaries are not guaranteed; absence is
recorded. These are exposed summaries and observable actions, not a complete
record of private internal reasoning. A later judge can compare retained traces,
but that requires its own rubric, blinding policy and validation.

Historical ephemeral runs keep their original RPC/events and cannot acquire
missing native rollouts retroactively. Do not compare capture-policy generations
as if their available evidence were identical. Requesting summaries also changes
the inference configuration: use the same capture/summary policy in both arms of
a new comparison, and establish comparability before pooling older runs.

The opt-in `scripts/qualify-rollout.py` uses the production native route to check
persistence, identity, actual request settings and cleanup. Its default synthetic
response includes a readable-summary positive control; `--auth-file` selects a
bounded real-provider run and reports actual summary availability. Neither is a
skill-effect experiment or scored admission.

Rollouts currently remain with the run until an explicit archival/deletion policy
is adopted. There is no automatic age-based expiry, including for failed runs or
pending audits. The cleanup commands below never delete them.

## Disposable staging

`storage prune RUN...` retains its existing image-only behavior. Native/offline
cleanup already removes a verified per-run image copy after qualified recovery;
the canonical source image remains. See [environment retention](ENVIRONMENT.md#image-retention).

```sh
uv run skilleval storage prune-staging "$experiment_dir/check" "$experiment_dir/native"
uv run skilleval storage prune-staging "$experiment_dir/check" "$experiment_dir/native" --apply
```

The default is a read-only preview. `--apply` rechecks recovery and producer
identity, hashes, filesystem types and stability before deletion, and writes a
separate `staging-retention.json` receipt. It accepts explicit run directories,
not recursive roots or age thresholds. Unsupported, active, unverified or changed
files are retained with a reason; a mixed result reports partial success.

Only these `runtime-state/` copies are eligible:

- `checker-input.tar`, after a bound repair-check producer and recovered VM.
- `validation-input.tar`, after a bound task-validation case and recovered VM.
- `tooling.tar`, after a bound native-run audit and recovered VM.

Qualification tooling remains required by admission and is never eligible.
Canonical captures/images, `artifacts.tar`, RPC/events, native rollouts, candidate
patches, reports, recovery tickets and all judgments remain. Unknown files are
not candidates. A later successful recovery can qualify staging from a failed
run without rewriting that run's original report.

Measure allocated bytes as well as apparent file lengths. Sparse VM templates
can have large logical capacities with small disk allocation; copying them with
an unsuitable tool can inflate usage. Neither capacity nor summed APFS file
allocation establishes unique physical bytes freed.

## Retiring old implementation worktrees

Frozen records may embed absolute paths into other runs, captures or worker
files. Readers deliberately reject symlink ancestors. Moving `.local` and adding
a symlink can therefore break replay even when ordinary file reads succeed.

For existing data, preserve the real evidence directory at its original path.
Before removing a merged source checkout, inventory its dirty, untracked and
ignored contents; verify there are no active consumers; preserve every evidence
tree and external file referenced by frozen inputs. Retain nested Git histories
inside captures. A source checkout's editable virtual environment is disposable;
a captured task's Git history is not.

Remove only merged, inactive source worktrees. Restore preserved evidence to the
same real paths and verify original hashes plus actual task/assessment/report
readers. Keep a maintenance inventory outside the retiring checkout. The remaining
directory is a legacy evidence location, not a Git worktree. Do not treat it as
an orphan and delete it during later source cleanup. General legacy relocation
requires a supported locator migration; it is not implemented by `storage init`.
