# Yaffo UI Test Framework

AI-augmented UI testing framework using Playwright + MCP (Model Context Protocol) with self-healing capabilities.

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                            TEST WORKFLOW                                     │
│                                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌────────────┐  │
│  │   DEFINE     │───▶│   GENERATE   │───▶│   EXECUTE    │───▶│   HEAL     │  │
│  │  (specs/)    │    │(generated_   │    │ (playwright) │    │  (triage   │  │
│  │              │    │  tests/)     │    │              │    │   + fix)   │  │
│  └──────────────┘    └──────────────┘    └──────────────┘    └────────────┘  │
│                                                                              │
│  Human writes        AI generates        Deterministic       AI triages      │
│  high-level specs    Playwright code     test execution      then fixes      │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Layer 1: Test Definition (specs/)

High-level, human-readable test specifications in YAML format. These describe **what** to test, not **how**.

```yaml
# specs/face_assignment.yaml
feature: face_assignment
description: User is shown all the faces that have not been assigned to people
tags:
  - smoke
  - core
context:
  - tag: face_assignment-template
    path: templates/faces/index.html
    description: Main template for face assignment
data:
  - Faces 1,11,13,18,26,37,41 belong to person Obama
scenarios:
  - name: face_assignment_can_be_done
    goal: Should be able to assign faces to people
    priority: high
    steps:
      - Create a person named 'Obama' if needed
      - Select Obama from the 'Assign to Person' dropdown
      - Click on face 1
      - Click the Assign Selected button
    verify:
      - Face 1 is removed from the view
      - Success message is displayed
    cleanup:
      - Delete person Obama
```

### Layer 2: Code Generation (generated_tests/)

AI-generated Playwright test scripts. Created from specs using an LLM with MCP tool access (filesystem + Playwright browser).

- Each feature gets its own directory under `generated_tests/{feature}/`
- Generated `.spec.ts` files and a `{feature}.json` metadata file
- A `memories/` subdirectory stores investigation notes from the AI
- Test result history tracked in `{feature}.history.json`

See [Generated Tests](./lib/test_generator/README.md) for more details.

### Layer 3: Execution (Playwright)

Standard Playwright test execution via `@playwright/test`:
- Chromium browser (headless by default)
- Screenshot on failure, video retained on failure
- HTML + JSON reporters
- Configurable base URL and isolated Flask server

### Layer 4: Failure Analysis & Self-Healing (lib/)

When tests fail, the auto-healer runs a **two-phase** process using a single model session:

```
┌─────────────────────────────────────────────────────────────────┐
│              PHASE 1: TRIAGE (Classification)                   │
│                                                                 │
│  AI investigates using filesystem + Playwright browser tools,   │
│  then classifies the failure:                                   │
│                                                                 │
│  ┌───────────────────────────────────────────────────────┐      │
│  │  test_code_defect                                     │      │
│  │  • Wrong selectors, logic errors, missing waits       │      │
│  │  • Action: proceed to Phase 2 (fix)                   │      │
│  └───────────────────────────────────────────────────────┘      │
│  ┌───────────────────────────────────────────────────────┐      │
│  │  application_regression                               │      │
│  │  • The test is correct, the app has a real bug        │      │
│  │  • Action: record failure, exit with error code       │      │
│  └───────────────────────────────────────────────────────┘      │
│  ┌───────────────────────────────────────────────────────┐      │
│  │  environment_instability                              │      │
│  │  • Flaky infra, missing test data, timing issues      │      │
│  │  • Action: record failure, exit with error code│      │      │
│  └───────────────────────────────────────────────────────┘      │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│              PHASE 2: FIX (same model session)                  │
│                                                                 │
│  The model retains all investigation context from triage and    │
│  generates corrected test code. Both phases share a single      │
│  iteration budget (max 50 API calls).                           │
└─────────────────────────────────────────────────────────────────┘
```

Test run history (last 5 results per feature) is recorded in `{feature}.history.json` and provided to the model for trend analysis.

## Folder Structure

```
yaffo_ui_tests/
├── README.md
├── package.json
├── playwright.config.ts
├── tsconfig.json
├── jest.config.js
├── .env / .env.example
│
├── specs/                           # Human-written YAML test specs
├── generated_tests/                 # AI-generated Playwright tests
│   ├── {SPEC}/
│   │   ├── face-assignment.spec.ts
│   │   ├── {SPEC}.json     # Generation metadata
│   │   ├── {SPEC}.history.json  # Test run history
│   │   ├── face-assignment.triage_analysis.json  # Failure triage, one per spec file
│   │   └── memories/               # AI investigation notes
├── lib/                             # Framework library code
│   ├── test_generator/
│   │   ├── index.ts                 # CLI entry: generate tests from specs
│   │   └── prompt/                  # Generation prompt builder
│   ├── model_clients/               # Implementations for AI Model Clients
│   ├── tool_providers/              # Implementation for ToolProviders 
│   ├── services/                    #
│   └── __tests__/                        # Unit tests (Jest)
├── docker/
│   └── mcp-filesystem/              # Dockerized MCP filesystem server
├── test_data/                       # Test data for creating isolated test environemtn
└── .playwright/                     # Playwright browser cache
```

## Installation

### Prerequisites

- Node.js 18+
- Yaffo application source code (expected at `../../yaffo` relative to this directory)
- An API key for the model provider you use: Anthropic (default), Google (Gemini), OpenAI (GPT), DeepSeek, Moonshot AI (Kimi), or xAI (Grok)
- Docker — required by `npm run generate`, optional elsewhere. See [Docker](#docker) below.

### Setup

```bash
cd yaffo_ui_tests

# Install dependencies
npm install

# Install Playwright browsers
npx playwright install

# Set up environment variables
cp .env.example .env
# Edit .env with your API keys
```

## Usage

### 1. Define a Test Spec

Create a YAML file in `specs/`:

```yaml
feature: my_feature
description: Brief description of what this tests
tags:
  - smoke

context:
  - tag: my-template
    path: templates/my_feature/index.html
    description: Main template for this feature

data:
  - Any test data hints for the AI

scenarios:
  - name: scenario_name
    goal: What this scenario verifies
    priority: high
    steps:
      - Step 1 in natural language
      - Step 2 in natural language
    verify:
      - Expected outcome 1
      - Expected outcome 2
    cleanup:
      - Cleanup step (optional)
```

### 2. Generate Playwright Tests

```bash
# Generate tests from a spec (uses claude-sonnet-5 by default)
npm run generate specs/my_feature.yaml

# Generate and run tests in an isolated environment
npm run generate:test specs/my_feature.yaml

# Generate with a specific model
npm run generate:test -- specs/my_feature.yaml -m gemini-2.5-pro

# Generate with Gemini shortcut
npm run generate:test:gemini specs/my_feature.yaml
```

### 3. Run Tests

```bash
# Start the seeded isolated app manually
npm run isolatedEnvironment:start

# Start it with the public-demo boundary (instance A uses the source role)
npm run isolatedEnvironment:start -- --demo

# Start two demo instances (A is source, B is receiver)
npm run isolatedEnvironment:start:sharing -- --demo

# Run all generated tests (against an already-running environment)
npm test

# Run in headed mode (see browser)
npm run test:headed

# Run with Playwright UI mode
npm run test:ui

# Run unit tests
npm run test:unit
```

#### Per-spec runs with the seed cache

CI runs each spec file in its own isolated environment restored from a **seed
cache** — the seeded data dir (indexing, faces, labels, models) built once and
reused, so the expensive pipeline runs only when its inputs change. You can run
the same way locally:

```bash
# Build the seed cache once (real indexing/labeling; seeds A + B). Re-run only
# after changing fixtures, the seed script, or indexing/model code.
npm run seed:build

# Run one spec against its own environment restored from the cache. The project
# (chromium/sharing) and whether a peer is needed are derived from the path.
npm run test:spec -- generated_tests/albums/albums.spec.ts
npm run test:spec -- generated_tests/sharing/sharing.spec.ts

# Skip the cache and seed inline instead (no seed:build needed):
npm run test:spec -- generated_tests/albums/albums.spec.ts --fresh
```

`test:spec` starts the environment, runs the single spec, and tears it down.
Its report lands in `reports/<spec-id>/` (e.g. `reports/albums__albums/`), the
same per-spec layout CI uploads.

### 4. Self-Heal Failing Tests

```bash
# Auto-heal a feature: runs every generated test for the spec, heals failures
npm run test:heal specs/face_assignment.yaml

# With custom port for isolated server
npm run test:heal -- specs/my_feature.yaml -p 5002

# Reuse the seed cache instead of seeding inline (run `npm run seed:build` first)
npm run test:heal -- specs/my_feature.yaml --preseeded
```

The healer will:
1. Start an isolated Flask environment (two instances for a `sharing` spec)
2. Run the test to capture failures
3. **Triage** the failure (classify root cause using tools)
4. **Fix** if it's a test code defect (reusing investigation context)
5. Validate the fix (type check + re-run)

## Configuration

### Environment Variables

Create a `.env` file (see `.env.example`):

```bash
# Required for Claude models
ANTHROPIC_API_KEY=sk-ant-...

# Required for Gemini models
GOOGLE_GENERATIVE_AI_API_KEY=...

# Required for GPT models
OPENAI_API_KEY=sk-...

# Required for DeepSeek models
DEEPSEEK_API_KEY=sk-...

# Required for Kimi models (Moonshot AI)
MOONSHOT_API_KEY=sk-...

# Required for Grok models (xAI)
XAI_API_KEY=xai-...

# Model alias generation and healing use when no --model flag is given
# (any alias from the Supported Models table; default: claude-sonnet-5)
MODEL_ALIAS=claude-sonnet-5

# Model-turn budget per test file for auto-heal (default: 50); also
# settable per run with --max-iterations
HEAL_MAX_ITERATIONS=50

# Application base URL (default: http://127.0.0.1:5001)
BASE_URL=http://127.0.0.1:5001
```

### Playwright Configuration

See `playwright.config.ts`:
- Test directory: `./generated_tests`
- Browser: Chromium only
- Timeout: 5 seconds per test
- Screenshot on failure, video retained on failure
- HTML + JSON reporters
- Auto-starts Flask server if `BASE_URL` is not set

## CLI Commands

| Command | Description |
|---------|-------------|
| `npm run generate <spec>` | Generate Playwright test from YAML spec |
| `npm run generate:test <spec>` | Generate + run in isolated environment |
| `npm run generate:test:gemini <spec>` | Generate with Gemini 2.5 Pro |
| `npm test` | Run all generated tests |
| `npm run test:unit` | Run Jest unit tests |
| `npm run test:ui` | Run tests with Playwright UI |
| `npm run test:headed` | Run tests in headed browser |
| `npm run seed:build` | Build the seed cache (seeds A + B once) for `--preseeded` runs |
| `npm run test:spec -- <spec>` | Run one spec in its own cache-restored environment |
| `npm run validate:specs` | Verify all `specs/*.yaml` are valid YAML |
| `npm run isolatedEnvironment:start [-- --demo]` | Start a seeded isolated app, optionally in source demo mode |
| `npm run isolatedEnvironment:start:sharing [-- --demo]` | Start isolated A/B apps, optionally as source/receiver demos |
| `npm run test:heal <spec> [--preseeded]` | Auto-heal a feature's failing tests (YAML spec path) |
| `npm run logs` | Browse AI model API logs |
| `npm run typecheck` | TypeScript type check |
| `npm run docker:build:mcp-filesystem` | Build MCP filesystem Docker image |

## Continuous integration

Two workflows drive CI (`.github/workflows/`):

**`playwright.yml`** — one environment per spec file:

1. `validate-specs` — every `specs/*.yaml` parses (`npm run validate:specs`).
2. `list-specs` — enumerates spec files into a fan-out matrix (`scripts/list_specs.ts`)
   and computes the seed cache key.
3. `seed-cache` — builds the seed once (real indexing/faces/labels for A + B) and
   stores it via `actions/cache`, keyed on the fixtures + seed script + indexing /
   model code. It only rebuilds when those inputs change.
4. `playwright` — a matrix job **per spec file**: restores the seed cache, starts
   that spec's own isolated environment (`--preseeded`, plus `--peer` for sharing
   specs), and runs just that spec with the derived project. Each leg uploads its
   report as `playwright-reports-<spec-id>`.

Because every spec gets a fresh, isolated instance, cross-file state interference
is impossible and the core specs run in parallel across jobs.

**`playwright-auto-heal.yml`** — fans out the same way. On a failed Playwright
run it downloads the per-spec reports, builds a matrix of only the **failed**
specs (`scripts/failed_spec_matrix.ts`), heals each one in its own cache-restored
environment (`heal_test.ts --preseeded`), then collects the per-spec patches into
a single pull request.

Each heal writes a machine-readable assessment (`heal_test.ts --assessment-out`)
with its classification (`test_code_defect` / `application_regression` /
`environment_instability`), which is published to the job summary, summarized in
the PR body, and — for `application_regression` (a real app bug, no fix) — filed
as a GitHub issue. Because artifacts expire (90-day max), each run's assessments
are also archived permanently as an asset on a rolling `auto-heal-history`
prerelease.

The seed cache path is pinned with `YAFFO_SEED_CACHE_ROOT` so the build and
restore jobs agree on the absolute location — required because the seeded
database stores absolute media paths (see `seedCacheDir` in
`lib/services/isolated_runner.ts`). Run the same flow locally with
`npm run seed:build` then `npm run test:spec -- <spec>`.

## Spec File Format

### Full Schema

```yaml
feature: string              # Unique feature identifier (required)
description: string          # Human-readable description (required)
tags:                        # Optional tags for filtering
  - smoke
  - regression

context:                     # Source code context hints for the AI
  - tag: string              # Reference tag name
    path: string             # Template/source file path (path or attribute required)
    attribute: string        # HTML attribute to search for
    description: string      # What this context provides

preconditions:               # Optional setup requirements
  - condition 1

data:                        # Optional test data hints
  - Faces 1,11,13 belong to person Obama

scenarios:
  - name: string             # Unique scenario name (required)
    goal: string             # What this scenario verifies (required)
    priority: high|medium|low  # Default: medium

    steps:                   # Natural language steps (required)
      - Navigate to page
      - Click element
      - Enter text

    verify:                  # Expected outcomes (required)
      - Element is visible
      - Success message shown

    cleanup:                 # Optional teardown steps
      - Delete created data
```

## Supported Models

| Provider | Model | Alias |
|----------|-------|-------|
| Anthropic | Claude Opus 5 | `claude-opus-5` |
| Anthropic | Claude Sonnet 5 | `claude-sonnet-5` |
| Anthropic | Claude Haiku 4.5 | `claude-haiku-4-5` |
| Google | Gemini 2.0 Flash | `gemini-2.0-flash` |
| Google | Gemini 2.5 Flash | `gemini-2.5-flash` |
| Google | Gemini 2.5 Pro | `gemini-2.5-pro` |
| OpenAI | GPT-5.6 Sol | `gpt-5.6-sol` |
| OpenAI | GPT-5.6 Terra | `gpt-5.6-terra` |
| OpenAI | GPT-5.6 Luna | `gpt-5.6-luna` |
| DeepSeek | DeepSeek V4 Pro | `deepseek-v4-pro` |
| DeepSeek | DeepSeek V4 Flash | `deepseek-v4-flash` |
| Moonshot AI | Kimi K3 | `kimi-k3` |
| xAI | Grok 4.5 | `grok-4.5` |
| xAI | Grok 4.3 | `grok-4.3` |

Anthropic models support native structured output and prompt caching. Generation defaults to `claude-sonnet-5`, healing uses `claude-sonnet-5`.

## Docker

Docker does two jobs here: it confines the parts of this harness that hand filesystem
access to a model, and it pins screenshot rendering so a guide image does not depend on
which machine captured it.

### What needs it

| Uses Docker | Why |
|---|---|
| `npm run generate` | Passes `useDocker: true`, so the MCP filesystem server the model reads source through runs in a container with the repo mounted **read-only** and `--network none` |
| `npm run test:heal` | No. Runs the filesystem server directly via `npx` |
| `npm run docs:capture:docker` | Runs the browser half in a container so screenshots are reproducible |
| `npm run docs:capture` | No. Captures on the host — fine locally, but see the drift below |

Everything except `generate` and `docs:capture:docker` works without Docker installed.

### Why docs capture is containerized when the Playwright suite is not

The test suite confines generated specs with `sandbox-exec` (macOS) or `bwrap` (Linux),
which is cheaper and enough for *safety*. Docs capture has a second requirement the
tests do not: its output is a committed image, compared per-pixel on the next run, so it
has to be reproducible.

macOS and Linux disagree on font metrics. That changes line wrapping, which moves
layout. Measured on `library-basics/browsing-filtering` against the same sandbox:

| | `gallery-home` | `gallery-filter-sidebar` |
|---|---|---|
| container, run A vs run B | **0 px differ** | **0 px differ** |
| container vs macOS host | 1392×**777** vs 1392×**782** | 312×**1326** vs 312×**1359** |

Two container runs are byte-identical. The same page captured on the host is a
different size, so every CI run would report every shot as reframed and the comparison
would be noise. The container pins the whole rendering stack — Chromium build, fonts,
freetype — so the two agree.

Encoding and comparison stay on the host either way: they use Pillow and NumPy from the
project virtualenv, which is not in the Playwright image and should not be.

### Install

**macOS** — [Docker Desktop](https://www.docker.com/products/docker-desktop/), or
[Rancher Desktop](https://rancherdesktop.io/) with the container engine set to
**dockerd (moby)** rather than containerd. Either way the daemon has to be *running*,
not merely installed:

```bash
docker info
```

A `Cannot connect to the Docker daemon` or `dial unix /var/run/docker.sock` error means
the desktop app is not started.

**Linux** — Docker Engine from your distribution, plus your user in the `docker` group so
the harness can talk to the socket without `sudo`:

```bash
sudo usermod -aG docker "$USER"    # log out and back in for this to take effect
```

### Build the images

Both are local and have to be built once:

```bash
npm run docker:build:mcp-filesystem
npm run docker:build:docs-capture
```

`yaffo-mcp-filesystem` is `node:22-slim` with `@modelcontextprotocol/server-filesystem`
pinned, running as a non-root user. Rebuild it if that pin changes.

`yaffo-docs-capture` is the official Playwright image with this project's
`node_modules` installed inside it, pinned to the `@playwright/test` version in
`package.json` — **bump both together**, or the browser in the image stops matching the
one the harness expects. `node_modules` is built into the image rather than mounted
because the host's is compiled for darwin and will not execute on Linux; the run masks
the mounted one with an anonymous volume.

Verify:

```bash
docker image inspect yaffo-mcp-filesystem:latest yaffo-docs-capture:latest --format '{{.Id}}'
```

### Running a containerized capture

The container reaches the app through `host.docker.internal`, and **a container cannot
see the host's loopback**. The sandbox therefore has to bind beyond `127.0.0.1`:

```bash
YAFFO_SANDBOX_HOST=0.0.0.0 npm run isolatedEnvironment:start
```

```bash
npm run docs:capture:docker
npm run docs:capture:docker -- --promote library-basics/browsing-filtering
```

`YAFFO_SANDBOX_HOST` defaults to `127.0.0.1` and is opt-in because `0.0.0.0` puts the
sandbox on your LAN. `--network host` is not an alternative on macOS: it joins the Linux
VM's network namespace, not your Mac's.

The container gets the repo mounted **read-only** with exactly one writable hole, at
`user_doc_automation/.staging`. Walkthroughs are model-generated code and staging is the
only place they have any business writing; images are promoted into `docs/guide/` by the
host, after they have been compared. Its environment is an allowlist (see
`lib/user_doc_automation/env.ts`) that carries no provider key and, deliberately, no
`DOCKER_HOST` — the daemon socket is root on the host, and handing it to generated code
would make the container pointless.

### How the filesystem container is run

Worth knowing when debugging a path the model claims it cannot read. Each allowed
directory is mounted at `/data/0`, `/data/1`, … in the order given, and the client
translates paths in both directions, so the model sees container paths while the harness
works in host paths.

```
docker run --rm -i --network none -v <hostDir>:/data/0:ro … yaffo-mcp-filesystem:latest /data/0 …
```

- `--network none` — the server has no reason to reach the network, and the code it
  serves was written by a model.
- `:ro` — mounts are read-only unless `readonly: false` is passed. Note that
  `getTools()` filters out the write tools **regardless** of that flag, so the model is
  never offered one; generated artifacts come back as its answer and the harness writes
  them.

### Troubleshooting

| Symptom | Cause |
|---|---|
| `dial unix /var/run/docker.sock` | Daemon not running — start Docker Desktop |
| `Unable to find image 'yaffo-…:latest'` | Build it — see [Build the images](#build-the-images) |
| Capture fails with `ECONNREFUSED` | Sandbox bound to loopback; restart it with `YAFFO_SANDBOX_HOST=0.0.0.0` |
| Every shot reports `reframed` | Baselines were captured on the host; re-promote once from a container run |
| Model reports a file missing that exists | A path outside `allowedDirectories` is not mounted, so it does not exist inside the container |
| `permission denied` on the socket (Linux) | User not in the `docker` group |

## MCP Tool Providers

The framework gives the AI model access to three tool providers via MCP:

| Tool Provider | Purpose |
|---------------|---------|
| **Filesystem** | Read-only access to Yaffo source code (templates, routes, models) |
| **Playwright** | Browser automation — navigate, click, fill forms, take screenshots |
| **Memory** | Local scratchpad for the AI to store investigation notes |

## Development

### Running Unit Tests

```bash
npm run test:unit
```

Tests are in `lib/__tests__/` using Jest with `ts-jest`.

### Type Checking

```bash
npm run typecheck
```

### Adding a New Feature Test

1. Create spec: `specs/new_feature.yaml`
2. Generate test: `npm run generate:test specs/new_feature.yaml`
3. Review generated code in `generated_tests/new_feature/`
4. If tests fail, heal: `npm run test:heal generated_tests/new_feature/new-feature.spec.ts`

## References

- [Playwright Documentation](https://playwright.dev/)
- [Playwright MCP Server](https://github.com/microsoft/playwright-mcp)
- [Anthropic Claude API](https://docs.anthropic.com/)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Vercel AI SDK](https://sdk.vercel.ai/)
