# Vision

Skilleval should answer a practical question: on the work we actually delegate,
when does adding a particular skill improve the result enough to justify its
cost? Its first audience is a person maintaining skills and evaluating coding
agents against their own repositories.

The unit of work is a reproducible task with an explicit environment and evidence
contract. The unit of comparison is a matched experiment with a vanilla baseline,
a precisely identified skill treatment, repeated attempts, and separately
versioned assessments. Preserve execution failures, missing observations, novel
findings, and inconclusive comparisons.

Start with repository review tasks because historical material is available and
grading errors are visible. Use exposed historical cases to develop the
instrument. Introduce held-out real reviews, repairs, and bounded implementation
tasks before claiming coverage of ordinary engineering work. Task selection must
describe the target population, repository and language mix, difficulty, natural
versus seeded defects, and exposure history. A convenient case collection alone
does not establish representativeness.

Success means a maintainer can inspect why a run was valid, why a judgment was
made, and what a comparison can support. A result can be useful when it shows no
clear difference. Generic methodological overlap with vanilla is a legitimate
outcome, not a reason to change the scoring after seeing results.

The project owns experimental contracts, task/environment packaging, evidence,
grading, and analysis. Skills remain independently versioned inputs. Repositories
providing task evidence remain independent projects. Import useful existing logic
through explicit, documented interfaces; do not depend on sibling checkouts or
carry forward their entire runners.

Executable checks establish objective outcomes where possible. Semantic judges
assess claims against source and observations, with evidence citations and an
unresolved option. Human adjudication handles disagreement, novelty, and calibration
samples. No lexical matcher or model judge alone is the authority on review
quality. Judge reliability must itself be measured before a score is used to
rank skills.

Initial non-goals: a universal benchmark leaderboard, automatic skill optimization,
multi-tenant hosted execution, an agent framework, or a new virtualization engine.
Python owns the experiment core; TypeScript is reserved for a later evidence and
review UI; native task tooling remains in its original language.
