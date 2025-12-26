import {Spec, ContextItem} from "@lib/test_generator/spec_parser.types";
import {join, resolve, relative} from 'path';
import fs from 'fs'
import {execSync} from 'child_process';

const YAFFO_ROOT = resolve(join(process.cwd(), "../yaffo"));

interface LoadedContext {
    tag: string;
    path: string;
    description?: string;
    content: string;
}

const searchFilesByAttribute = (attribute: string): string[] => {
    try {
        const pattern = `@context.*${attribute}`;
        const result = execSync(
            `grep -rlE "${pattern}" --include="*.py" --include="*.html" --include="*.js" --include="*.css" .`,
            {cwd: YAFFO_ROOT, encoding: 'utf-8', maxBuffer: 10 * 1024 * 1024}
        );
        return result
            .trim()
            .split('\n')
            .filter((line) => line.length > 0)
            .map((line) => line.replace(/^\.\//, ''));
    } catch {
        return [];
    }
};

const loadContextFiles = (contextItems: ContextItem[]): LoadedContext[] => {
    const loaded: LoadedContext[] = [];

    for (const item of contextItems) {
        if (item.path) {
            const fullPath = join(YAFFO_ROOT, item.path);
            try {
                if (fs.existsSync(fullPath)) {
                    const content = fs.readFileSync(fullPath, 'utf-8');
                    loaded.push({
                        tag: item.tag,
                        path: item.path,
                        description: item.description,
                        content,
                    });
                    console.log(`   📄 Loaded context: ${item.tag} (${item.path})`);
                } else {
                    console.warn(`   ⚠️  Context file not found: ${item.path}`);
                }
            } catch {
                console.warn(`   ⚠️  Failed to load context file: ${item.path}`);
            }
        } else if (item.attribute) {
            const matchingFiles = searchFilesByAttribute(item.attribute);
            console.log(`   🔍 Searching for attribute "${item.attribute}" - found ${matchingFiles.length} file(s)`);

            for (const filePath of matchingFiles) {
                const fullPath = join(YAFFO_ROOT, filePath);
                try {
                    const content = fs.readFileSync(fullPath, 'utf-8');
                    loaded.push({
                        tag: item.tag,
                        path: filePath,
                        description: item.description
                            ? `${item.description} (matched: ${item.attribute})`
                            : `Matched attribute: ${item.attribute}`,
                        content,
                    });
                    console.log(`   📄 Loaded context: ${item.tag} (${filePath})`);
                } catch {
                    console.warn(`   ⚠️  Failed to load matched file: ${filePath}`);
                }
            }
        }
    }
    return loaded;
};

const buildContextSection = (loadedContext: LoadedContext[]): string => {
    if (loadedContext.length === 0) {
        return '';
    }

    const sections = loadedContext.map((ctx) => {
        const header = ctx.description
            ? `### ${ctx.tag}: ${ctx.description}`
            : `### ${ctx.tag}`;
        return `${header}
Path: ${ctx.path}
\`\`\`
${ctx.content}
\`\`\``;
    });

    return `## Pre-loaded Source Context
The following source files have been pre-loaded as relevant context for this test. Use these actual selectors, routes, and structures rather than exploring for them.

${sections.join('\n\n')}`;
};

const OUTPUT_FORMAT = fs.readFileSync(join(process.cwd(), 'lib', 'test_generator', 'model_client.response.types.ts'), 'utf8');

export const SYSTEM_PROMPT = `You are an expert Playwright test generator with READ-ONLY access to filesystem tools. Your job is to:
1. Explore the codebase to discover actual HTML elements, selectors, and routes if needed
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

When you are ready to generate the tests, respond with a JSON object in this exact format.:

\`\`\`typescript
${OUTPUT_FORMAT}
\`\`\`

IMPORTANT:
- The "code" field must contain the complete, valid TypeScript test file
- Do NOT wrap the JSON in markdown code blocks when responding
- Do NOT include an explanation before the JSON. Output the json in a single line format. 
`;

export const buildUserPrompt = (spec: Spec, specPath: string, baseUrl: string, allowedDirs: string[]): string => {
    const timestamp = new Date().toISOString();

    const loadedContext = spec.context ? loadContextFiles(spec.context) : [];
    const contextSection = buildContextSection(loadedContext);

    const explorationNote = loadedContext.length > 0
        ? `Pre-loaded context has been provided below. Use these files as your primary reference. You may still explore other files if needed for additional context.`
        : `Start by exploring the templates directory to find actual selectors, then generate the test file as JSON.`;

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

${contextSection}

## Configuration
- Base URL: ${baseUrl}
- Generated at: ${timestamp}

${explorationNote}`
};
