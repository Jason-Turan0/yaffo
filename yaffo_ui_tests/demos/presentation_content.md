# AI-Augmented UI Testing: From Spec to Self-Healing Tests

Presentation content for engineering leadership (~15-20 minutes)

---

## Slide 1: Title

**AI-Augmented UI Testing**
From Spec to Self-Healing Tests

*A technique demonstration for automated test generation and maintenance*

> Speaker notes: This is a POC I've been building on my own time to demonstrate a technique. The demo app is a personal project, but the approach is designed for the kind of web applications we build at Capital One. The goal today is to show what's possible and discuss how this could fit into our development workflow.

---

## Slide 2: The Problem

**UI tests are expensive to maintain**

- Writing Playwright/Cypress tests is slow: selectors, waits, assertions, edge cases
- A single UI refactor can break dozens of tests
- When tests break, someone has to figure out: is it a real bug, or just a stale test?
- Most teams either underinvest in UI tests or spend too much time babysitting them

> Speaker notes: This is a universal problem. Every team that maintains a UI test suite deals with it. You update a component, rename a CSS class, or restructure a page - and suddenly the test suite is red. The real cost isn't writing the initial tests, it's keeping them green sprint after sprint. And the triage problem compounds it: when a test fails in CI, someone has to context-switch, read the error, check the app, and decide if it's a real bug or a flaky test.

---

## Slide 3: What If?

**What if tests could...**

- Be generated from plain-English descriptions
- Investigate their own failures using real tools
- Distinguish between real bugs and broken tests
- Fix themselves when the test code is the problem
- Open a PR for human review instead of blocking the pipeline

> Speaker notes: This isn't hypothetical. I've built a working proof of concept that demonstrates all of this. The demo app is a personal Flask project, but the technique is framework-agnostic - it works by reading whatever source code and UI your app has. The important thing is the pattern, not the specific app.

---

## Slide 4: The Workflow

```
DEFINE  ───>  GENERATE  ───>  EXECUTE  ───>  HEAL
(YAML)       (Playwright)   (Browser)      (Triage + Fix)
```

**Four layers, clear separation of concerns:**

1. Humans write **what** to test (YAML specs)
2. AI generates **how** to test it (Playwright code)
3. Tests run deterministically (standard Playwright)
4. When tests fail, AI triages and fixes them

> Speaker notes: The key insight is the separation. Humans stay in the "what" layer - natural language specs. The AI handles the "how" - selectors, waits, assertions. And when things break, the AI has the tools to investigate and fix, not just guess. This pattern would work the same way against a React app, an Angular app, or any web application our teams build.

---

## Slide 5: Layer 1 - Human-Readable Specs

**What a test definition looks like:**

```yaml
feature: account_dashboard
description: User can view account summary and recent transactions

scenarios:
  - name: dashboard_shows_account_balance
    goal: Account balance is displayed correctly after login
    steps:
      - Navigate to the login page
      - Enter valid credentials and submit
      - Navigate to the account dashboard
    verify:
      - Account balance is visible
      - Recent transactions list is populated
      - Last updated timestamp is shown
```

> Speaker notes: Notice what's NOT here: no selectors, no CSS classes, no waitForSelector calls. Just intent. A QA engineer, a PM, or a developer can write this. The AI figures out the implementation details by reading the actual source code and interacting with the live app. This is a made-up example, but in the POC these specs generate real, working Playwright tests.

---

## Slide 6: Layer 2 - AI Code Generation

**How generation works:**

- AI reads the YAML spec
- Gets **scoped, read-only** access to app source code via MCP
- Browses the live app with a real browser (Playwright MCP)
- Generates TypeScript Playwright test files
- Validates: type-checks the code, runs the tests

**Tools the AI uses during generation:**
| Tool | Access Level | Purpose |
|------|-------------|---------|
| Filesystem | Read-only, scoped directories | Read templates, routes, components |
| Playwright Browser | Sandboxed test environment | Navigate, click, inspect the live app |
| Memory | Local scratchpad only | Store investigation notes across turns |

> Speaker notes: The model isn't just pattern-matching from a prompt. It's actually reading source code to find the real selectors, navigating the app to verify what it sees, and running the generated tests to confirm they pass. MCP - Model Context Protocol - is what gives the AI structured tool access. I'll talk more about the security model in a moment.

---

## Slide 7: Layer 3 - Standard Execution

**Tests run like any Playwright suite**

- Standard `npx playwright test` execution
- Chromium, headless
- Screenshots on failure, video on retry
- HTML + JSON reporting
- No AI involvement at runtime - pure deterministic execution

> Speaker notes: This is important. The generated tests are just normal Playwright tests. No AI at runtime, no flakiness from model calls, no API keys needed to run them. You can run them in CI exactly the way we run tests today. The AI is a developer tool for creation and maintenance, not a runtime dependency.

---

## Slide 8: Layer 4 - Self-Healing

**When a test fails, the AI investigates before fixing**

Two-phase process, single model session:

**Phase 1: Triage**
- AI reads the error, the test code, the spec
- Uses tools to inspect source code and browse the live app
- Classifies the failure into one of three categories

**Phase 2: Fix**
- Same session, all investigation context preserved
- Generates corrected test code
- Validates: type-check + re-run

> Speaker notes: The key design decision here is using a SINGLE model session for both phases. The triage phase does heavy investigation - reading files, clicking around the app, comparing what the test expects vs what actually happens. All that context carries over to the fix phase, so the model doesn't have to re-investigate. Both phases share a budget of 50 API calls total.

---

## Slide 9: Three Failure Classifications

| Classification | What it means | What happens |
|----------------|---------------|--------------|
| **test_code_defect** | Test is broken (wrong selector, bad wait, logic error) | AI fixes the test |
| **application_regression** | Test is correct, the app has a real bug | Fail the build - this is a real bug |
| **environment_instability** | Flaky infra, timing, missing data | Record and exit - investigate environment |

> Speaker notes: This is the most valuable part for our teams. Today when a test fails in CI, someone has to manually triage it. Is it a real bug? Is it flaky? Is the test outdated? The AI does that triage automatically, with access to the same tools a developer would use. If it's a real app regression, it says so and fails the build - it doesn't try to "fix" a passing test to match a broken app. That distinction is critical.

---

## Slide 10: Test Run History

**Trend analysis over time**

- Last 5 test results per feature tracked
- Provided to the model during triage
- Helps distinguish patterns:
  - Same test always fails = defect or regression
  - Intermittent failures = environment instability
  - Recently started failing = likely regression

> Speaker notes: This gives the AI temporal context that a human would naturally have. "This test was passing last week and just started failing" is a strong signal toward regression. "This test has failed 3 of the last 5 runs with different errors" suggests flakiness. The history file lives alongside the tests and gets committed to the repo.

---

## Slide 11: Security Model - Narrowly Scoped Access

**Principle: minimum necessary permissions for a narrowly defined task**

```
┌─────────────────────────────────────────────────────┐
│                 AI AGENT SCOPE                        │
│                                                       │
│  Task:  Generate or fix Playwright UI tests           │
│                                                       │
│  CAN:                                                 │
│    - Read source code (specified directories only)    │
│    - Browse a sandboxed test instance of the app      │
│    - Write test files (output directory only)         │
│    - Read/write its own scratchpad notes              │
│                                                       │
│  CANNOT:                                              │
│    - Write to application source code                 │
│    - Access production environments                   │
│    - Access secrets, credentials, or .env files       │
│    - Make network calls beyond the sandboxed app      │
│    - Execute arbitrary shell commands                 │
│    - Access databases directly                        │
│    - Modify CI/CD configuration                       │
│                                                       │
│  All tool calls are logged and auditable              │
└─────────────────────────────────────────────────────┘
```

> Speaker notes: This is how we'd want to deploy something like this at Capital One. The AI agent has a very narrow job: generate or fix UI test code. It gets exactly the permissions it needs and nothing more. Filesystem access is read-only and scoped to specific directories - it can read templates and routes but not write to them. The browser only connects to a sandboxed test environment, never production. Every tool call the model makes is logged to JSON files for full auditability. This is the opposite of "give the AI full access and hope for the best."

---

## Slide 12: How MCP Enforces Boundaries

**Model Context Protocol gives us explicit tool definitions**

- Each tool has a defined name, input schema, and scope
- The orchestrator controls which tools are available
- Filesystem MCP server enforces directory allowlists at the server level
- Playwright MCP connects only to the sandboxed test URL
- No shell access, no arbitrary code execution

**Example: Filesystem tool allowlist**
```
Allowed: /app/templates/, /app/routes/, /app/static/
Denied:  everything else (enforced by MCP server, not by prompt)
```

> Speaker notes: This is an important distinction. The access controls aren't just prompt instructions that the model might ignore. They're enforced at the tool server level. The MCP filesystem server physically cannot read files outside the allowlist. The Playwright MCP server is configured with a specific base URL for the sandboxed app. Even if the model tried to access something it shouldn't, the tool would reject the request. This is defense in depth - the model is told its scope, and the tools enforce that scope independently.

---

## Slide 13: The CI/CD Vision

**How this technique could work in our pipelines:**

```
  PR merged to main
       │
       ▼
  CI runs UI tests (standard Playwright)
       │
       ├── All pass ──────────────> Done
       │
       └── Some fail
            │
            ▼
       AI triage step (narrowly scoped agent)
            │
            ├── application_regression ──> Fail the build
            │                              Alert the team
            │
            ├── environment_instability ──> Flag as flaky
            │                               Log for investigation
            │
            └── test_code_defect ──> AI generates fix
                                     Opens a PR for review
                                     Team reviews + merges
```

**The human is always in the loop for code changes**

> Speaker notes: The ideal end state is NOT that the AI silently fixes tests in the background. It's that the AI does the investigation and proposes a fix, then a developer reviews and approves the change - just like any other PR. The AI eliminates the tedious part (triage + writing the fix), while the team retains control over what gets merged. For Capital One, this means the AI agent runs in a locked-down CI environment with scoped permissions, and the output is always a PR that goes through normal review.

---

## Slide 14: What This Means for Our Teams

**Cost of test creation - before and after:**

| | Traditional | AI-Augmented |
|---|---|---|
| Write test | 1-4 hours per scenario | 5-10 min YAML spec |
| Implementation | Manual: selectors, waits, assertions | Automated generation |
| Review | Code review of test code | Review spec intent + generated code |

**Cost of test maintenance - before and after:**

| | Traditional | AI-Augmented |
|---|---|---|
| Triage | 15-60 min per failure (manual) | Automated classification |
| Fix | Manual selector/assertion updates | AI proposes fix via PR |
| Confidence | "Is this a real bug?" | Classification with reasoning |

> Speaker notes: The biggest win isn't the initial generation - it's the ongoing maintenance. Every time a UI change breaks tests, the team currently loses developer hours to triage and repair. This technique turns that into an automated step that produces a PR. The developer time shifts from writing fixes to reviewing them.

---

## Slide 15: Technical Architecture

```
┌─────────────┐     ┌─────────────────┐     ┌──────────────┐
│  YAML Spec  │────▶│  Orchestrator   │────▶│  Model Client │
│             │     │  (generate/heal) │     │  (Claude/     │
└─────────────┘     └────────┬────────┘     │   Gemini)     │
                             │              └──────┬───────┘
                    ┌────────┴────────┐            │
                    │  Tool Providers  │◀───────────┘
                    │  (scoped access) │    (tool calls)
                    ├──────────────────┤
                    │ Filesystem (MCP) │  Read-only, allowlisted dirs
                    │ Playwright (MCP) │  Sandboxed test env only
                    │ Memory           │  Local scratchpad
                    └──────────────────┘
```

- TypeScript, Node.js, Playwright
- Model-agnostic (Anthropic Claude, Google Gemini)
- MCP for tool access with enforced boundaries
- Zod schema validation on all model outputs
- Full API call logging for auditability

> Speaker notes: The architecture is intentionally modular. Model clients are swappable, tool providers are pluggable, and the orchestrator handles the agentic loop. All API calls are logged to JSON for debugging, cost tracking, and audit. This could be adapted to work with whatever web framework our teams use - React, Angular, or anything that runs in a browser.

---

## Slide 16: Applicability to Capital One

**This technique is framework-agnostic. It works by:**

- Reading whatever source code your app has (React components, Angular templates, HTML)
- Browsing the live app through a standard browser
- Generating standard Playwright tests (which we already use)

**What would need to happen to adopt:**
- Define YAML specs for existing features (can be incremental)
- Configure filesystem access scope for the app's source tree
- Set up a sandboxed test environment (we likely already have this)
- Integrate the heal step into CI as a post-failure hook
- Route proposed fixes through normal PR review

> Speaker notes: The POC uses a Flask/Jinja app, but nothing about the technique is Flask-specific. The AI reads whatever source files you point it at and browses whatever URL you give it. For our React or Angular apps, it would read JSX/TSX components instead of Jinja templates. The Playwright tests it generates are the same either way. Adoption can be incremental - start with one feature, one team, and expand from there.

---

## Slide 17: What I've Demonstrated

**POC results:**

- 3 feature specs written, multiple test files generated per feature
- Self-healing has successfully:
  - Fixed broken selectors after UI changes
  - Correctly identified app regressions (refused to "fix" the test)
  - Classified environment instability separately from code defects
- Tested with both Anthropic (Claude) and Google (Gemini) models
- Full audit trail: every model API call logged with request/response

> Speaker notes: This is running against a real web application with complex interactive features - face recognition UI, photo management, filtering and pagination. Not a toy todo app. The technique handles real-world complexity.

---

## Slide 18: Key Takeaways

1. **Specs, not scripts** - humans define intent, AI writes implementation
2. **Tools, not guessing** - AI reads real source code and browses the real app
3. **Triage before fix** - distinguishes real bugs from broken tests
4. **Narrowly scoped** - read-only filesystem, sandboxed browser, no shell access
5. **Human in the loop** - AI proposes fixes via PR, team reviews and merges
6. **Standard execution** - generated tests are just Playwright, no AI at runtime

> Speaker notes: This isn't "AI writes some tests and hopes they work." The AI has a narrowly defined task, narrowly scoped permissions, and follows a structured process: investigate, classify, then propose. The human team stays in control of what code actually ships. The AI handles the tedious parts - writing boilerplate, triaging failures, proposing fixes - so developers can focus on reviewing intent rather than debugging selectors.

---

## Slide 19: Q&A

**The POC is available for hands-on exploration**

Happy to walk through any part of the architecture in detail, discuss how it could apply to a specific team's app, or talk about the security model further.

---

## Appendix: Demo Script (pre-recorded ~5 min video)

Since we can't call model APIs from within the firewall, a pre-recorded demo works best:

1. **Show the YAML spec** - walk through the natural language definition (~1 min)
2. **Show the generated Playwright test** - point out real selectors, waits, assertions (~1 min)
3. **Break a selector intentionally** - simulate a UI change
4. **Run the heal command** - show terminal output: triage classification, tool calls, fix (~2 min)
5. **Show the API log browser** - filter by tool calls to show the AI's investigation process (~1 min)

> Record this ahead of time at home where API access is available. Embed the video in the presentation.
