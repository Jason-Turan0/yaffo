import {Spec, ContextItem} from "@lib/test_generator/spec_parser.types";
import {basename, extname, join, resolve} from "path";
import fs, {existsSync, readFileSync} from "fs";
import {GeneratedTestResponse} from "@lib/test_generator/model_client.response.types";
import {runPlaywrightTests, formatTestResultsAsXml} from "@lib/test_generator/isolated_runner";

const YAFFO_ROOT = resolve(join(process.cwd(), "../yaffo"));

interface LoadedContext {
    tag: string;
    path: string;
    description?: string;
    content: string;
}

export const TEST_GENERATOR_OUTPUT_FORMAT = fs.readFileSync(
    join(process.cwd(), "lib", "test_generator", "model_client.response.types.ts"),
    "utf8"
);

export class PromptGenerator {

    constructor(
        private testServerIsRunning: boolean,
        private baseUrl: string,
        private yaffoRoot: string,
        private outputDir: string,
        private spec: Spec,
    ) {
    }

    getExistingTestFilePaths(): string[] {
        const jsonPath = join(this.outputDir, `${this.spec.feature}.json`);
        if (!existsSync(jsonPath)) {
            return [];
        }
        const foundTestFilePaths: string[] = [];
        try {
            const existingJson = readFileSync(jsonPath, "utf-8");
            const existingResponse = JSON.parse(existingJson) as GeneratedTestResponse;

            for (const file of existingResponse.files) {
                const filePath = join(this.outputDir, basename(file.filename));
                if (existsSync(filePath)) {
                    foundTestFilePaths.push(filePath);
                }
            }
        } catch (e) {
            console.log(`   ⚠️  Could not parse existing JSON from file ${jsonPath}: ${e instanceof Error ? e.message : String(e)}`);
        }
        return foundTestFilePaths;
    }

    async getSystemPrompt(): Promise<string> {
        const existingTestFiles = this.getExistingTestFilePaths();
        const isUpdateMode = existingTestFiles.length > 0;

        // 1. Core Persona
        const roleBlock = [
            "<role>",
            "    You are an expert Playwright test generator with READ-ONLY access to filesystem tools.",
            "    Your goal is to produce high-quality, resilient TypeScript tests.",
            "</role>"
        ];

        // 2. Dynamic Task Objective
        const taskBlock = [
            "<task_objective>",
            isUpdateMode
                ? "    Review the <existing_tests>, evaluate the <test_evaluation>, and fix or update the test code."
                : "    Explore the codebase via tools to discover HTML elements, then generate new Playwright tests.",
            "</task_objective>"
        ];

        // 3. Environment & Capabilities
        const envBlock = [
            "<environment>",
            `    <base_url>${this.baseUrl}</base_url>`,
            "    <capabilities>",
            "        - READ-ONLY file system access application source code",
            "        - READ-ONLY file system access to directory with generated playwright tests",
            this.testServerIsRunning ? "        - READ-ONLY file system access to root folder of running website. Location of sqllite db, photos, thumbnails" : "",
            "        - Memory for storing and retrieving context of the task ",
            this.testServerIsRunning ? "        - Access to a live sandboxed website via Playwright MCP tools" : "",
            "    </capabilities>",
            "</environment>"
        ];

        // 4. Conditional Context (Existing Files & Test Results)
        const contextBlocks: string[] = [];
        if (isUpdateMode) {
            contextBlocks.push("<existing_tests>", ...existingTestFiles.map(f => `    ${f}`), "</existing_tests>");

            if (this.testServerIsRunning) {
                const testRunResult = await runPlaywrightTests(this.baseUrl, existingTestFiles);
                contextBlocks.push(formatTestResultsAsXml(testRunResult));
            }
        }

        // 5. Static Instructions & Policies
        const instructionBlocks = [
            "<workflow>",
            "    1. Use filesystem tools to explore relevant templates and routes.",
            "    2. Look for actual element IDs, classes, and data-testid attributes.",
            "    3. Generate accurate Playwright tests and return as structured JSON.",
            "</workflow>",
            "<guidelines>",
            "    1. Use TypeScript (@playwright/test).",
            "    2. Use ACTUAL selectors from templates—NEVER guess.",
            "    3. Add appropriate waits for dynamic content.",
            "    4. Use page.goto('/') and derive base URL for requests dynamically.",
            "</guidelines>",
            "<tool_policy>",
            "    <use_parallel_tool_calls>",
            "        Maximize speed by making independent tool calls in parallel. If reading 3 files,",
            "        run 3 tool calls simultaneously. Only call sequentially if parameters depend on previous results.",
            "    </use_parallel_tool_calls>",
            "</tool_policy>"
        ];

        // 6. Final Formatting
        const outputBlock = [
            "<output_format>",
            "<instructions>When you have finished evaluating and are ready to generate the test with an end_turn message respond ONLY with a JSON object. Use no explanations or markdown blocks.</instructions>",
            `<expected_schema type="typescript">${TEST_GENERATOR_OUTPUT_FORMAT}</expected_schema>`,
            "</output_format>"
        ];

        // Flatten all blocks into a single array
        return [
            ...roleBlock,
            "",
            ...taskBlock,
            "",
            ...envBlock,
            "",
            ...contextBlocks,
            "",
            ...instructionBlocks,
            "",
            ...outputBlock
        ].join("\n");
    }


    async getSystemPrompt2(): Promise<string> {
        const testServerPrompt = this.testServerIsRunning ? [
            "You also access to running sandboxed instance of the website that you can use to interact with a live instance of the website",
            "Use the provided tools from playwright mcp to interact with the website to help provide context for the runtime behavior of the site for generating tests",
            `The base url of the website is ${this.baseUrl}`,
            ""
        ] : [];
        const existingTestFiles = this.getExistingTestFilePaths();
        const createPrompts = [
            "1. Explore the codebase to discover actual HTML elements, selectors, and routes if needed",
            "2. Generate accurate Playwright tests based on what you find",
            "3. Return your output as structured JSON (you cannot write files directly)",
        ];
        const updatePrompts = [
            `1. Check your memory and the existing tests.`,
            "2. Fix/Update the test code as needed.",
            "3. Return your output as structured JSON (you cannot write files directly)",
            "Existing test files:",
            "",
            ...existingTestFiles,
        ];
        if (this.testServerIsRunning && existingTestFiles.length > 0) {
            updatePrompts.push('Test Results:')
            const testRunResult = await runPlaywrightTests(this.baseUrl, existingTestFiles);
            if (testRunResult.success) {
                updatePrompts.push('All tests passed')
            } else {
                updatePrompts.push(...[
                    "The generated playwright test had failures that need to be corrected:",
                    "",
                    testRunResult.output,
                    "",
                ]);
            }
        }
        return [
            "You are an expert Playwright test generator with READ-ONLY access to filesystem tools.",
            "",
            "Your job is to:",
            ...(existingTestFiles.length > 0 ? updatePrompts : createPrompts),
            "",
            "You have access to READ-ONLY tools that let you explore:",
            "- Flask templates (templates/) - the actual HTML structure",
            "- Routes (routes/) - available endpoints and their behavior",
            "- Static files (static/) - JavaScript and CSS",
            "",
            ...testServerPrompt,
            "## WORKFLOW",
            "",
            "1. Use filesystem tools to explore relevant templates and routes",
            "2. Look for actual element IDs, classes, data-testid attributes, and form structures",
            "3. Generate accurate Playwright tests using the real selectors you discovered",
            "4. Return the generated code as structured JSON",
            "",
            "## GUIDELINES",
            "",
            "1. Generate TypeScript Playwright tests using @playwright/test",
            "2. Use ACTUAL selectors from the templates - do not guess",
            "3. Add appropriate waits for dynamic content (waitForSelector, waitForResponse, etc.)",
            "4. Include meaningful assertions that match the 'verify' criteria from the spec",
            "5. Handle common edge cases (loading states, network delays)",
            "",
            //Source
            //https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-4-best-practices#optimize-parallel-tool-calling
            "## TOOL USAGE",
            "<use_parallel_tool_calls>",
            "If you intend to call multiple tools and there are no dependencies between the tool calls, make all of the independent tool calls in parallel. Prioritize calling tools simultaneously whenever the actions can be done in parallel rather than sequentially. For example, when reading 3 files, run 3 tool calls in parallel to read all 3 files into context at the same time. Maximize use of parallel tool calls where possible to increase speed and efficiency. However, if some tool calls depend on previous calls to inform dependent values like the parameters, do NOT call these tools in parallel and instead call them sequentially. Never use placeholders or guess missing parameters in tool calls.",
            "</use_parallel_tool_calls>",
            "",
            "## URL HANDLING",
            "",
            "IMPORTANT:",
            "- NEVER hardcode URLs like 'http://127.0.0.1:5000' in test code",
            "- Use page.goto('/') for navigation - Playwright uses baseURL from config",
            "- For HTTP requests, derive the base URL dynamically: new URL(page.url()).origin",
            "",
            "## OUTPUT FORMAT",
            "",
            "When you are ready to generate the tests, respond with a JSON object in this exact format:",
            "",
            "```typescript",
            TEST_GENERATOR_OUTPUT_FORMAT,
            "```",
            "",
            "IMPORTANT:",
            "- The 'code' field must contain the complete, valid TypeScript test file",
            "- Do NOT wrap the JSON in markdown code blocks when responding",
            "- Do NOT include an explanation before the JSON. Output the JSON in a single line format.",
        ].join("\n");
    }

    buildSchemaFixPrompt(schemaErrors: string[]): string {
        return [
            "<schema_validation>",
            "    <status>failed</status>",
            "    <errors>",
            ...schemaErrors.map(e => `        <error>${e}</error>`),
            "    </errors>",
            "</schema_validation>",
            "",
            "",
            "<instructions>Fix the schema errors and provide the corrected JSON in the <output_format>.</instructions>",
        ].join("\n");
    }

    buildTypeErrorFixPrompt(typeErrors: string[], currentCode: string): string {
        return [
            "<type_validation>",
            "    <status>failed</status>",
            "    <errors>",
            ...typeErrors.map(e => `        <error>${e}</error>`),
            "    </errors>",
            "</type_validation>",
            "",
            "<current_code>",
            currentCode,
            "</current_code>",
            "",
            "<instructions>Fix the TypeScript compilation errors and provide the corrected JSON in the <output_format>.</instructions>",
        ].join("\n");
    }

    buildTestFailurePrompt(playwrightFailures: string[], currentCode: string): string {
        return [
            "<test_evaluation>",
            "    <status>failed</status>",
            "    <failures>",
            ...playwrightFailures.map(f => `        <failure>${f}</failure>`),
            "    </failures>",
            "</test_evaluation>",
            "",
            "<current_code>",
            currentCode,
            "</current_code>",
            "",
            "<instructions>Fix the test failures and provide the corrected code in the <output_format>.</instructions>",
        ].join("\n");
    }

    buildUserPrompt(spec: Spec, specPath: string, baseUrl: string, allowedDirs: string[]): string {
        const timestamp = new Date().toISOString();
        const loadedContext = spec.context ? this.loadContextFiles(spec.context) : [];
        const contextSection = this.buildContextSection(loadedContext);

        const explorationNote = loadedContext.length > 0
            ? "Pre-loaded context has been provided. Use these files as your primary reference. " +
            "You may still explore other files if needed for additional context."
            : "Start by exploring the templates directory to find actual selectors, " +
            "then generate the test file as JSON.";

        const preconditionsSection = spec.preconditions
            ? `<preconditions>\n${spec.preconditions.map(p => `    ${p}`).join("\n")}\n</preconditions>`
            : "";
        const scenariosSection = spec.scenarios.map(s => [
            `    <scenario>`,
            `        <name>${s.name}</name>`,
            `        <goal>${s.goal}</goal>`,
            `        <priority>${s.priority}</priority>`,
            `        <steps>`,
            ...s.steps.map(step => `            <step>${step}</step>`),
            `        </steps>`,
            `        <verify>`,
            ...s.verify.map(v => `            <assertion>${v}</assertion>`),
            `        </verify>`,
            `    </scenario>`,
        ].join("\n")).join("\n");

        return [
            `<task>Generate Playwright tests from this specification.</task>`,
            "",
            "<filesystem_access>",
            "    <description>You have READ-ONLY access to these directories:</description>",
            ...allowedDirs.map(d => `    <directory>${d}</directory>`),
            "    <note>Use absolute paths when using the file tools</note>",
            "</filesystem_access>",
            "",
            "<spec_file>",
            `    <path>${specPath}</path>`,
            "</spec_file>",
            "",
            "<specification>",
            `    <feature>${spec.feature}</feature>`,
            `    <description>${spec.description}</description>`,
            preconditionsSection,
            ...(spec.data ? ["    <data>", ...spec.data.map(d => `        ${d}`), "    </data>"] : []),
            "    <scenarios>",
            scenariosSection,
            "    </scenarios>",
            "</specification>",
            "",
            contextSection,
            "",
            "<configuration>",
            `    <base_url>${baseUrl}</base_url>`,
            `    <generated_at>${timestamp}</generated_at>`,
            "</configuration>",
            "",
            "<instructions>",
            `    ${explorationNote}`,
            "</instructions>",
            ""
        ].join("\n");
    }

    private loadContextFiles(contextItems: ContextItem[]): LoadedContext[] {
        const loaded: LoadedContext[] = [];

        for (const item of contextItems) {
            if (item.path) {
                this.loadContextByPath(item, loaded);
            } else if (item.attribute) {
                this.loadContextByAttribute(item, loaded);
            }
        }

        return loaded;
    }

    private loadContextByPath(item: ContextItem, loaded: LoadedContext[]): void {
        const absolutePath = resolve(join(this.yaffoRoot, item.path!));

        try {
            if (fs.existsSync(absolutePath)) {
                const content = fs.readFileSync(absolutePath, "utf-8");
                loaded.push({
                    tag: item.tag,
                    path: absolutePath,
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
    }

    private loadContextByAttribute(item: ContextItem, loaded: LoadedContext[]): void {
        const relativePaths = this.searchFilesByAttribute(item.attribute!);
        console.log(`   🔍 Searching for attribute "${item.attribute}" - found ${relativePaths.length} file(s)`);

        for (const filePath of relativePaths) {
            const absolutePath = resolve(join(this.yaffoRoot, filePath));

            try {
                const content = fs.readFileSync(absolutePath, "utf-8");
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

    private searchFilesByAttribute(attribute: string): string[] {
        const validExtensions = new Set([".py", ".html", ".js", ".css"]);
        const skipDirs = new Set(["node_modules", ".git", "__pycache__", "venv", ".venv"]);
        const pattern = new RegExp(`@context.*${attribute}`);
        const results: string[] = [];

        const searchDir = (dir: string, relativePath: string = ""): void => {
            let entries: fs.Dirent[];
            try {
                entries = fs.readdirSync(dir, {withFileTypes: true});
            } catch {
                return;
            }

            for (const entry of entries) {
                const fullPath = join(dir, entry.name);
                const relPath = relativePath ? join(relativePath, entry.name) : entry.name;

                if (entry.isDirectory()) {
                    if (!skipDirs.has(entry.name)) {
                        searchDir(fullPath, relPath);
                    }
                } else if (entry.isFile() && validExtensions.has(extname(entry.name))) {
                    try {
                        const content = fs.readFileSync(fullPath, "utf-8");
                        if (pattern.test(content)) {
                            results.push(relPath);
                        }
                    } catch {
                        // Skip files that can't be read
                    }
                }
            }
        };

        try {
            searchDir(this.yaffoRoot);
        } catch {
            // Return empty if root directory can't be read
        }

        return results;
    }

    private buildContextSection(loadedContext: LoadedContext[]): string {
        if (loadedContext.length === 0) {
            return "";
        }

        const sections = loadedContext.map(ctx => {
            const descriptionAttr = ctx.description ? ` description="${ctx.description}"` : "";
            return [
                `    <context_file tag="${ctx.tag}"${descriptionAttr}>`,
                `        <path>${ctx.path}</path>`,
                `        <content>`,
                ctx.content,
                `        </content>`,
                `    </context_file>`,
            ].join("\n");
        });

        return [
            "<preloaded_context>",
            "    <note>The following source files have been pre-loaded as relevant context for this test. Use these actual selectors, routes, and structures rather than exploring for them.</note>",
            ...sections,
            "</preloaded_context>",
        ].join("\n");
    }

}

// Factory function
export const promptGeneratorFactory = (testServerIsRunning: boolean, baseUrl: string, yaffoRoot: string, outputDir: string, spec: Spec): PromptGenerator => {
    return new PromptGenerator(testServerIsRunning, baseUrl, yaffoRoot, outputDir, spec);
};