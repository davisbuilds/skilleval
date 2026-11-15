# Pinned source capture

Source capture prepares repository context as data. It reads a full Git commit
object ID, optionally applies an explicit exclusion policy, and writes a fresh
evidence generation. It does not run imported source, setup scripts or tests,
launch VMs, or qualify the resulting task.

```sh
uv run skilleval schema source-exclusions
uv run skilleval validate source-exclusions exclusions.json
uv run skilleval source capture /path/to/source --revision FULL_COMMIT_SHA \
  --exclusions exclusions.json --output /path/to/fresh-capture
```

Omit `--exclusions` to retain the selected revision's ordinary files. The source
working tree can differ from the selected revision; capture reads the revision.
Generic `skilleval capture` remains the explicit evidence-tree copier and does
not perform Git source selection.

## Context selection

An exclusion is a declared context intervention. Keep ordinary repository
instructions, implementation and tests unless separately declared. For example,
an operator can select a task's retained source while removing historical
specification directories from the solver context:

```json
{
  "schema_version": 1,
  "exclude": [
    {
      "path": "docs/specs",
      "reason": "Remove historical specification context from both comparison arms"
    },
    {
      "path": "docs/archive/specs",
      "reason": "Remove archived specification exemplars from both comparison arms"
    }
  ]
}
```

Selectors are exact repository-relative POSIX file paths or directory subtrees,
without globs. Reasons must be nonempty. Duplicated, overlapping and unmatched
selectors are rejected; a renamed or missing directory requires an explicit
policy revision. Excluding `AGENTS.md` or `CLAUDE.md` is rejected so ordinary
agent instructions remain available. `validate source-exclusions` checks
declaration structure; capture resolves selectors against the named revision. Retained nonempty files
with bytes identical to excluded content also cause rejection, so a second path
cannot silently preserve that same excluded content. Empty-file blob aliases
are disclosed in provenance because identical empty bytes carry no content.

Freeze the same selected baseline before preparing both skill OFF and skill ON
arms. Apply the selection symmetrically; the skill package remains the treatment.
Retain the original source evidence and full exclusion policy separately so the
intervention stays inspectable. Removing a template, plan or specification does
not prove that the model lacks learned or earlier template familiarity. Record
the intervention and its limits when interpreting results.

## Solver baseline and operator provenance

The output contains an ordinary evidence snapshot at `baseline/` and an
operator-only `provenance.json` beside it. The receipt pins the provenance
SHA-256, source revision, excluded file count and baseline manifest identity.
Retain these digests separately. The baseline can be verified with the existing
`verify` command and used as captured input by the artifact workflow.

The baseline includes retained file bytes and one synthetic Git commit. Original
Git history, remotes and configuration do not enter it. Provenance records the
complete policy, excluded Git entry identities, supported link adaptations and
Git object controls. Keep provenance and exclusion-policy files outside all
solver captures; only the baseline is task context.

The supported instruction-link adaptation is `CLAUDE.md` pointing to its adjacent
`AGENTS.md`. Capture materializes the instruction bytes as a regular file and
records the adaptation. Other source symlinks are rejected. This preserves
ordinary agent guidance while remaining compatible with regular-file evidence
snapshots.

This initial exporter supports SHA-1 commit IDs, regular Git files and the
instruction links above. Submodules, case-aliased paths, `.git`/`.codex` source
components and filenames outside ASCII letters, digits, `_./@+-` are rejected.
The existing evidence file-count and byte limits still apply. There is no
automatic ignore-rule filtering, text rewriting or archive-content inspection.

Continue with [artifact and task-package preparation](TASKS.md), independent
acceptance mapping, and baseline/reference validation through the intended VM
solver boundary. A successful source capture establishes selected input bytes
and provenance; packaged-task validity, environment qualification, task quality
and skill effectiveness remain separate questions.
