# Receipt-admission development seed

The [pilot shortlist](../../../docs/project/PILOT-CANDIDATES.md) records the public
historical source, base and reference fix. The task brief and observation fixtures
here are newly authored for skilleval. They are exposed development material,
not a protected evaluation task or evidence of task difficulty.

`worker.py` is VM-only. It imports the submitted source and exercises its real
receipt consumer, validator, control assessment, source hashes and workflow
checks against temporary files. Task inspection supplies an authored task
fixture, and the Docker replay boundary supplies controlled observations. This
covers receipt-admission decisions, not production Docker grading or isolation.
Expected observations are frozen in the host repair contract; no expected-value
file, historical repair or study history belongs in solver input.

Use the untouched parent and historical fix as polarity controls. Also reject
always-accept and always-reject variants. Run ordinary focused project tests in
the qualified guest. All variants of this seed are related controls. Passing its
finite checks leaves semantic quality and representativeness unassessed.

Start from `spec.template.json`: replace the source path/digest and the worker
SHA-256 with independently retained identities, then run `repair prepare`.
The prompt is embedded as plain text; the CLI does not expand template variables.
Use the same shared prompt/input in a `mode: repair` study task. See the
[repair procedure](../../../docs/system/REPAIRS.md) for collection and offline checks.
Keep source exports and generated task contracts outside tracked examples.
