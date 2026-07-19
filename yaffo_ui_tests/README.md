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
│   │   ├── {SPEC}.triage_analysis.json  # Analysis from test failure analysis
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
- Anthropic API key (required) and/or Google Generative AI API key (for Gemini models)

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
# Generate tests from a spec (uses claude-sonnet-4-5 by default)
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

# Run all generated tests
npm test

# Run in headed mode (see browser)
npm run test:headed

# Run with Playwright UI mode
npm run test:ui

# Run unit tests
npm run test:unit
```

### 4. Self-Heal Failing Tests

```bash
# Auto-heal a specific test file
npm run test:heal generated_tests/face_assignment/face-assignment.spec.ts

# With custom port for isolated server
npm run test:heal -- generated_tests/my_feature/my-test.spec.ts -p 5002
```

The healer will:
1. Start an isolated Flask environment
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
| `npm run isolatedEnvironment:start [-- --demo]` | Start a seeded isolated app, optionally in source demo mode |
| `npm run isolatedEnvironment:start:sharing [-- --demo]` | Start isolated A/B apps, optionally as source/receiver demos |
| `npm run test:heal <test>` | Auto-heal a failing test |
| `npm run logs` | Browse AI model API logs |
| `npm run typecheck` | TypeScript type check |
| `npm run docker:build:mcp-filesystem` | Build MCP filesystem Docker image |

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
| Anthropic | Claude Opus 4.5 | `claude-opus-4-5` |
| Anthropic | Claude Sonnet 4.5 | `claude-sonnet-4-5` |
| Anthropic | Claude Haiku 4.5 | `claude-haiku-4-5` |
| Google | Gemini 2.0 Flash | `gemini-2.0-flash` |
| Google | Gemini 2.5 Flash | `gemini-2.5-flash` |
| Google | Gemini 2.5 Pro | `gemini-2.5-pro` |

Anthropic models support native structured output and prompt caching. Generation defaults to `claude-sonnet-4-5`, healing uses `claude-sonnet-4-5`.

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
