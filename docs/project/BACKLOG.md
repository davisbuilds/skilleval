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

- **Skill-relevant task population:** define the work and plausible contribution
  of selected independently supplied skills, then choose real task candidates
  with independent quality criteria. Current review exports and tiny authored
  controls do not cover that population. Pilot for ceiling effects and feasibility,
  retain selection decisions, and freeze separate evaluation cases; do not retain
  only vanilla failures or observed skill wins. Add required task/grader adapters
  as the selected work demands them.
- **Explicit-use treatment:** the controller currently compares absent versus
  naturally available skills. Implement a separately identified explicit-use ON
  treatment, freezing the minimal invocation difference, full package and common
  task/environment inputs. Keep existing runs unchanged and consultation failures
  visible. Upstream-versus-adaptation comparisons are outside initial scope.
- **Quality and efficiency accounting:** qualify reported usage semantics before
  summing turns or applying rates; capture independent observations where available.
  Report input/cached/output usage without double counting, missing fields as
  unknown, and observed charges separately from dated rate-based estimates.
  Include skill overhead and failed/retried attempts under the declared policy;
  keep judge/evaluation spend separate. Separate agent time from setup/cleanup,
  retain cache behavior, and report paired quality/cost/time tradeoffs with
  uncertainty. Current study output retains usage and whole-attempt elapsed time
  but does not implement these accounting or analysis contracts.
- **Judge reliability and label audit:** independently audit authored labels,
  native verdicts and disagreements. The human audit remains pending; agreement
  with authored labels does not establish general reliability. The separate
  reliability suite now checks repeat/probe-order stability and within-packet
  paraphrase, anchor, injection and treatment-cue invariance. Expand to isolated
  perturbation sessions and representative cases; shared-packet controls cannot
  isolate contamination or establish general injection resistance.
  Include cross-model or human checks before relying on judgments from the same
  model family as the evaluated agents.
- **General review coverage:** extend targeted known-defect probes to an auditable
  missed/novel-defect family contract for real reports. Explicit coverage hypotheses
  do not measure unaided discovery or exhaustive recall. Define duplicate handling
  and severity policy before aggregate scores.
- **Scored study admission:** the development controller now freezes randomized
  paired matrices, checks optional bounded admission before each dispatch, enforces
  per-attempt budgets, retains retries and reports first attempts. Extend the seal
  to retained full-workload/treatment evidence and independent grader qualification
  before implementing scored dispatch. The existing bounded seal cannot grant it.
- **Assessment selection:** choose one declared assessment policy per analysis;
  explicitly handle branching revisions and disagreement. Storage preserves all
  records and does not select a winner.
- **Catalog exposure telemetry:** fresh development evidence observed the complete
  pinned skill body in 14 of 15 broker requests and a successful native read.
  Catalog matching remains unmeasured: the detector requires the exact name,
  description and full installed path in one decoded string. Component counts
  cannot establish that presentation. Capture value-free locator diagnostics with
  a public skill before supporting aliases or broadening the detector. Preserve
  the earlier all-unmatched run as unresolved historical evidence; the new positive
  body control does not explain or repair it. Failed matches do not prove absence.
- **Recovery supervision:** the controller-interruption controls establish
  explicit same-state recovery, not automatic recovery after host loss. Study
  outputs now have a local controller lock, durable attempt publication and
  explicit reconciliation. Cross-study coordination and host-loss supervision
  remain open. Real runtime stop/delete failures and host reboot
  remain outside the live controls; local fault-injection tests cover the
  refusal/retry contract.
- **Qualification packaging:** the opt-in controls, native runner and admission
  currently require the source checkout and share private package helpers.
  Stabilize those interfaces when supporting installed-wheel qualification,
  preserving independent outcome checks and producer/admission agreement tests.
- **OCI portability:** attest media types/effective image policy before permitting
  registries other than explicit Docker Hub references in the pinned adapter.
- **General task packages:** protected holdouts, exposure tracking, and
  public/private packaging boundaries for the selected task population.
- **Analysis:** prespecified estimands, clustered uncertainty, missing-data policy
  and repeated-run variance; use the task population and accounting contracts above.
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
- **Later:** natural-discovery comparisons, pi support, TypeScript review UI.
