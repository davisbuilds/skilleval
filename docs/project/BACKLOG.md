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

- **Judge reliability and label audit:** independently audit authored labels,
  native verdicts and disagreements. The human audit remains pending; agreement
  with authored labels does not establish general reliability. Check
  injection, anchoring, position effects, treatment leakage, and repeat consistency.
  Include cross-model or human checks before relying on judgments from the same
  model family as the evaluated agents.
- **General review coverage:** extend targeted known-defect probes to an auditable
  missed/novel-defect family contract for real reports. Explicit coverage hypotheses
  do not measure unaided discovery or exhaustive recall. Define duplicate handling
  and severity policy before aggregate scores.
- **Study orchestration:** immutable expanded run matrix, checked environment
  admission at the actual dispatch boundary, interleaving, budget/timeout
  enforcement, retries as new attempts, and paired report. The current seal
  covers bounded controls. Incorporate retained full-workload and treatment
  evidence into an explicit dispatch scope before any scored campaign.
- **Assessment selection:** choose one declared assessment policy per analysis;
  explicitly handle branching revisions and disagreement. Storage preserves all
  records and does not select a winner.
- **Request exposure telemetry:** a native development run observed a complete
  successful read of the pinned skill, but the request-content detector matched
  neither catalog metadata nor body. Investigate the actual native serialization
  with a controlled public skill before expanding that detector. Preserve this
  as unmeasured request exposure; failed matches do not prove absence.
- **Recovery supervision:** the controller-interruption controls establish
  explicit same-state recovery, not automatic recovery after host loss. Add
  controller ownership/concurrency protection and supervision when study
  orchestration needs them. Real runtime stop/delete failures and host reboot
  remain outside the live controls; local fault-injection tests cover the
  refusal/retry contract.
- **Qualification packaging:** the opt-in controls, native runner and admission
  currently require the source checkout and share private package helpers.
  Stabilize those interfaces when supporting installed-wheel qualification,
  preserving independent outcome checks and producer/admission agreement tests.
- **OCI portability:** attest media types/effective image policy before permitting
  registries other than explicit Docker Hub references in the pinned adapter.
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
