import {TestRunResult} from "@lib/test_generator/isolated_runner";
import {formatTestResultsAsXml} from "@lib/test_generator/run_playwright_tests";
import {Spec} from "@lib/test_generator/spec_parser.types";
import {SpecPromptGenerator} from "@lib/test_generator/spec_prompt_generator";

export interface HealContext {
    absoluteTestFilePath: string;
    testCode: string;
    testFailures: TestRunResult;
    specPath: string;
    spec: Spec;
    testDescription: string;
    testContext: string;
    explanation: string;
}

export class HealPromptGenerator {
    private specPromptGenerator: SpecPromptGenerator;

    constructor(
        private baseUrl: string,
    ) {
        this.specPromptGenerator = new SpecPromptGenerator();
    }

    async buildSystemPrompt(outputSchema?: string): Promise<string> {
        const roleBlock = [
            "<role>",
            "    You are an expert Playwright test debugger with READ-ONLY access to filesystem tools.",
            "    Your goal is to analyze failing tests and produce corrected, working TypeScript test code.",
            "</role>"
        ];

        const taskBlock = [
            "<task_objective>",
            "    Analyze the failing test, investigate the root cause using available tools,",
            "    and produce a corrected version of the test that will pass.",
            "</task_objective>"
        ];

        const envBlock = [
            "<environment>",
            `    <base_url>${this.baseUrl}</base_url>`,
            "    <capabilities>",
            "        - READ-ONLY file system access to application source code",
            "        - READ-ONLY file system access to generated playwright tests",
            "        - Access to a live sandboxed website via Playwright MCP tools",
            "        - Memory for storing and retrieving investigation notes",
            "    </capabilities>",
            "</environment>"
        ];

        const workflowBlock = [
            "<workflow>",
            "    1. Analyze the test failures to understand what went wrong.",
            "    2. Use filesystem tools to inspect relevant templates, routes, and source code.",
            "    3. Use Playwright tools to interact with the live site and verify assumptions.",
            "    4. Identify the root cause (wrong selector, timing issue, incorrect assertion, etc.).",
            "    5. Generate the corrected test code and return as structured JSON.",
            "</workflow>"
        ];

        const guidelinesBlock = [
            "<guidelines>",
            "    1. Focus on fixing the specific failures - don't rewrite tests unnecessarily.",
            "    2. Use ACTUAL selectors from templates—verify them before using.",
            "    3. Add appropriate waits for dynamic content if timing is the issue.",
            "    4. Preserve the test structure and naming where possible.",
            "    5. If a test is fundamentally flawed, explain why and provide a working alternative.",
            "</guidelines>"
        ];

        const toolPolicyBlock = [
            "<tool_policy>",
            "    <use_parallel_tool_calls>",
            "        Maximize speed by making independent tool calls in parallel.",
            "    </use_parallel_tool_calls>",
            "</tool_policy>"
        ];

        const outputFormatBlock = outputSchema ? [
            "<output_format>",
            "    When you are done using tools and ready to provide your final answer,",
            "    respond with ONLY valid JSON matching this schema (no markdown, no commentary):",
            "    <schema>",
            `    ${outputSchema}`,
            "    </schema>",
            "</output_format>"
        ] : [];

        return [
            ...roleBlock,
            "",
            ...taskBlock,
            "",
            ...envBlock,
            "",
            ...workflowBlock,
            "",
            ...guidelinesBlock,
            "",
            ...toolPolicyBlock,
            "",
            ...outputFormatBlock,
        ].join("\n");
    }

    buildHealPrompt(context: HealContext, allowedDirs: string[]): string {
        const timestamp = new Date().toISOString();
        const failuresXml = formatTestResultsAsXml(context.testFailures);
        const specSection = this.specPromptGenerator.formatSpec(context.spec);
        const allowDirectoriesSection = this.specPromptGenerator.generateAllowedDirectories(allowedDirs);

        return [
            "<task>Fix the failing Playwright test.</task>",
            "",
            ...allowDirectoriesSection,
            "",
            "<spec_file>",
            `    <path>${context.specPath}</path>`,
            "</spec_file>",
            "",
            specSection,
            "",
            "<failing_test>",
            `    <path>${context.absoluteTestFilePath}</path>`,
            "    <code>",
            context.testCode,
            "    </code>",
            "</failing_test>",
            "",
            failuresXml,
            "",
            "<configuration>",
            `    <base_url>${this.baseUrl}</base_url>`,
            `    <timestamp>${timestamp}</timestamp>`,
            "</configuration>",
            "",
            "<instructions>",
            "    Investigate the failures using the available tools, then provide the corrected test code.",
            "    Return your response as structured JSON with the fixed code.",
            "</instructions>",
            "<context>",
            `   <test_context>${context.testContext}</test_context>`,
            `   <test_description>${context.testDescription}</test_description>`,
            `   <explanation>${context.explanation}</explanation>`,
            "</context>",
            ""
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
            "<instructions>Fix the TypeScript compilation errors and provide the corrected JSON.</instructions>",
        ].join("\n");
    }

    buildTestFailurePrompt(testFailures: TestRunResult, currentCode: string): string {
        const failuresXml = formatTestResultsAsXml(testFailures);
        return [
            failuresXml,
            "",
            "<current_code>",
            currentCode,
            "</current_code>",
            "",
            "<instructions>The test is still failing. Investigate further and provide corrected code.</instructions>",
        ].join("\n");
    }
}

export const healPromptGeneratorFactory = (
    baseUrl: string,
): HealPromptGenerator => {
    return new HealPromptGenerator(baseUrl);
};