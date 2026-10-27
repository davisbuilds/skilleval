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

- **Recovery supervision:** the controller-interruption controls establish
  explicit same-state recovery, not automatic recovery after host loss. Add
  controller ownership/concurrency protection and supervision when study
  orchestration needs them. Real runtime stop/delete failures and host reboot
  remain outside the live controls; local fault-injection tests cover the
  refusal/retry contract.
- **Native workload compatibility:** extend the successful bounded provider/tool
  control to longer real reviews and grader tasks, including reasoning/history
  forms not exercised by the small control. Preserve strict capability checks,
  actual version/catalog identity, failures, and native event/exit evidence.
- **Qualification packaging:** the opt-in controls and admission currently
  require the source checkout. Move shared invocation policies into stable
  package interfaces when adding the longer runner, preserving independent
  outcome checks and producer/admission agreement tests. Installed-wheel
  qualification is not yet a supported route.
- **Skill packaging and observation:** preserve ordinary task instructions while
  varying only skill availability, bind skill bytes, and observe actual discovery
  and consultation. Reconcile native events with process outcomes and documented
  usage semantics before matched trials.
- **OCI portability:** attest media types/effective image policy before permitting
  registries other than explicit Docker Hub references in the pinned adapter.
- **Native judge and label audit:** run calibration through the declared native
  route and independently audit authored labels and disagreements. One external
  development judge's agreement does not establish general reliability. Check
  injection, anchoring, position effects, treatment leakage, and repeat consistency.
- **General review coverage:** extend targeted known-defect probes to an auditable
  missed/novel-defect family contract for real reports. Explicit coverage hypotheses
  do not measure unaided discovery or exhaustive recall. Define duplicate handling
  and severity policy before aggregate scores.
- **Assessment selection:** choose one declared assessment policy per analysis;
  explicitly handle branching revisions and disagreement. Storage preserves all
  records and does not select a winner.
- **Study orchestration:** immutable expanded run matrix, checked environment
  admission at the actual dispatch boundary, interleaving, budget/timeout
  enforcement, retries as new attempts, and paired report. The current seal
  covers bounded controls; longer review budgets and skill packaging need fresh
  controls before that scope expands.
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
