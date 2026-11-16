# Results and human exceptions

The local results view presents a selected agent or human synthesis alongside
retained evidence excerpts. It leads with conclusions and quality, usage, cost
and time, then separates material human questions from agent follow-ups and
qualified unknowns. An empty human queue is useful: missing evidence does not
automatically require owner judgment.

This is a presentation and response-recording layer. It does not call a model,
dispatch experiments, change original judgments, select a winner, or establish
grader reliability. An investigating agent authors the synthesis after checking
the original request, selected results and contrary evidence. Use existing study
and handoff reports for canonical consumption; incomplete usage remains unknown.
Do not promote a raw diagnostic into a canonical total in display prose.

The separate [synthesis pipeline](SYNTHESIS.md) can perform that investigation in
a recorded native agent session and produce a packet for this same view. Manual
authored packets remain supported; opening the view itself never invokes a model.

## Prepare and open

```sh
uv run skilleval storage init
review_dir="$(uv run skilleval storage path audits --plain)/review-demo-v1"
uv run skilleval results prepare --spec examples/results/review/source.json \
  --output "$review_dir"
uv run skilleval results inspect "$review_dir"
uv run skilleval results serve "$review_dir"
```

Open the loopback URL printed to stderr; Ctrl-C stops the server. The default
port is chosen by the OS; `--port` selects one explicitly. Serving needs only
Python and the packaged assets, not Node, a browser download, VM or credential.
The example is synthetic and exercises a real human question; it is not
calibration or task-effect evidence. Use a new directory for each generation.

`skilleval schema results-review-spec` owns the exact source contract. A source
document names UTF-8 files, expected SHA-256 digests and one-based inclusive line
ranges. Relative paths resolve against that document. Preparation verifies bytes,
reference identities and bounded ranges, then freezes excerpts in `packet.json`.
Its content hash is the packet ID. `source-spec.json` retains the authoring input
for provenance but is never served. The browser receives source labels/digests,
locators and selected excerpts, without a general file-browsing endpoint. Review
private source labels and excerpts before any sharing; these files can contain
sensitive task context.

The projection remains usable after source files move. Checking a packet verifies
its self-contained identity, not current external sources, authorship authenticity
or semantic truth. Changed evidence or interpretation requires a newly prepared
generation; originals remain unchanged. Author/model attribution is declared, not
authenticated. No automatically computed numeric quality score is implied.

## Route only consequential questions

Each exception includes a precise question, impact, prior investigation and cited
evidence. Only `human-needed` accepts an answer, and it must also state why human
input is necessary. `agent-follow-up` identifies remaining investigation;
`qualified-unknown` preserves a limit without demanding action. Categorization is
the synthesizing agent's judgment; schema validation does not make it correct.
Follow [measurement policy](../project/MEASUREMENT.md#human-involvement).

The human form allows a suggested answer or free text. Each save creates a new
UUID-named JSON record in `responses/`, bound to the packet and question IDs with
a UTC timestamp. Further answers append history. They do not overwrite a verdict,
automatically regrade an artifact, resolve an independent audit or authorize a
new experiment. A later agent can inspect those files and prepare a new synthesis
that cites the operator's answer. This initial interface has no automated
follow-up dispatch, calibration sampling or cross-packet inbox.

If saving fails, the draft remains. If response history is unavailable, the view
still shows the immutable conclusions, marks prior responses unknown and disables
saving until an explicit retry succeeds. It never substitutes empty history for
corrupt history. Unknowns stay visible even when no human question remains.

## Local boundary

The Python server binds only `127.0.0.1`, verifies Host/Origin and requires a
session token for answer writes. It serves a fixed set of assets and JSON routes,
has no CORS access and renders captured text literally under a restrictive content
security policy. It rejects stale packet identities, invalid question targets,
oversized requests and malformed stored responses. Token and answer values are
excluded from request logs. This protects the browser boundary; it is not
multi-user authentication or protection against a process already acting as the
same OS user. Keep it local and stop it when finished.

## Development and verification

TypeScript source lives in `frontend/app.ts`; committed browser assets live in
`src/skilleval/review_ui/` and ship in the Python distribution. Node is a development
dependency only. With the Node version in `.node-version`:

```sh
npm ci --ignore-scripts
npm run build:ui
npm run check:ui
scripts/check
```

The deterministic gate checks Python contracts, real loopback HTTP/persistence,
TypeScript types and compiled-asset drift. For UI changes, also run the explicit
browser control using an already installed Playwright module and browser:

```sh
SKILLEVAL_PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs \
  SKILLEVAL_BROWSER_CHANNEL=chrome npm run verify:ui
```

That control uses synthetic data and a small HTTP fixture: overview, unknown cost,
question routing, save/reload, draft retention, history errors/retry, literal
hostile text, empty queue, mobile width and zero remote requests. It downloads
nothing. Also exercise the actual `results serve` command through a browser after
transport changes; the fixture alone cannot qualify the production route.
