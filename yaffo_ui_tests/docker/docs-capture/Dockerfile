# Renders guide screenshots identically wherever the capture runs.
#
# The Playwright suite confines generated code with sandbox-exec/bwrap instead, which is
# cheaper and enough for *safety*. Docs capture has a different requirement: the output
# is a committed image, so it must be byte-stable across machines. macOS and Linux
# disagree on font metrics, which changes line wrapping, which moves layout — so a
# screenshot taken on a laptop and one taken in CI would differ on every run and the
# pixel comparison would be pure noise. Pinning the whole rendering stack is the point.
#
# Pinned to the Playwright version in package.json; bump both together.
FROM mcr.microsoft.com/playwright:v1.57.0-noble

# node_modules is built here rather than mounted: the host's is compiled for darwin and
# would not run. The run script masks the mounted one with an anonymous volume so this
# copy wins.
WORKDIR /app/yaffo_ui_tests
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# The repo is mounted over /app at run time; this is only the dependency layer.
WORKDIR /app/yaffo_ui_tests
