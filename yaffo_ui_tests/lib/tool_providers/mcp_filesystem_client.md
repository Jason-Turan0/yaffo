# MCP Filesystem Client

Wraps `@modelcontextprotocol/server-filesystem` as a `ToolProvider`, giving the AI test generator read access to source code, templates, and routes. Supports two transport modes: a bare `npx` process (default) and a Docker-sandboxed container.

## Prerequisites

- Node.js >= 18
- Docker (required only for `useDocker: true`) — [Docker Desktop](https://www.docker.com/products/docker-desktop/) or [Rancher Desktop](https://rancherdesktop.io/) (set engine to **dockerd/moby**)

## Building the Docker Image

```bash
npm run docker:build:mcp-filesystem
```

This builds `yaffo-mcp-filesystem:latest` from `docker/mcp-filesystem/Dockerfile`.

## Usage

### Basic (bare process)

```typescript
import { createFilesystemClient } from "@lib/tool_providers/mcp_filesystem_client";

const client = await createFilesystemClient(["/path/to/project"]);
const result = await client.callTool("read_file", { path: "/path/to/project/src/app.ts" });
await client.disconnect();
```

The MCP server runs as a child process with the same OS identity as the caller. `allowedDirectories` is enforced by the server's application-level JavaScript validation only.

### Docker-sandboxed

```typescript
const client = await createFilesystemClient(["/path/to/project"], {
  useDocker: true,
});
```

The MCP server runs inside a Docker container. Host directories are mounted as read-only volumes and paths are transparently translated between host and container.

### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `allowedDirectories` | `string[]` | *(required)* | Host directories the MCP server can access |
| `useDocker` | `boolean` | `false` | Run the MCP server inside a Docker container |
| `dockerImage` | `string` | `yaffo-mcp-filesystem:latest` | Docker image to use |
| `readonly` | `boolean` | `true` | Block write tools (`write_file`, `edit_file`, `create_directory`, `move_file`, `delete_file`) |

### Readonly Mode

Readonly mode is **on by default**. When a write tool is called, the client returns an error message instead of forwarding the call to the server:

```
Error: Tool "write_file" is not allowed in readonly mode
```

To allow writes:

```typescript
const client = await createFilesystemClient(["/path/to/project"], {
  readonly: false,
});
```

### Path Translation (Docker mode)

When `useDocker` is true, the client maps each allowed directory to `/data/0`, `/data/1`, etc. inside the container:

- **Tool arguments**: host paths in `path` and `paths` fields are rewritten to container paths before calling the server
- **Tool results**: container paths in the response text are rewritten back to host paths

This is transparent to callers — they always work with host paths.

### Demo Script

```bash
npx tsx scripts/demo_docker_filesystem_mcp.ts
npx tsx scripts/demo_docker_filesystem_mcp.ts --skip-build
npx tsx scripts/demo_docker_filesystem_mcp.ts --dir /some/other/path
```

The demo exercises read tools, write tool blocking, and out-of-bounds directory access, printing PASS/FAIL for each boundary test.

## Security

### Defense in Depth

The client provides three layers of protection:

| Layer | Mechanism | Enforced by |
|---|---|---|
| **Readonly mode** | Blocks write tools before they reach the server | Client (application code) |
| **Allowed directories** | Server rejects paths outside configured dirs | MCP server (application code) |
| **Docker isolation** | Container sees only mounted volumes, no network | Linux kernel (namespaces, cgroups) |

Without Docker, only the first two layers are active and both are application-level JavaScript checks. With Docker, the kernel enforces the boundaries.

### What Docker Isolation Provides

The container is launched with:

```
docker run --rm -i --network none -v /host/dir:/data/0:ro <image>
```

- **`--network none`** — no network access of any kind
- **`-v ...:ro`** — read-only volume mounts; kernel-enforced, not bypassable from inside the container
- **`--rm`** — container is destroyed on exit, no persistent state
- **Non-root user** — the `mcpuser` account inside the container has no sudo/su access
- **Minimal image** — `node:22-slim` with only the MCP server installed

### Attack Surface Analysis

If the MCP server process inside the container is fully compromised:

| Attack vector | Result |
|---|---|
| Read mounted files | **Allowed** (intended functionality) |
| Write to mounted volume | **Denied** — `:ro` flag is kernel-enforced |
| Write to container filesystem | Allowed but ephemeral (`--rm` destroys on exit) |
| Network access | **Denied** — `--network none` |
| Read host filesystem outside mounts | **Denied** — not mounted, not visible |
| Install packages | **Denied** — non-root user, no network |
| Privilege escalation | **Denied** — no sudo, non-root |
| Docker socket escape | **Denied** — socket not mounted |
| Host process visibility | **Denied** — container PID namespace |
| Mount new filesystems | **Denied** — non-root, no `CAP_SYS_ADMIN` |

**Worst case**: an attacker can read exactly what the MCP server is supposed to read and nothing more.

### Manual Container Exploration

To interactively verify the security boundaries, start a shell inside the container:

```bash
docker run --rm -it --network none \
  -v "$(pwd)":/data/0:ro \
  --entrypoint /bin/bash \
  yaffo-mcp-filesystem:latest
```

Then try these commands from inside the container:

```bash
# Identity
whoami                        # mcpuser
id                            # uid=1000, no special groups

# Read mounted files (should work)
ls /data/0
cat /data/0/package.json | head -5

# Write to mounted volume (should fail)
touch /data/0/pwned.txt
echo "hacked" > /data/0/evil.txt

# Write to container fs (works but ephemeral)
touch /tmp/test.txt

# Network (should all fail)
curl https://example.com
ping 8.8.8.8
wget https://example.com

# Host filesystem (not visible)
ls /home
cat /etc/shadow

# Escalation (should fail)
su root
sudo ls /
apt-get update

# Docker escape (socket not mounted)
ls /var/run/docker.sock

# Mount new filesystems (should fail)
mount -t proc proc /mnt
```

## Tests

```bash
npx jest --testPathPatterns='mcp_filesystem_client'
```

Unit tests cover:
- `buildDockerTransportConfig` — volume mounts, `--network none`, image selection
- `translateHostToContainer` / `translateContainerToHost` — including overlapping prefix edge cases
- `translateToolArgs` — single `path` and `paths` array translation
- `translateToolResult` — container path replacement in result text
- Readonly mode — default-on blocking, all write tools blocked, reads allowed, opt-out with `readonly: false`
- Integration tests — `read_file`, `list_directory`, tool filtering, error handling (via bare process)
