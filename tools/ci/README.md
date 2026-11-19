# CI tools

These tools are isolated from skilleval's dependencies. They run on hosted
runners against public source and synthetic inputs; no VM, model account, or
private dataset is needed.

## Review UI compiler

The root `package-lock.json` pins TypeScript 7.0.2 and its platform-specific
compiler packages as development dependencies. Install with
`npm ci --ignore-scripts`. `npm run check:ui` typechecks the source and detects
drift from committed Python-package assets. Dependabot observes an eight-day
cooldown for npm updates. Renewal checks release age, advisories and available
publisher evidence before updating the exact pin/lock.

`.node-version` pins the development/CI runtime; the installed Python application
does not invoke Node. The initial 24.22.3 pin takes a narrow early-release exception
through 2026-11-18: this [security release](https://nodejs.org/en/blog/release/v24.22.3)
supersedes 24.22.1 and repairs HTTP/TLS and other defects. It is confined to the
UI compiler/verification tools and public hosted CI, with package install scripts
disabled. The maintainer owns this exception; it expires once the seven-day age
window elapses. Local qualification used the official darwin-arm64 archive,
verified against the published SHA256
`ff6e17d4874b76d272212b908cc020d6e815fba5a6c7eaaa06add725840307fd`;
no local host default was changed. Published checksum agreement is not signature
verification. CI uses the pinned official setup-node action to obtain its runtime.

## Workflow audit

`pyproject.toml` and `uv.lock` pin zizmor and its distribution hashes. Use the
Python and uv versions in `../../.github/workflows/workflow-audit.yml`:

```sh
uv sync --project tools/ci --locked
uv run --project tools/ci --locked zizmor --offline .github/workflows/
```

Dependabot proposes weekly uv and action updates after an eight-day cooldown.
The uv tool project lives outside `.github`: the hosted updater rejected a
resolved tool update there during PR creation. Related upstream reports are
[dependabot-core #15237](https://github.com/dependabot/dependabot-core/issues/15237)
and [#15721](https://github.com/dependabot/dependabot-core/issues/15721).
Installation keeps the seven-day release quarantine. Review SHA/version comments
together; Dependabot does not update workflow tool-version inputs. Security
updates bypass its cooldown and still need review and an explicit, narrow
exception if they conflict with installation policy.

## Secret scanner

`build-gitleaks.sh` builds Gitleaks 8.30.1 with Go 1.27.3 and the complete graph in
`gitleaks/go.mod` and `gitleaks/go.sum`. It uses the public Go proxy and checksum
database, disables ambient workspace/toolchain overrides, verifies modules, and
builds with a read-only module graph. The binary stays outside tracked source.

The upstream release binary predates the patched compiler and archive/crypto
dependencies selected here. A version pin alone would preserve those older
components. The graph explicitly updates compress, rardecode, xz, archives, and
the Go crypto/sys/text modules; the lock records the resulting transitive graph.
Go is only a scanner build dependency, not an application implementation language.

The checksum-verified upstream module is
`github.com/zricethezav/gitleaks/v8@v8.30.1`, with module digest
`h1:PmEvCfVI7ti9dV3s5aMZUY7sS2GxRvG3yzih7E+cS3w=` and source revision
`8d1f98c7967eb1e79cb44ac6241a124e145d2165`. The current upstream release tag
resolves to `83d9cd684c87d95d656c1458ef04895a7f1cbd8e`; their difference is release
packaging configuration, not Go source. The build uses the checksum-verified
module rather than resolving that mutable tag. Upstream source and licensing:
[Gitleaks](https://github.com/gitleaks/gitleaks).

From the repository root, with the pinned Go compiler:

```sh
set -eu
umask 077
uv run skilleval storage init
ci_tools_dir="$(uv run skilleval storage path tools ci-scanners-v1 --plain)"
mkdir "$ci_tools_dir"
tools/ci/build-gitleaks.sh "$ci_tools_dir/gitleaks"
"$ci_tools_dir/gitleaks" git --redact --no-banner --log-opts=--all .
```

CI first requires a synthetic credential pattern to be detected, then scans all
fetched history with redacted output. A successful scan establishes coverage of
that scanner invocation, not suitability of every file for public disclosure.

When renewing the scanner, inspect the upstream source/license, exact compiler,
and resolved graph for advisories; verify checksums and source provenance. Keep
`go.mod`, `go.sum`, the script's compiler/version checks, workflow inputs, and this
reference consistent. Rebuild and exercise the detection control and history
scan. Do not replace the repaired graph with an upstream binary solely because
its version is newer. Scanner renewal is deliberate; Dependabot does not own
this Go tool graph.
