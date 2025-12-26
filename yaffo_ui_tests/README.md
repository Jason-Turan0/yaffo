# Yaffo UI Test Framework

AI-augmented UI testing framework using Playwright + MCP (Model Context Protocol) with self-healing capabilities.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           TEST WORKFLOW                                      │
│                                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌───────────┐ │
│  │   DEFINE     │───▶│   GENERATE   │───▶│   EXECUTE    │───▶│  ANALYZE  │ │
│  │  (specs/)    │    │ (generated/) │    │ (playwright) │    │  (lib/)   │ │
│  └──────────────┘    └──────────────┘    └──────────────┘    └───────────┘ │
│                                                                              │
│  Human writes        AI generates        Deterministic       AI evaluates   │
│  high-level specs    Playwright code    test execution      failures        │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Layer 1: Test Definition (specs/)

High-level, human-readable test specifications in YAML format. These describe **what** to test, not **how**.

```yaml
# specs/photo_upload.yaml
feature: photo_upload
description: User can upload photos and see them in the gallery

preconditions:
  - User is logged in
  - Gallery page is accessible

scenarios:
  - name: upload_single_photo
    goal: Upload a single photo and verify it appears in gallery
    steps:
      - Navigate to the home page
      - Click the upload button
      - Select a test image file
      - Confirm the upload
    verify:
      - Photo thumbnail appears in gallery
      - Success notification is shown
      - Photo count increases by 1
```

### Layer 2: Code Generation (generated/)

AI-generated Playwright test scripts. These are created from specs using Claude + Playwright MCP.

- Scripts are deterministic and version-controlled
- Each generated file references its source spec
- Metadata tracks generation timestamp and DOM context hash

See [Generated Tests](./lib/test_generator/README.md) for more details
### Layer 3: Execution (Playwright)

Standard Playwright test execution:
- Headless or headed browser
- Parallel test execution
- Screenshot/video on failure
- Network request logging

### Layer 4: Failure Analysis (lib/)

When tests fail, the AI analyzer evaluates the failure:

```
┌─────────────────────────────────────────────────────────────┐
│                  FAILURE CLASSIFICATION                      │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  REAL REGRESSION                                     │    │
│  │  • Feature is broken                                 │    │
│  │  • Action: FAIL test, create bug report              │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  FLAKY / TIMING                                      │    │
│  │  • Race condition, element not ready                 │    │
│  │  • Action: Retry, suggest wait strategies            │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  SUPERFICIAL CHANGE                                  │    │
│  │  • Selector changed, text updated                    │    │
│  │  • Feature still works                               │    │
│  │  • Action: Auto-heal, warn about outdated tests      │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

## Folder Structure

```
yaffo_ui_tests/
├── README.md                 # This file
├── package.json              # Node.js dependencies
├── playwright.config.ts      # Playwright configuration
├── tsconfig.json             # TypeScript configuration
│
├── specs/                    # Human-written test specifications
│   ├── photo_upload.yaml     # Example: photo upload feature
│   ├── face_tagging.yaml     # Example: face tagging feature
│   └── ...
│
├── generated/                # AI-generated Playwright tests
│   ├── photo_upload.spec.ts  # Generated from specs/photo_upload.yaml
│   ├── face_tagging.spec.ts  # Generated from specs/face_tagging.yaml
│   └── ...
│
├── lib/                      # Framework library code
│   ├── index.ts          # Spec → Playwright code generation
│   ├── analyzer.ts           # Failure analysis with AI
│   ├── healer.ts             # Self-healing logic
│   ├── mcp_filesystem_client.ts         # Playwright MCP integration
│   └── index.types.ts              # TypeScript type definitions
│
├── fixtures/                 # Test data and fixtures
│   ├── images/               # Test images for upload
│   ├── database/             # Database seed scripts
│   └── auth.ts               # Authentication fixtures
│
├── reports/                  # Test execution reports
│   ├── results/              # JSON test results
│   ├── screenshots/          # Failure screenshots
│   ├── videos/               # Test recordings
│   └── healing-log.json      # Record of auto-healed tests
│
└── .playwright/              # Playwright browser cache
```

## Installation

### Prerequisites

- Node.js 18+
- Yaffo application running locally (default: http://localhost:5000)
- Anthropic API key for AI features

### Setup

```bash
cd yaffo_ui_tests

# Install dependencies
npm install

# Install Playwright browsers
npx playwright install

# Set up environment variables
cp .env.example .env
# Edit .env with your ANTHROPIC_API_KEY
```

## Usage

### 1. Define a Test Spec

Create a YAML file in `specs/`:

```yaml
# specs/my_feature.yaml
feature: my_feature
description: Brief description of what this tests

preconditions:
  - Any setup required

scenarios:
  - name: scenario_name
    goal: What this scenario verifies
    steps:
      - Step 1 in natural language
      - Step 2 in natural language
    verify:
      - Expected outcome 1
      - Expected outcome 2
```

### 2. Generate Playwright Tests

```bash
# Generate tests from a single spec
npm run generate specs/my_feature.yaml

# Generate all tests from specs
npm run generate:all

# Generate with DOM context (crawls running app)
npm run generate specs/my_feature.yaml -- --with-context
```

### 3. Run Tests

```bash
# Run all tests
npm test

# Run specific test file
npm test -- generated/my_feature.spec.ts

# Run in headed mode (see browser)
npm test -- --headed

# Run with UI mode
npm run test:ui
```

### 4. Run with Self-Healing

```bash
# Run tests with AI failure analysis
npm run test:heal

# Analyze a specific failure
npm run analyze -- reports/results/my_feature.json
```

## Configuration

### Environment Variables

Create a `.env` file:

```bash
# Required for AI features
ANTHROPIC_API_KEY=sk-ant-...

# Application URL (default: http://localhost:5000)
BASE_URL=http://localhost:5000

# AI model selection
ANTHROPIC_MODEL=claude-sonnet-4-20250514

# Healing behavior
AUTO_HEAL_ENABLED=true
HEALING_CONFIDENCE_THRESHOLD=0.8
```

### Playwright Configuration

See `playwright.config.ts` for:
- Browser settings (Chromium, Firefox, WebKit)
- Timeouts and retries
- Screenshot/video capture
- Report generation

## CLI Commands

| Command | Description |
|---------|-------------|
| `npm run generate <spec>` | Generate Playwright test from spec |
| `npm run generate:all` | Generate all tests from specs/ |
| `npm test` | Run all generated tests |
| `npm run test:ui` | Run tests with Playwright UI |
| `npm run test:heal` | Run tests with self-healing enabled |
| `npm run analyze <result>` | Analyze a test failure |
| `npm run regenerate <spec>` | Regenerate outdated test |
| `npm run validate` | Validate all specs syntax |

## Spec File Format

### Full Schema

```yaml
feature: string              # Unique feature identifier
description: string          # Human-readable description
tags:                        # Optional tags for filtering
  - smoke
  - regression

preconditions:               # Setup requirements
  - condition 1
  - condition 2

scenarios:
  - name: string             # Unique scenario name
    goal: string             # What this scenario verifies
    priority: high|medium|low

    steps:                   # Natural language steps
      - Navigate to page
      - Click element
      - Enter text
      - Wait for response

    verify:                  # Expected outcomes
      - Element is visible
      - Text matches pattern
      - API returns success

data:                        # Test data
  users:
    - username: test_user
      password: test_pass
  files:
    - path: fixtures/images/test.jpg
      type: image/jpeg
```

### Step Keywords

The generator recognizes these action keywords:

| Keyword | Example | Generated Code |
|---------|---------|----------------|
| Navigate | "Navigate to home page" | `page.goto('/')` |
| Click | "Click the upload button" | `page.click('[data-testid="upload"]')` |
| Enter/Type | "Enter 'test' in search" | `page.fill('[name="search"]', 'test')` |
| Select | "Select 'Option A' from dropdown" | `page.selectOption(...)` |
| Wait | "Wait for loading to complete" | `page.waitForSelector(...)` |
| Verify/Assert | "Verify success message shown" | `expect(page.locator(...)).toBeVisible()` |

## Self-Healing Workflow

When a test fails:

```
1. Test execution fails
         │
         ▼
2. Collect failure context:
   • Error message
   • Screenshot
   • DOM snapshot
   • Expected selector
   • Original spec intent
         │
         ▼
3. Send to AI analyzer
         │
         ▼
4. AI classifies failure:
   ├─▶ REAL BUG ──────▶ Fail test, generate bug report
   │
   ├─▶ FLAKY ─────────▶ Retry with wait strategies
   │                    Log to flaky-tests.json
   │
   └─▶ SUPERFICIAL ───▶ Generate healing patch
                        Apply patch, re-run test
                        Log to healing-log.json
                        Warn: "Test outdated, regenerate"
```

### Healing Log Format

```json
{
  "timestamp": "2025-01-15T10:30:00Z",
  "test": "photo_upload.spec.ts",
  "scenario": "upload_single_photo",
  "classification": "superficial",
  "original_selector": "[data-testid='upload-btn']",
  "healed_selector": "[data-testid='photo-upload-button']",
  "confidence": 0.92,
  "recommendation": "Regenerate test from spec to update selectors"
}
```

## Integration with MCP

This framework uses [Playwright MCP](https://github.com/microsoft/playwright-mcp) for AI-browser interaction:

```typescript
// lib/mcp_filesystem_client.ts
import { MCPClient } from '@anthropic-ai/mcp';

// MCP provides structured DOM access without screenshots
// AI can query accessibility tree, find elements, execute actions
```

### MCP vs Vision-Based Approaches

| Aspect | MCP (This Framework) | Vision-Based |
|--------|---------------------|--------------|
| Speed | Fast (structured data) | Slow (image processing) |
| Accuracy | High (exact selectors) | Variable (visual matching) |
| Cost | Lower (text only) | Higher (image tokens) |
| Debugging | Easy (DOM inspection) | Harder (visual) |

## Development

### Adding a New Feature Test

1. Create spec: `specs/new_feature.yaml`
2. Generate test: `npm run generate specs/new_feature.yaml`
3. Review generated code in `generated/new_feature.spec.ts`
4. Run test: `npm test -- generated/new_feature.spec.ts`
5. Commit both spec and generated test

### Updating When UI Changes

```bash
# Option 1: Regenerate from spec
npm run regenerate specs/affected_feature.yaml

# Option 2: Run with healing to see what changed
npm run test:heal -- generated/affected_feature.spec.ts
```

### Writing Custom Fixtures

```typescript
// fixtures/auth.ts
import { test as base } from '@playwright/test';

export const test = base.extend({
  authenticatedPage: async ({ page }, use) => {
    await page.goto('/login');
    await page.fill('[name="username"]', 'test_user');
    await page.fill('[name="password"]', 'test_pass');
    await page.click('[type="submit"]');
    await page.waitForURL('/home');
    await use(page);
  },
});
```

## Roadmap

- [x] Folder structure and architecture
- [ ] Core library implementation (generator, analyzer, healer)
- [ ] MCP client integration
- [ ] CLI tooling
- [ ] Example specs for Yaffo features
- [ ] CI/CD integration
- [ ] Visual regression testing
- [ ] API mocking support

## References

- [Playwright Documentation](https://playwright.dev/)
- [Playwright MCP Server](https://github.com/microsoft/playwright-mcp)
- [Anthropic Claude API](https://docs.anthropic.com/)
- [Model Context Protocol](https://modelcontextprotocol.io/)