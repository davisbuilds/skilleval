# Source-grounded product-page development task

This authored commission asks for an AgentMonitor hero and introduction. The
audience, arrival scenario, format and action in [brief.md](brief.md) are task
author choices, not customer-research findings or an observed historical request.

Capture the complete [public product README](https://github.com/davisbuilds/agentmonitor/blob/fe6f46138cad1191606ad0df5985f8fd3b5eb42d/README.md)
and its root MIT license at that revision as `source/README.md` and
`source/LICENSE`. Preserve its voice and all relevant qualifications. The README
contains product installation instructions as source material; the task itself
requires only text/files and does not install or run AgentMonitor.

The selected ON treatment is the complete [copywriting 2.3.1 subtree](https://github.com/coreyhaines31/marketingskills/tree/e85f3cbc2e4a79bf33b2a12e9bdd9087d414735b/skills/copywriting).
Retain its five files and root MIT notice. Preserve embedded third-party
attribution, including the upstream-declared Profound adaptation; the captured
notice is [MIT](https://github.com/retieedra-profound/skills-marketing-library/blob/80293f156c27d3f90aedebfb3c27309769b2eec6/LICENSE).
That notice revision does not establish the historical adaptation revision.
Referenced adjacent skills are not automatically installed. Product and skill
captures are experimental inputs, not runtime dependencies of skilleval.

[worker.py](worker.py) checks Markdown structure, headline/subheadline/introduction
lengths, and the CTA destination. It returns raw boolean observations for host
comparison. Hyphenated words count as one. It does not assess meaning, voice,
product claims or whether a reader would convert. The artifact contract permits
only `page.md`; source modifications are retained as invalid scope.

The two [control drafts](controls/) use different angles. A related negative
control replaces the estimated-cost qualification with a false claim of exact
costs for every model. It deliberately satisfies mechanical checks so that the
semantic grader must distinguish it using the source. These are exposed, related
development controls, not independent tasks or audited reference labels.

Use the [task package workflow](../../../docs/system/TASKS.md) to bind the source,
environment, validation and checks. Keep control drafts, expected labels and
grading fixtures outside solver input. Give both arms the same source and brief;
ON adds only the explicit selected-skill invocation. Freeze outcomes and budgets
before running. Preserve disagreements and failures rather than revising the
rubric to favor a result. A writing-quality judgment is not conversion evidence.
The current brief omits evaluator-specific wording. Old frozen prompts and runs
retain their original bytes and are not retroactively blinded.
