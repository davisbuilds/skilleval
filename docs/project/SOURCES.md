# Evaluated sources and reuse candidates

Research baseline: **2026-10-27**. This records the projects and papers evaluated
during skilleval's design investigation. It is a dated source register, not a
claim that every upstream has been installed, run, or adopted. Recheck a pinned
revision before depending on its behavior.

The OpenBench gateway is incorporated at the exact revision recorded below.
The other concrete external contracts are the candidate smolvm CLI adapter and
native Codex interfaces. Remaining entries are design influences or reuse
candidates. Declared Python dependencies are owned by `pyproject.toml` and `uv.lock`.

## Evaluation platforms, workflows, and research

| Source | What we evaluated / may adopt | Boundary for skilleval |
| --- | --- | --- |
| [Tessl scenario evaluation](https://docs.tessl.io/improving-your-skills/evaluate-skill-quality-using-scenarios), [variant comparisons](https://docs.tessl.io/improving-your-skills/comparing-with-test-variants) | Deriving inspectable tasks from real commits/PRs; reusable fixtures; explicit skill variants; repeated outcomes and review UI | Hosted product reference. Preserve ordinary repository context and distinguish forced activation from natural discovery. No Tessl service integration is selected. |
| [SkillsBench / BenchFlow](https://github.com/benchflow-ai/skillsbench), [task authoring](https://github.com/benchflow-ai/skillsbench/blob/main/CONTRIBUTING.md), [paper v4](https://arxiv.org/html/2602.12670v4) | Task/environment/skill/oracle/verifier packaging; identical conditions for paired runs; executable outcome checks | Candidate task-package ideas, not a replacement for environment qualification. Published task selection for observable skill effects should not define skilleval's representative workload. |
| [adewale/skill-eval-harness](https://github.com/adewale/skill-eval-harness), [inspected runner](https://github.com/adewale/skill-eval-harness/blob/0cc7392473c3d8e2e7de7412239ba39229101604/skill_benchmark.py) | Paired identities, repeated cases, holdouts, missing-pair diagnostics, script oracles, semantic judgments, comparison exports | Candidate measurement/analysis logic. The inspected built-in Codex answer route uses a read-only sandbox; qualify writable repository execution separately and audit statistical assumptions before reuse. |
| [Anthropic skill-creator workflow](https://github.com/anthropics/skills/blob/dbd4588f9e1033efb41dad4bef2f7947c8993d44/skills/skill-creator/SKILL.md), [grader](https://github.com/anthropics/skills/blob/dbd4588f9e1033efb41dad4bef2f7947c8993d44/skills/skill-creator/agents/grader.md) | Matched tasks, produced artifacts, evidence-backed assertion grading, human feedback, and comparison presentation | Candidate grader/UI patterns. Its supplied-skill-path workflow is a distinct treatment from native discovery, and it does not itself establish VM isolation. Preserve missing telemetry rather than defaulting it to zero. |
| [gstack overlay benchmark](https://github.com/garrytan/gstack/blob/1f246e3505eac788abf570d1e3f552ef939bee7f/docs/OVERLAY_BENCHMARK_CONTRACT.md), [workspace controls](https://github.com/garrytan/gstack/blob/1f246e3505eac788abf570d1e3f552ef939bee7f/test/helpers/overlay-workspace.ts) | Fresh workspaces, independent executable oracles, full attempt records, and separation of validity, correctness, and comparative benefit | Candidate controls. Appended instruction overlays and narrow fixtures do not establish effects of installing a skill on representative work. Baseline saturation remains an informative outcome. |
| [SWE-Skills-Bench paper](https://arxiv.org/html/2603.15401v1) | Fixed repository states and requirement-to-test traceability for skill-relevant tasks | Methodology reference. Runnable author artifacts were not established during the investigation; do not depend on an unverified implementation or treat generated verifiers as independently correct. |
| [WebDev-Skills-Bench paper](https://arxiv.org/html/2608.23067v1), [released analysis artifacts](https://anonymous.4open.science/r/webdev-skills-bench-1C32/) | No-skill, target-skill, length-matched irrelevant-context controls, component ablations, and repeats | Experimental-design reference. Prompt injection is a different treatment from natural discovery. The inspected release did not establish complete trajectory/harness reproduction. |

The [original SkillsBench paper](https://arxiv.org/html/2602.12670v1) used Harbor;
later evaluated material uses BenchFlow. This is historical context, not a
separate selection of Harbor as skilleval's runtime. Keep paper versions, current
task repositories, and leaderboard snapshots distinct.

## Runtime and harness contracts

| Source | Current decision / possible reuse | Qualification still needed |
| --- | --- | --- |
| [smolvm](https://github.com/smol-machines/smolvm), [v1.32.3 CLI](https://github.com/smol-machines/smolvm/blob/v1.32.3/src/cli/machine.rs), [pack resolver](https://github.com/smol-machines/smolvm/blob/v1.32.3/src/data/pack_ref.rs) | Preferred runtime candidate; adapter pins this version, restricts registry execution to explicit Docker Hub OCI references, and also supports pinned local Docker-save archives | Actual task fidelity, isolation, lifecycle, transport, and recovery. Packed-image policy inheritance motivated the route restriction; subprocess fixtures are not runtime proof. |
| [smolvm credential substitution](https://github.com/smol-machines/smolvm/blob/main/docs/credential-substitution.md), [SDK restore issue #1989](https://github.com/smol-machines/smolvm/issues/1989) | Sources evaluated for credential routing and network-policy pitfalls | Confirm the chosen Codex transport/authentication route. Do not assume a credential proxy or packed restore path preserves the requested policy. |
| [Colima](https://github.com/abiosoft/colima), [configuration](https://colima.run/docs/configuration/) | Used for a dedicated no-host-mount image builder; retained as an execution alternative if smolvm fails qualification | No fallback adapter implemented. Dedicated profiles, per-attempt state, credential/network scope, and cleanup need the same effective-boundary checks. |
| [Codex noninteractive mode](https://learn.chatgpt.com/docs/non-interactive-mode), [SDKs](https://learn.chatgpt.com/docs/codex-sdk) | Captured JSONL parser plus a native guest profile with 0.164.0 as the adapter baseline; stable same-major updates may enter qualification | Live model/tool execution, event/exit reconciliation, version/catalog identity, and observed skill consultation remain qualification work. SDK availability does not determine the core language. |
| [Pi](https://github.com/earendil-works/pi), [CLI integration](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/cli-integration.md) | Later native harness through JSON/RPC interfaces | Keep Pi's native agent/resources and compare ON/OFF within Pi. Avoid silently substituting SDK defaults or confounding skill treatment with harness choice. |

## Existing project precedents

These public repositories were also inspected. Their visibility was checked
against GitHub on the research date; no private sibling checkout is required to
use skilleval.

| Source | Candidate or influence | Adoption status |
| --- | --- | --- |
| [Dojo](https://github.com/davisbuilds/dojo) | Versioned skill packaging, discovery contracts, portable references, attribution, and distinction between routing checks and task quality | Conceptual input; skill bundles remain independently versioned experimental inputs. No runtime dependency. |
| [OpenBench upstream](https://github.com/minghinmatthewlam/openbench) and [public fork](https://github.com/davisbuilds/openbench) | Task extraction/fidelity, attempt lifecycle, runtime controls, result handling, and a bounded native inference gateway | Gateway implementation incorporated from the exact revision below; task/runner machinery remains a selective reuse candidate. No whole-runner adoption or runtime dependency on another checkout. |
| [Engram](https://github.com/davisbuilds/engram), [curation](https://github.com/davisbuilds/engram/tree/main/internal/curate) | Agent semantic judgment plus deterministic admissibility checks; evidence-bound proposals; separate observation, consumption, and benefit | Architectural influence. Engram's structural validation is not a calibrated LLM-as-judge benchmark. Skilleval must establish its own grader reliability. |

Earlier private capture/accounting experiments and frozen task exports informed
evidence preservation, failure accounting, and historical import contracts. Their
raw data and detailed provenance stay outside the public source tree. Public
tests use authored examples; historical imports are development material, never
new holdouts merely because they were copied into a new project.

## Incorporated OpenBench gateway

`src/skilleval/_vendor/openbench_gateway.py` is adapted from
[`obench/sandbox_gateway.py` at commit
39fec903f1e73bd3e8e3b15cacefbcce051bd1a6](https://github.com/davisbuilds/openbench/blob/39fec903f1e73bd3e8e3b15cacefbcce051bd1a6/obench/sandbox_gateway.py).
The original upstream file's SHA-256 is
`ed02f42cc52a22cfd16d49e3d639a27ce66de0b3bc3a9f41758c646bb26aed48`.
A narrow local compatibility patch accepts empty `annotations` and `logprobs`
on replayed assistant text, preserving native request bytes while refusing
nonempty metadata and hosted capabilities. The pinned Codex wire format and a
real rejected follow-up request established the need for this adaptation.
The adjacent `OPENBENCH_LICENSE` retains the MIT permission and copyright notice,
Copyright (c) 2026 Matthew Lam. Selected real-socket tests were adapted from the
same revision's
[`obench/tests/test_sandbox_gateway.py`](https://github.com/davisbuilds/openbench/blob/39fec903f1e73bd3e8e3b15cacefbcce051bd1a6/obench/tests/test_sandbox_gateway.py).

`skilleval.gateway` owns the local typed adaptation: explicit host authentication,
expiry checks, qualification ceilings, bounded receipts, cleanup, and the pinned
native guest profile. The upstream broker CLI is not the supported host entry
point. See [GATEWAY.md](../system/GATEWAY.md) for that boundary and renewal rules.
This is source adoption, not an endorsement, live provider proof, or evidence of
runtime isolation. The public calibration corpus was newly authored for this
repository and does not copy private review content.

## How adoption is recorded

For a selected component, record its exact public source/revision, intended use,
and local owner. Check the license and any NOTICE or file-level terms at that
revision before copying code, prompts, assets, or fixtures. Retain required
notices with incorporated material and add a concise README acknowledgement.
Attribution is not a substitute for permission or license compatibility.

Record whether a change is a dependency, copied implementation, adaptation, or
independent implementation informed by a design. Keep meaningful upstream tests
where applicable, add skilleval-specific controls, and state remaining limits.
Do not present a candidate as adopted, a source inspection as a successful run,
or upstream benchmark results as evidence of skilleval's effectiveness.
