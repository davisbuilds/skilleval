# CI tools

These tools are isolated from skilleval's dependencies. They run on hosted
runners against public source and synthetic inputs; no VM, model account, or
private dataset is needed.

## Workflow audit

`pyproject.toml` and `uv.lock` pin zizmor and its distribution hashes. Use the
Python and uv versions in `../workflows/workflow-audit.yml`:

```sh
uv sync --project .github/ci-tools --locked
uv run --project .github/ci-tools --locked zizmor --offline .github/workflows/
```

Dependabot proposes weekly uv and action updates after an eight-day cooldown.
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
mkdir -p .local/ci-tools
.github/ci-tools/build-gitleaks.sh "$PWD/.local/ci-tools/gitleaks"
.local/ci-tools/gitleaks git --redact --no-banner --log-opts=--all .
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
