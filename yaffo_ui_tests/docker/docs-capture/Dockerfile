# The whole documentation environment: the Yaffo sandbox the guide is captured from,
# and the browser that captures it.
#
# The Playwright suite confines generated code with sandbox-exec/bwrap instead, which is
# cheaper and enough for *safety*. Docs capture has a different requirement: the output
# is a committed image, so it must be byte-stable across machines. Pinning only the
# browser is not enough. macOS and Linux disagree on font metrics, but they also
# produce different thumbnails (Pillow/libwebp builds), face boxes and label scores
# (ONNX Runtime kernels), and fixture paths (/private/tmp). So the fixture is built,
# and the app served, from this same image on every machine.
#
# Build it for linux/amd64, the architecture of the CI runners, even on Apple
# Silicon: Skia and ONNX Runtime take different SIMD paths per architecture.
#
# Build context is yaffo_ui_tests/, with the repo root as the named `repo` context:
#   docker build --platform linux/amd64 --build-context repo=.. \
#       -t yaffo-docs-capture:latest -f docker/docs-capture/Dockerfile .
#
# Pinned to the Playwright version in package.json; bump both together.
FROM mcr.microsoft.com/playwright:v1.57.0-noble

# Python comes from uv rather than the distribution, so the interpreter matches the
# version pinned in pyproject.toml rather than whatever Ubuntu ships.
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_NO_CACHE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN uv python install 3.13.3 \
    && uv venv --python 3.13.3 /opt/venv

# Only the dependencies. The source is mounted read-only at run time, so editing it
# never needs a rebuild; changing a dependency does.
COPY --from=repo pyproject.toml VERSION README.md LICENSE /tmp/yaffo-project/
RUN uv pip install --python /opt/venv/bin/python -r /tmp/yaffo-project/pyproject.toml \
    && rm -rf /tmp/yaffo-project \
    && chmod -R a+rX /opt/python /opt/venv

# The app's home. Yaffo abbreviates paths under it ("~/...") and opens folder pickers
# there, so it is a fixed, empty directory outside the fixture, writable by whichever
# user the container runs as.
RUN mkdir -p /home/yaffo && chmod 1777 /home/yaffo

# node_modules is built here rather than mounted: the host's is compiled for darwin and
# would not run. The run script masks the mounted one with an anonymous volume so this
# copy wins.
WORKDIR /app/yaffo_ui_tests
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# The repo is mounted over /app at run time; this is only the dependency layer.
WORKDIR /app/yaffo_ui_tests
