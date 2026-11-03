# Node debugging image

This opt-in recipe extends a context-clean native Python/Codex image with Node
24.22.1, pnpm 11.28.6, npm from the official Node distribution, jq, and a task's
frozen workspace dependencies. It imports no task implementation, reference
repair, checker, prompt, credentials, or host configuration. Building the image
does not qualify a task or grant scored admission.

Prepare a dedicated minimal build context containing only:

- `node.tar.xz`: the official Node 24.22.1 Linux archive for the guest architecture.
- `pnpm.tgz`: the official npm registry tarball for pnpm 11.28.6.
- `jq` and `JQ_COPYING`: the matching official Linux binary and license. The
  inspected build uses jq 1.8.2.
- The task's exact `package.json`, `frontend/package.json`, `pnpm-lock.yaml`,
  `pnpm-workspace.yaml`, and `LICENSE`.

Verify official archive hashes, pnpm registry integrity/signature, and available
publisher attestations before building. Retain the exact base image archive
digest, loaded image ID, tool release evidence, input hashes, recipe hash, build
logs, final image ID and archive digest outside tracked source. Keep original
manifests in that evidence; the runtime image removes its build manifests and
caches. The source capture retains the ordinary repository manifests unchanged.
Runtime archives are extracted without preserving publisher UIDs or unrestricted
permission bits. The build checks root ownership and non-writable dependency
ancestry; official Node archives otherwise assign their publisher's UID to
shared directories such as `/usr/local/share`, which the offline guard rejects.

Use the project's dedicated Docker builder with an explicit context and no host
directory mounts. The base must already include native Codex and its companion,
Python, Git, Bash, curl, GCC/G++, and make. Pin an immutable registry base digest,
or load a retained Docker-save archive and verify its local tag's image ID before
and after building; a local tag is only a build handle.

```sh
docker --context YOUR_BUILD_CONTEXT build --progress=plain \
  --build-arg BASE_IMAGE=YOUR_VERIFIED_BASE \
  --build-arg NODE_SHA256=VERIFIED_NODE_ARCHIVE_SHA256 \
  --build-arg PNPM_SHA256=VERIFIED_PNPM_ARCHIVE_SHA256 \
  --build-arg JQ_SHA256=VERIFIED_JQ_BINARY_SHA256 \
  -f environments/node-debugging/Dockerfile \
  -t skilleval-node-debugging:local /path/to/minimal-context
docker --context YOUR_BUILD_CONTEXT image save \
  -o /path/to/node-debugging.tar skilleval-node-debugging:local
shasum -a 256 /path/to/node-debugging.tar
```

Dependency installation uses the exact frozen lock and original build allowlist.
The build-only workspace adds `nodeLinker: hoisted` so both workspace packages
resolve the same dependency tree from their ancestor. This is an explicit install
layout adaptation; matching versions alone does not establish test fidelity.
pnpm 11 defaults `verifyDepsBeforeRun` to `install`. Relocated read-only packages
and unchanged task workspace settings cannot satisfy that install-state check.
The image's `pnpm` launcher therefore supplies the supported
`--config.verify-deps-before-run=false` option and matching environment setting
for ordinary and nested commands. It clears pnpm's inherited lowercase internal
override before each invocation, so nested scripts keep the same explicit policy.
This disables implicit dependency installation, while executing the ordinary
requested scripts. It does not verify the preinstalled dependencies; frozen-lock
installation, native controls and baseline/reference test evidence do that
separately. An explicitly requested install remains an install operation.
The build rejects any remaining frontend-local dependency tree and checks that
the lockfile bytes did not change. Native SQLite and Node's native zstd functions
receive positive round-trip checks. SQLite packages that ship their native addon
inside the locked tarball use those bytes; source builds use the image compiler
and the selected Node archive's headers.

The dependency tree lives at
`/usr/local/share/task-dependencies/node_modules`, outside collected task source.
The image's `/workspace/node_modules` alias supports container build controls;
smolvm can replace `/workspace`, so offline observer preparation must explicitly
restore that fixed dependency alias. The native solver boundary must explicitly
project the same tree read-only at
`/workspace/node_modules`; placing it in the image alone does not expose an
ancestor directory inside the solver jail. The editable task keeps an empty real
`node_modules` directory for tests that create temporary bin entries there.
Small executable wrappers expose the existing `.bin` tools through
`/usr/local/bin` without replacing existing executables. Install, dependency
updates, and dependency writes are unsupported during offline execution.

Before using the image, validate untouched and reference captures through
`solver-isolated-v1`, including normal backend/frontend commands, effective tool
paths, native SQLite, zstd, generated files, source stability and final collection
budget. Record failures and skips explicitly. The legacy offline observer's
512 MiB address-space ceiling must also be checked for the selected Node route;
successful image build checks run outside that observer ceiling. Keep evaluator
metadata outside `/usr` and other solver-visible runtime trees. Do not alter an
image or execution source while its VM is active.
