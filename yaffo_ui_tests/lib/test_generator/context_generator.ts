import {Spec} from "@lib/test_generator/spec_parser.types";
import {join, } from 'path';
import fs from 'fs'

const OUTPUT_FORMAT = fs.readFileSync(join(process.cwd(), 'lib', 'test_generator', 'model_client.response.types.ts'), 'utf8');

export const SYSTEM_PROMPT = `You are an expert Playwright test generator with READ-ONLY access to filesystem tools. Your job is to:
1. Explore the codebase to discover actual HTML elements, selectors, and routes
2. Generate accurate Playwright tests based on what you find
3. Return your output as structured JSON (you cannot write files directly)

You have access to READ-ONLY tools that let you explore:
- Flask templates (templates/) - the actual HTML structure
- Routes (routes/) - available endpoints and their behavior
- Static files (static/) - JavaScript and CSS

WORKFLOW:
1. Use filesystem tools to explore relevant templates and routes
2. Look for actual element IDs, classes, data-testid attributes, and form structures
3. Generate accurate Playwright tests using the real selectors you discovered
4. Return the generated code as structured JSON

Guidelines:
1. Generate TypeScript Playwright tests using @playwright/test
2. Use ACTUAL selectors from the templates - do not guess
3. Add appropriate waits for dynamic content (waitForSelector, waitForResponse, etc.)
4. Include meaningful assertions that match the "verify" criteria from the spec
5. Handle common edge cases (loading states, network delays)

IMPORTANT URL Handling:
- NEVER hardcode URLs like "http://127.0.0.1:5000" in test code
- Use page.goto('/') for navigation - Playwright uses baseURL from config
- For HTTP requests, derive the base URL dynamically: new URL(page.url()).origin

## OUTPUT FORMAT

When you are ready to generate the tests, respond with a JSON object in this exact format:

\`\`\`typescript
${OUTPUT_FORMAT}
\`\`\`

IMPORTANT:
- The "code" field must contain the complete, valid TypeScript test file
- Do NOT wrap the JSON in markdown code blocks when responding
`;

export const buildUserPrompt = (spec: Spec, specPath: string, baseUrl: string, allowedDirs: string[]): string => {
    const timestamp = new Date().toISOString();
    return `Generate Playwright tests from this specification.

## Filesystem Access
You have READ-ONLY access to these directories:
${allowedDirs.map((d) => `- ${d}`).join("\n")}

Use relative paths like "templates/", "routes/", "static/" when exploring.

## Spec File
Path: ${specPath}

## Specification
\`\`\`yaml
feature: ${spec.feature}
description: ${spec.description}

${spec.preconditions ? `preconditions:\n${spec.preconditions.map((p) => `  - ${p}`).join("\n")}` : ""}

scenarios:
${spec.scenarios
        .map(
            (s) => `  - name: ${s.name}
    goal: ${s.goal}
    priority: ${s.priority}
    steps:
${s.steps.map((step) => `      - ${step}`).join("\n")}
    verify:
${s.verify.map((v) => `      - ${v}`).join("\n")}`
        )
        .join("\n\n")}
\`\`\`

## Configuration
- Base URL: ${baseUrl}
- Generated at: ${timestamp}

Start by exploring the templates directory to find actual selectors, then generate the test file as JSON.`
};
