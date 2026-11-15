# First build node-debugging with this task's exact frozen manifests/lock.
# NODE_IMAGE is an immutable digest or an independently verified local image ID.
ARG NODE_IMAGE
FROM ${NODE_IMAGE}
ARG CHROME_SHA256=1f496a826c9ba4d6a5f5bf2de686145fe718528c2cc3dbeb96b5893b89653724
RUN test -f /usr/local/bin/node && test ! -L /usr/local/bin/node && \
    mkdir -p /usr/local/lib/node-runtime/bin && \
    mv /usr/local/bin/node /usr/local/lib/node-runtime/bin/node && \
    sha256sum /usr/local/lib/node-runtime/bin/node > /usr/local/lib/node-runtime/binary.sha256
COPY node-launcher.sh /usr/local/bin/node
RUN chmod 0755 /usr/local/bin/node && \
    test "$(node --version)" = v24.22.1 && \
    sha256sum -c /usr/local/lib/node-runtime/binary.sha256
# Official Chrome for Testing 156.0.8078.80, linux-arm64; never the linux64 asset.
COPY chrome.zip /tmp/operational-metrics-chrome.zip
RUN set -eux; test "$(uname -m)" = aarch64 && \
    echo "$CHROME_SHA256  /tmp/operational-metrics-chrome.zip" | sha256sum -c - && \
    apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates fonts-liberation libasound2 libatk-bridge2.0-0 libatk1.0-0 \
      libatspi2.0-0 libcairo2 libcups2 libdbus-1-3 libdrm2 libgbm1 libglib2.0-0 \
      libnspr4 libnss3 libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 libxdamage1 \
      libxext6 libxfixes3 libxkbcommon0 libxrandr2 && \
    mkdir -p /usr/local/share/chrome-for-testing && \
    cp -Lr /etc/fonts /usr/local/share/chrome-for-testing/fontconfig && \
    python3 -m zipfile -e /tmp/operational-metrics-chrome.zip /usr/local/share/chrome-for-testing && \
    chmod 0755 /usr/local/share/chrome-for-testing/chrome-linux-arm64/chrome \
      /usr/local/share/chrome-for-testing/chrome-linux-arm64/chrome_crashpad_handler \
      /usr/local/share/chrome-for-testing/chrome-linux-arm64/chrome_sandbox && \
    chown -R root:root /usr/local/share/chrome-for-testing && \
    chmod -R go-w /usr/local/share/chrome-for-testing && \
    ! ldd /usr/local/share/chrome-for-testing/chrome-linux-arm64/chrome | grep 'not found' && \
    python3 -c "import subprocess; version=subprocess.check_output(['/usr/local/share/chrome-for-testing/chrome-linux-arm64/chrome','--version'],text=True); print(repr(version)); assert version.strip() == 'Google Chrome for Testing 156.0.8078.80'" && \
    test ! -e /usr/local/bin/playwright && \
    test -x /usr/local/share/task-dependencies/node_modules/.bin/playwright && \
    printf '#!/bin/sh\nexec /usr/local/share/task-dependencies/node_modules/.bin/playwright "$@"\n' > /usr/local/bin/playwright && \
    chmod 0755 /usr/local/bin/playwright && \
    dpkg-query -W > /usr/local/share/chrome-for-testing/system-packages.txt && \
    rm -rf /var/lib/apt/lists/* /tmp/operational-metrics-chrome.zip
