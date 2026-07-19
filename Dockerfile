# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.13.3-slim-bookworm

FROM ${PYTHON_IMAGE} AS wheel-builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY pyproject.toml README.md LICENSE VERSION ./
COPY yaffo ./yaffo
RUN python -m pip wheel --wheel-dir=/wheels .

FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    YAFFO_ASSET_DIR=/opt/yaffo-assets \
    YAFFO_DATA_DIR=/data

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        perl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 yaffo \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin yaffo \
    && mkdir -p /data /identity /opt/yaffo-assets \
    && chown 10001:10001 /data /identity

COPY --from=wheel-builder /wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels yaffo \
    && rm -rf /wheels

# Fetch model/binary assets while building. Demo startup deliberately performs
# no downloads, so the runtime can use a read-only root filesystem.
RUN YAFFO_DATA_DIR=/tmp/asset-build python -m yaffo.download_assets \
    && rm -rf /tmp/asset-build

COPY deploy/demo/container-entrypoint.sh /usr/local/bin/yaffo-demo-entrypoint
# download_assets.py's exiftool pruning step chmods some directories to 0700 as a
# side effect of deleting read-only files from the upstream tarball; since the
# download itself runs as root, those end up root-owned. chown to the runtime
# user before the read-only pass so it keeps read+execute (not just root).
RUN chmod 0755 /usr/local/bin/yaffo-demo-entrypoint \
    && chown -R 10001:10001 /opt/yaffo-assets \
    && chmod -R a-w /opt/yaffo-assets

USER 10001:10001
EXPOSE 5101/tcp 5201/udp
ENTRYPOINT ["/usr/local/bin/yaffo-demo-entrypoint"]
CMD ["python", "-m", "yaffo"]

