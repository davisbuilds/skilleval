# Operational-metrics browser image

This opt-in Linux aarch64 extension adds Chrome for Testing to the
[Node debugging image](../node-debugging/README.md). Build that image first using
the task's exact frozen root/frontend manifests and lock. The operational-metrics
source revision `0dd253129d143b5811224a0f80554a64eb3f4da0` has different dependencies
from the completed-sessions capture; sharing Node 24.22.1, pnpm 11.28.6 and jq
inputs does not permit sharing its old installed dependency tree.

The extension's minimal context contains only the checked-in `node-launcher.sh`
and `chrome.zip`, downloaded from the
[official version manifest](https://googlechromelabs.github.io/chrome-for-testing/156.0.8078.80.json):

```text
https://storage.googleapis.com/chrome-for-testing-public/156.0.8078.80/linux-arm64/chrome-linux-arm64.zip
SHA256 1f496a826c9ba4d6a5f5bf2de686145fe718528c2cc3dbeb96b5893b89653724
```

The version matches the frozen repository's CI browser pin; the architecture
variant is deliberately different. The official manifest explicitly lists
`linux-arm64`; this is neither an x64 archive nor emulated execution. On
2026-11-08, the October 29 [upstream release](https://chromereleases.googleblog.com/2026/10/stable-channel-update-for-desktop_02107719452.html)
was ten days old and included the CVE-2026-76024 fix required by the source's
Chrome `>=156.0.8078.79` guard. This is release/provenance evidence, not a claim
that the selected build has no vulnerabilities. CfT's manifest supplies no
archive checksum or signature: the digest above was measured after downloading
from its official HTTPS URL, rather than verified against a publisher signature.
Recheck advisories and eligibility before renewing this image.

```sh
docker --context YOUR_BUILD_CONTEXT build --progress=plain \
  --build-arg NODE_IMAGE=YOUR_VERIFIED_REFRESHED_NODE_IMAGE \
  -f environments/operational-metrics/Dockerfile \
  -t skilleval-operational-metrics:local /path/to/chrome-only-context
docker --context YOUR_BUILD_CONTEXT image save \
  -o /path/to/operational-metrics.tar skilleval-operational-metrics:local
shasum -a 256 /path/to/operational-metrics.tar
```

Retain base/Node image IDs and archive hashes, tool input hashes and release
evidence, recipe hash, apt resolution/build logs, final image ID/archive hash,
and `/usr/local/share/chrome-for-testing/system-packages.txt` outside tracked
source. A verified local image tag is only a build handle. Debian packages follow
the verified Bookworm base's configured repositories; record the actual resolved
versions instead of presenting this networked apt step as a reproducible snapshot.
The image imports no task implementation, prompt, checker, fixture, host HOME,
database, provider configuration or credentials. Do not change the image or
execution source while its VM is active.

Chrome lives under `/usr/local/share` because `solver-isolated-v1` projects that
runtime tree read-only and excludes `/opt`. The frozen browser guard disables
Chrome's own sandbox on Linux; retain those source bytes and the outer VM/solver
boundary. The boundary permits Unix and loopback TCP sockets, but denies vsock
and io_uring. Library checks and `--version` during the image build do not prove
Chrome can start under the solver UID, inherited seccomp policy, private `/proc`,
shared memory and actual VM resources. Start qualification with the normal
two-vCPU/2-GiB profile and a single browser; retain OOM, timeout or launch failures
and change resources explicitly if required.

The solver also excludes `/etc/fonts`. The image copies dereferenced ordinary
fontconfig configuration under Chrome's runtime directory and points
`FONTCONFIG_PATH`/`FONTCONFIG_FILE` there; fonts themselves remain under `/usr`.
This is an image adaptation, not an extension of the solver's filesystem allowlist.

The native and offline solver routes construct a minimal environment and discard
Docker `ENV`. The image therefore retains the exact Node binary at
`/usr/local/lib/node-runtime/bin/node` with its SHA256 recorded alongside it, and
installs a small `/usr/local/bin/node` launcher. The launcher sets absent browser
and fontconfig defaults before execing that binary. Explicit caller overrides
remain intact. Ordinary pnpm, Playwright and direct Node entry points use this
route; children inherit the defaults. `process.execPath` consequently names the
retained binary location rather than `/usr/local/bin/node`. This changes the
launcher path, not the Node bytes/version or captured repository source.
Toolchain controls record both binary identity and child-observed configuration;
the browser lane must still prove actual native-shell discovery and launch.

Use the [task environment lanes](../../examples/document/operational-metrics/environment/README.md)
under the actual solver boundary before claiming the browser path works. The
dependency projection and ordinary test commands remain the Node image's
contract. The launcher supplies `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`; the
frozen source selects the explicit Chrome executable and rejects older builds.
No bundled-browser fallback is accepted.
