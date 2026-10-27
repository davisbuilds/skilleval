# Build with an explicit immutable Python image and a minimal input directory.
# Task source, host configuration, and credentials never enter the build context.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY requirements.txt /opt/skilleval/requirements.txt
RUN python3 -m pip install --no-cache-dir --require-hashes \
    -r /opt/skilleval/requirements.txt
COPY --chmod=0755 codex codex-code-mode-host rg /usr/local/bin/
COPY openbench_gateway.py OPENBENCH_LICENSE /opt/skilleval/
RUN git --version && python3 --version && codex --version
WORKDIR /workspace/task
