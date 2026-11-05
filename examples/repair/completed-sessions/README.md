# Completed Codex sessions: authored observation fixture

This exposed development fixture exercises AgentMonitor's production discovery,
import, browser sync, project repair and session-total readers. It is based on
the public baseline [`5f681e2`](https://github.com/davisbuilds/agentmonitor/tree/5f681e21c8af7766bb21efe60d15bae33c4ac68b)
and reference repair [`f700961`](https://github.com/davisbuilds/agentmonitor/commit/f7009616a272eba3ff96bcb851457cc2b8e4b66c).
The adapter and session records here are newly authored; no upstream test or
repair implementation is copied into them. The [development validation record](environment.md)
tracks observed coverage, exceptions and corrected preparation defects.

## Validate the application home

The shared brief requires copying the supplied synthetic `CODEX_HOME` outside
source before application commands. [The VM-only validator](environment/validate.py)
now uses that same setup for every ordinary lane and retains fixture-preservation
observations. Its separate `application-home` lane compares the copy with the old
source-tree placement using real application imports and Codex initialization.

Stage the trusted validator outside task source, or freeze its exact bytes in a
task-package lane's `python3 -I -c` argument. Inside the credential-free
`solver-isolated-v1` VM, the standalone invocation is:

```sh
python3 -I /trusted/validate.py application-home \
  --source /workspace/task --dependencies /workspace/node_modules
```

This is a guest command, not a host setup command. Retain its complete JSON and raw
logs through task-package validation. The existing `checks`, `root-tests` and
`frontend-tests` lanes use the same source/dependency arguments. Freeze expected
observations separately; do not automatically accept whatever a diagnostic emits.
The [environment record](environment.md#disposable-application-home) describes the
positive control, suppressed account RPCs, known ordinary failure, and coverage
limits. A matching package does not grant scored admission.

## Prepare portable source and skill captures

`prepare.py` reads Git objects from explicitly supplied local repositories. It
never checks out, imports, builds or runs AgentMonitor or the upstream skill. Run
it from the skilleval checkout with the locked Python environment:

```sh
uv run --locked python examples/repair/completed-sessions/prepare_fixture.py \
  /absolute/external/fixtures
uv run --locked python examples/repair/completed-sessions/prepare.py \
  --source-repo /absolute/path/to/agentmonitor \
  --skill-repo /absolute/path/to/mattpocock-skills \
  --fixtures /absolute/external/fixtures \
  --output /absolute/external/completed-sessions-inputs
```

Check `prepare_fixture.py --help` for its compression prerequisite. Both outputs
must be fresh, and their parent directories must already exist. The preparation
output must remain separate from all supplied input directories. Review supplied
fixture content before capture: the helper checks regular-file structure and
embedded configuration, not whether arbitrary transcript prose contains personal
information, instructions or answers. Use the authored neutral fixtures, never
copied personal transcripts.

The helper pins AgentMonitor baseline `5f681e21c8af7766bb21efe60d15bae33c4ac68b`,
reference `f7009616a272eba3ff96bcb851457cc2b8e4b66c`, and Matt Pocock's skill
revision `a496f5513d4a4cf889bc48e189e579f4b0bdf6aa`. It produces:

- `baseline-source/` and `reference-source/`: portable ordinary source plus the
  same supplied fixtures under `sample-history/`.
- `baseline-capture/` and `reference-capture/`: immutable-file evidence snapshots
  with manifests binding every retained file, byte and executable flag.
- `skill-capture/`: the untouched `diagnosing-bugs/SKILL.md`,
  `agents/openai.yaml`, and `scripts/hitl-loop.template.sh`, plus the upstream
  root MIT notice at `.source-notices/ROOT_LICENSE`.
- `provenance.json`: external source revisions, capture digests, fixture hashes,
  instruction-link adaptations, synthetic Git identity and object-access controls.
  The helper also prints its SHA-256; retain that digest separately.

The baseline retains all **620 tracked files**, including ordinary guidance,
existing tests, archived documentation, frontend, assets, hook source, package
manifests and lockfile. Each `CLAUDE.md` symlink becomes a regular copy of its own
adjacent `AGENTS.md`: the capture format rejects links. This preserves instruction
content but changes the original link mode and bytes, recorded in provenance.

The ordinary baseline `docs/project/BACKLOG.md` already identifies compressed
rollout discovery as the cause and suggests Node zstd and stable identity. Keep
that guidance intact in both arms. This is an exposed repair task with existing
diagnostic guidance, not a concealed-cause debugging test. Ordinary product
benchmark/evaluation references also remain intact.

Fresh Git metadata provides a clean `main` baseline commit and an explicit index
of every source and fixture file. Direct index plumbing includes tracked archives
despite ignore rules and bypasses imported attributes, filters and hooks. No
upstream Git metadata, configuration, refs, reflogs, alternates or object packs are
copied. The reference receives the same baseline Git view with repaired source
overlaid as working-tree changes; its Git store contains no reference commit or
distinctive repair blobs. Positive baseline-blob reads and exclusion checks are
recorded externally for both views. Full historical bisection is unavailable.

Transfer only the selected ordinary source payload and `prompt.txt` to a solver;
install the captured skill only for explicit ON. Keep this README, provenance,
captures' manifests, reference tree, task/check specifications, expected values,
worker, staging archives and controller outside its readable namespace. The
`reference-source/` directory is exclusively an external polarity control. The
helper prepares evidence; it launches no VM/model and supplies no runtime,
isolation, checker-validity or skill-effect qualification.

Qualify the ordinary locked Node 24.22.1/pnpm 11.28.6 workspace, native SQLite and
Node zstd in the guest. Preserve separate validation lanes for lint, build, root
tests, frontend checks/tests, Chromium E2E and built verification. Build requires
the frontend workspace even for this backend repair. Git, Bash, Python, curl and
jq support ordinary test/hook tooling. Optional Dojo smoke remains documented and
unsupported unless separately supplied; do not copy an operator's sibling
checkout. Both arms need the same disposable application home, database,
session roots and empty application skill catalog, separate from solver harness
configuration. Keep failures, skips and unsupported lanes explicit.

The ordinary solver brief requires copying the supplied synthetic application
home outside the source tree before application commands. Codex quota polling can
create support databases in `CODEX_HOME`, even when the application's main database
is elsewhere. Use the copy as writable application context and preserve the source
fixtures. Earlier frozen briefs pointed directly at the source fixture; preserve
those generations and their context limitation rather than applying the new brief
retroactively. The [validation record](environment.md) distinguishes them.

## Observation adapter

After inspecting and retaining the capture's provenance, freeze the artifact task:

```sh
uv run --locked python examples/repair/completed-sessions/prepare_task.py \
  --input /absolute/external/completed-sessions-inputs/baseline-capture \
  --sha256 BASELINE_MANIFEST_DIGEST --output /absolute/external/task.json
```

This combines the shared brief, six request/expectation pairs, worker digest and
`node-v1` observer resource profile. Editable directories include source, tests,
scripts, docs and frontend source/tests, plus the root README. Ordinary build
outputs under `dist` and `frontend/dist` are also permitted and retained in full
in the candidate and patch; generated output does not prove a source repair.
Alternative helper and regression-test filenames are permitted. Source fixtures
and dependency manifests remain fixed for this slice. Preparation executes no imported code and
does not qualify the task or grader.

Preparation creates an exclusive adjacent `task.worker.py` copy and binds its
path and digest, so subsequent example-worker edits cannot change an already
prepared task. Choose a fresh output filename for every generation. Preparation
requires an existing output parent, refuses existing task or worker files, and
checks both destinations stay outside the input capture before writing. Keep the
frozen worker beside its task. Preparation rolls back only its newly created
worker after failure, permitting a corrected retry at the same output name.
It preserves the original preparation error if cleanup also fails.

`requests.json` and `expected.json` are paired by `probe_id`. Put each request
and expected observation into a repair specification's corresponding probe.
The worker alone is the executable payload. It embeds its Node adapter, so no
adjacent checker files are required in the guest. Expected observations remain
on the host. See [the repair contract](../../../docs/system/REPAIRS.md) for the
protocol and isolation boundary.

The direct reader checks decoded bytes against the bytes authored from the same
transported request, and verifies the reader's hash describes those bytes.
JSON property ordering in the request transport cannot change the expected
observation. Independent database observations still verify messages and usage.

The six probes cover:

- First observation of a completed compressed rollout.
- A plain rollout replaced by a compressed version containing its final two
  messages and final request usage; earlier message rows and event identities
  remain intact.
- Coexisting forms with a stale compressed copy and a current plain file;
  discovery chooses one current transcript, then survives removal of the plain
  form after compression catches up.
- An ordinary plain file that grows by appending the same final records.
- A compressed legacy transcript without metadata: its filename supplies a
  stable fallback identity, including names that contain no UUID.
- Invalid compressed bytes: a reader raises and browser sync reports an error,
  without creating a session or usage rows. The valid compressed probe supplies
  the corresponding positive control. No new global skip/error policy is imposed
  on import or project repair.

The first four probes independently read exact message text, roles, timestamps,
ordinals, usage tokens, cached input, model and response identity from a temporary
SQLite database. Session-total checks compare imported cache-inclusive usage with
the thread total. Repeated normal and forced cycles inspect both values and row
identity. Deliberately stale project names exercise the actual preview, apply and
repeat repair paths. Source and fixtures stay read-only; all generated rollout
files, databases and adapters live in private temporary directories.

Only run `worker.py` against imported AgentMonitor source inside the authorized
check VM. The VM needs Node 24 with zstd, tsx, and the frozen repository's native
SQLite dependency available through ordinary module resolution. The worker uses
Node on `PATH` and `SOURCE/node_modules/tsx/dist/loader.mjs`, falling back to the
qualified image's `/usr/local/share/task-dependencies/node_modules/tsx/dist/loader.mjs`;
explicit
`SKILLEVAL_NODE` and `SKILLEVAL_TSX_IMPORT` environment overrides support a qualified
image layout. Qualification must exercise these paths under the real worker
identity. A missing tool, import failure or timeout is a check execution failure,
not a negative outcome.

Host tests validate the authored records, expected contracts and Python transport
with a tiny authored executable. They never import or execute AgentMonitor.
Passing host tests does not establish VM execution, checker polarity, exhaustive
correctness, independent grader validity, representative task difficulty or a
skill effect. Before freezing a task, retain fresh baseline, reference and
plausible incomplete-repair observations in the actual VM. Useful incomplete
controls fix discovery without readers, fix the browser while leaving import
unchanged, or retain the compressed suffix in browser session keys.

## Ordinary environment lanes

Build the [Node image](../../../environments/node-debugging/README.md) from the
captured manifests and lockfile. Bind it in a `solver-isolated-v1` task package.
For each baseline/reference capture, use four JSON lanes:

| Lane | Guest command payload |
| --- | --- |
| Toolchain | `python3 -I -c TOOLCHAIN_SCRIPT` using `environment/toolchain.py` bytes |
| Checks | `python3 -I -c VALIDATION_SCRIPT checks` |
| Build and root tests | `python3 -I -c VALIDATION_SCRIPT root-tests` |
| Frontend tests | `python3 -I -c VALIDATION_SCRIPT frontend-tests` |

`VALIDATION_SCRIPT` is the exact content of `environment/validate.py`, embedded
in the package argv. Keep these wrappers outside captured task source. The
ordinary lanes run unchanged pnpm scripts from a disposable full source copy,
including synthetic Git. Writable local dependency links point at the same
read-only installed packages. Builds precede root tests so built-CLI tests do not
silently skip. Generated build files stay in the disposable copy; the wrapper
records their paths/counts/sizes and any changes to existing source.

Use 600-second lane deadlines. Raw command logs go to stderr; stable observations
go to stdout. Node TAP and Vitest counts must reconcile with test identities.
Empty suites, unsupported output, truncation and source mutation remain failures.
Retain a diagnostic generation, inspect failures/skips, then freeze expectations
and run a fresh validation generation. Matching a known failure does not make a
suite clean. Preserve Chromium E2E, separate built/verify/parity lanes and
unsupported platform checks as explicit coverage gaps.

The toolchain lane tests real SQLite/zstd/tsx use, read-only dependency aliases
and writable task storage. The image disables pnpm's automatic pre-run install
because the task uses preinstalled frozen dependencies in an adapted layout;
this policy applies to nested scripts too. Offline observations use a separate
UID and an explicit dependency projection after VM workspace preparation.
The Node observer allows 2 GiB address space, 8 MiB files and 256 descriptors,
while retained stdout/stderr remain capped at 128 KiB each. These limits are
distinct from the ordinary test lanes and do not change legacy Python observers.
