# Use a minimal manifest-only context and an immutable, context-clean native image.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG NODE_SHA256
ARG PNPM_SHA256
ARG JQ_SHA256
COPY node.tar.xz pnpm.tgz /tmp/node-debugging-build/
COPY jq /usr/local/bin/jq
COPY JQ_COPYING /usr/local/share/licenses/jq/COPYING
RUN test -n "$NODE_SHA256" && test -n "$PNPM_SHA256" && \
    test -n "$JQ_SHA256" && echo "$JQ_SHA256  /usr/local/bin/jq" | sha256sum -c - && \
    chmod 0755 /usr/local/bin/jq && jq --version && \
    echo "$NODE_SHA256  /tmp/node-debugging-build/node.tar.xz" | sha256sum -c - && \
    echo "$PNPM_SHA256  /tmp/node-debugging-build/pnpm.tgz" | sha256sum -c - && \
    tar --no-same-owner --no-same-permissions -xJf /tmp/node-debugging-build/node.tar.xz -C /usr/local --strip-components=1 && \
    mkdir -p /usr/local/lib/node_modules/pnpm && \
    tar --no-same-owner --no-same-permissions -xzf /tmp/node-debugging-build/pnpm.tgz -C /usr/local/lib/node_modules/pnpm --strip-components=1 && \
    test ! -e /usr/local/bin/pnpm && \
    printf '#!/bin/sh\nunset pnpm_config_verify_deps_before_run\nexport PNPM_CONFIG_VERIFY_DEPS_BEFORE_RUN=false\nexec /usr/local/bin/node /usr/local/lib/node_modules/pnpm/bin/pnpm.cjs --config.verify-deps-before-run=false "$@"\n' > /usr/local/bin/pnpm && \
    chmod 0755 /usr/local/bin/pnpm && \
    test "$(node --version)" = v24.22.1 && test "$(pnpm --version)" = 11.28.6 && \
    python3 --version && git --version && bash --version && curl --version && \
    gcc --version && g++ --version && make --version && \
    rm -rf /tmp/node-debugging-build
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml /tmp/node-debugging-manifests/
COPY frontend/package.json /tmp/node-debugging-manifests/frontend/package.json
COPY LICENSE /usr/local/share/licenses/node-debugging-task-dependencies/LICENSE
RUN mkdir -p /usr/local/share/task-dependencies && \
    cp -R /tmp/node-debugging-manifests/. /usr/local/share/task-dependencies/ && \
    cd /usr/local/share/task-dependencies && \
    printf '\n# Image dependency layout; captured task configuration is unchanged.\nnodeLinker: hoisted\n' >> pnpm-workspace.yaml && \
    npm_config_build_from_source=true npm_config_nodedir=/usr/local pnpm install --frozen-lockfile && \
    cmp pnpm-lock.yaml /tmp/node-debugging-manifests/pnpm-lock.yaml && \
    test ! -d frontend/node_modules && \
    node --input-type=module -e "import Database from 'better-sqlite3'; const db = new Database(':memory:'); if (db.prepare('select 1 as n').get().n !== 1) process.exit(1); db.close();" && \
    node --input-type=module -e "import {zstdCompressSync,zstdDecompressSync} from 'node:zlib'; const input=Buffer.from('native zstd control'); if (!zstdDecompressSync(zstdCompressSync(input)).equals(input)) process.exit(1);" && \
    python3 -c "from pathlib import Path; paths=[Path('/usr'),Path('/usr/local'),Path('/usr/local/share'),Path('/usr/local/share/task-dependencies'),Path('/usr/local/share/task-dependencies/node_modules')]; [(print(str(p),p.stat().st_uid,p.stat().st_gid,oct(p.stat().st_mode & 0o777)),None) for p in paths]; assert all(not p.is_symlink() and p.is_dir() and p.stat().st_uid == 0 and p.stat().st_gid == 0 and not p.stat().st_mode & 0o022 for p in paths)" && \
    rm -rf package.json pnpm-lock.yaml pnpm-workspace.yaml frontend /tmp/node-debugging-manifests /root/.cache /root/.local/share/pnpm /root/.local/state/pnpm
# The parent dependency directory is outside the collected editable task tree.
# Native solvers require the explicit read-only projection owned by solver_boundary.
RUN mkdir -p /workspace/task/node_modules && \
    ln -s /usr/local/share/task-dependencies/node_modules /workspace/node_modules && \
    for tool in tsc tsx eslint vite vitest svelte-check; do \
      test ! -e "/usr/local/bin/$tool" && \
      test -x "/usr/local/share/task-dependencies/node_modules/.bin/$tool" && \
      printf '#!/bin/sh\nexec /usr/local/share/task-dependencies/node_modules/.bin/%s "$@"\n' "$tool" > "/usr/local/bin/$tool" && \
      chmod 0755 "/usr/local/bin/$tool"; \
    done && \
    mkdir /tmp/node-debugging-codex-version && \
    HOME=/tmp/node-debugging-codex-version CODEX_HOME=/tmp/node-debugging-codex-version/codex-home codex --version && \
    rm -rf /tmp/node-debugging-codex-version && \
    test ! -e /root/.codex
WORKDIR /workspace/task
