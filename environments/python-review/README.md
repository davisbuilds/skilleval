# Python review image

This opt-in build recipe installs a task's frozen Python dependencies and a
verified native Codex binary. The image carries no task source or credentials.
Use a dedicated builder with no host directory mounts. Building an image does
not qualify the environment for scored execution.

Prepare a separate, minimal build context containing only:

- `requirements.txt`: the task's locked dependencies with package hashes.
- `codex`, `codex-code-mode-host`, and `rg`: matching Linux tools for the guest
  architecture, verified against the selected official package release digest.
  The inspected baseline is 0.164.0; stable same-major upgrades may enter
  qualification. Sol's native code-mode catalog requires
  the companion host; a standalone CLI binary is insufficient.
- `openbench_gateway.py` and `OPENBENCH_LICENSE`: the exact adjacent vendor
  implementation and notice from `src/skilleval/_vendor/`.

Do not use the repository root or a task checkout as the build context. Verify
that the selected base includes Git and the required Python version; pin its
architecture-specific OCI manifest digest. Use an explicit Docker context so
building does not change the user's default context:

```sh
docker --context YOUR_BUILD_CONTEXT build \
  --build-arg BASE_IMAGE='docker.io/library/python@sha256:ACTUAL_DIGEST' \
  -f environments/python-review/Dockerfile \
  -t skilleval-python-review:local /path/to/minimal-context
docker --context YOUR_BUILD_CONTEXT image save \
  -o /path/to/task-image.tar skilleval-python-review:local
shasum -a 256 /path/to/task-image.tar
```

Retain the base digest, recipe/input hashes, image ID, archive digest, tool
versions, and build output with the private qualification receipt. Different
tasks generally need different dependency images. The immutable archive is the
runtime input; a mutable local tag is only a build handle. Stop a disposable
builder when export is complete.
