FROM node:22-slim

RUN npm install -g @modelcontextprotocol/server-filesystem@2025.12.18

RUN useradd --create-home --shell /bin/bash mcpuser \
    && mkdir -p /data \
    && chown mcpuser:mcpuser /data

USER mcpuser
WORKDIR /data

ENTRYPOINT ["mcp-server-filesystem"]
