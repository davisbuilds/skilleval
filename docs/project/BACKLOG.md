# Backlog

Ordered by the next evidence gap. Phase dependencies are in [ROADMAP.md](ROADMAP.md).

## Conventions

Entries state the gap and why it matters, with a useful next step or revisit
trigger. Date/source volatile claims or label them unmeasured hypotheses. Fix
small blocking issues inline; retain only durable unresolved work here. Agents
can work directly from an entry. Use an issue when persistent discussion or
coordination helps, keeping one detailed owner rather than parallel checklists.
Remove resolved concerns; roadmap owns selected direction and Git owns routine
shipped history. Private provenance stays outside public entries and issues.

## Open

- **Live environment qualification:** build a pinned dependency-complete image;
  exercise actual smolvm lifecycle and host/network controls; compare Colima only
  if needed. Current process fixtures do not test effective isolation.
- **Git fidelity:** reconstruct captured base/candidate history and verify the
  review diff with native Git. An imported source folder and `git --version` do
  not establish this.
- **Runtime recovery:** operator recovery from failed stop/delete or interrupted
  cleanup; retain machine/state identity. No automatic resume or admission.
- **OCI portability:** attest media types/effective image policy before permitting
  registries other than explicit Docker Hub references in the pinned adapter.
- **Native Codex execution:** credential route, tool-loop capture, treatment
  packaging, contamination controls, event/exit reconciliation, version receipts.
  Current code parses captured JSONL only.
- **Grader calibration:** independent labels and executable reproductions;
  semantic judge calls, configuration identity, prompt-injection controls,
  disagreement routing, and human audit. Stored citations alone establish no truth.
- **Coverage judgments:** represent missed known defects and novel defect families,
  not only per-reported-finding verdicts. Define duplicates and severity weighting
  before aggregate scores.
- **Assessment selection:** choose one declared assessment policy per analysis;
  explicitly handle branching revisions and disagreement. Storage preserves all
  records and does not select a winner.
- **Study orchestration:** immutable expanded run matrix, admission checks,
  interleaving, budget/timeout enforcement, retries as new attempts, paired report.
- **General task packages:** native inputs beyond the historical review exporter;
  protected holdouts, exposure tracking, and public/private packaging boundaries.
- **Analysis:** prespecified estimands, clustered uncertainty, missing-data policy,
  token/cost semantics, repeated-run variance, representative task sampling.
- **Portability checks:** run filesystem/process tests on Linux in addition to the
  initial macOS validation. Synthetic subprocess tests cannot establish real VM
  compatibility on either platform.
- **Public GitHub launch:** public hosting is intended. Before publication, choose
  a license, review the complete initial tree/history and fixture provenance, and
  verify upstream notices for any actual code adoption. Set up hosted CI calling
  `scripts/check`, pin actions/toolchains, add workflow audit and secret scanning,
  and align dependency updates with the existing quarantine. Configure the
  intended merge/rebase policy and checks against live hosting capabilities. No
  private runners, credentials, or datasets in public/fork-triggered workflows.
- **Selective adoption:** use [SOURCES.md](SOURCES.md) to choose a component when
  its phase needs it. Pin and inspect that revision's license/NOTICE and tests
  before copying, adapting, or declaring it as a dependency. A research influence
  is not an adopted implementation.
- **Later:** pi support, repair/implementation task adapters, TypeScript review UI.
