import {TestRunResult} from "@lib/services/isolated_runner";
import {formatTestResultsAsXml} from "@lib/services/run_playwright_tests";
import {Spec} from "@lib/test_generator/prompt/spec_parser.types";
import {SpecPromptGenerator} from "@lib/test_generator/prompt/spec_prompt_generator";
import {TestRunRecord, formatHistoryForPrompt} from "@lib/test_generator/test_result_history";

export interface HealContext {
    absoluteTestFilePath: string;
    testCode: string;
    testFailures: TestRunResult;
    specPath: string;
    spec: Spec;
    testDescription: string;
    testContext: string;
    explanation: string;
    testRunHistory: TestRunRecord[];
}

export class HealPromptGenerator {
    private specPromptGenerator: SpecPromptGenerator;

    constructor(
        private baseUrl: string,
    ) {
        this.specPromptGenerator = new SpecPromptGenerator();
    }

    buildSystemPrompt(outputSchema?: string): string {
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
            "",
            formatHistoryForPrompt(context.testRunHistory),
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

    buildAnalysisSystemPrompt(outputSchema?: string): string {
        const blocks = [
            "<role>",
            "    You are a test failure triage specialist with READ-ONLY access to filesystem tools",
            "    and a live sandboxed website via Playwright MCP tools.",
            "    Your job is to classify WHY a Playwright test failed — not to fix it.",
            "</role>",
            "",
            "<task_objective>",
            "    Investigate the failing test using the available tools, then classify the failure",
            "    into one of three categories:",
            "    - test_code_defect: The test code itself is broken (wrong selectors, logic errors, missing waits).",
            "    - application_regression: The test is correct but the application has a real bug.",
            "    - environment_instability: Flaky infrastructure, network timeouts, or timing issues.",
            "</task_objective>",
            "",
            "<environment>",
            `    <base_url>${this.baseUrl}</base_url>`,
            "    <capabilities>",
            "        - READ-ONLY file system access to application source code",
            "        - READ-ONLY file system access to generated playwright tests",
            "        - Access to a live sandboxed website via Playwright MCP tools",
            "        - Memory for storing and retrieving investigation notes",
            "    </capabilities>",
            "</environment>",
            "",
            "<workflow>",
            "    1. Review the test failures, test code, and spec provided in the prompt.",
            "    2. Use filesystem tools to inspect relevant application source code (templates, routes, models).",
            "    3. Use Playwright tools to interact with the live site and verify whether the application behavior matches expectations.",
            "    4. Compare what the test expects vs what the application actually does.",
            "    5. Check the test run history for patterns (intermittent vs consistent failures).",
            "    6. Classify the failure and return structured JSON.",
            "</workflow>",
            "",
            "<guidelines>",
            "    1. INVESTIGATE before classifying — use tools to verify your hypothesis.",
            "    2. Look at the error messages carefully — timeout/network errors suggest environment instability.",
            "    3. If the same tests fail repeatedly with the same error, it's likely a defect or regression, not flakiness.",
            "    4. If the failure matches a recently-passing pattern (was passing, now failing), lean toward regression.",
            "    5. If the test code references selectors or behavior that doesn't match the spec, it's a test code defect.",
            "    6. Use Playwright to check if the page renders correctly — if the app is broken, classify as application_regression.",
            "    7. Consider the test run history trends when making your decision.",
            "</guidelines>",
            "",
            "<tool_policy>",
            "    <use_parallel_tool_calls>",
            "        Maximize speed by making independent tool calls in parallel.",
            "    </use_parallel_tool_calls>",
            "</tool_policy>",
        ];

        if (outputSchema) {
            blocks.push(
                "",
                "<output_format>",
                "    When you are done investigating and ready to provide your classification,",
                "    respond with ONLY valid JSON matching this schema (no markdown, no commentary):",
                "    <schema>",
                `    ${outputSchema}`,
                "    </schema>",
                "</output_format>",
            );
        }

        return blocks.join("\n");
    }

    buildAnalysisPrompt(context: HealContext, allowedDirs: string[]): string {
        const failuresXml = formatTestResultsAsXml(context.testFailures);
        const specSection = this.specPromptGenerator.formatSpec(context.spec);
        const historySection = formatHistoryForPrompt(context.testRunHistory);
        const allowDirectoriesSection = this.specPromptGenerator.generateAllowedDirectories(allowedDirs);

        return [
            "<task>Classify why this Playwright test failed.</task>",
            "",
            ...allowDirectoriesSection,
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
            historySection,
            "",
            "<instructions>",
            "    Investigate the failure using the available tools. Read relevant source code,",
            "    check the live application with Playwright, and review test history.",
            "    Then classify the failure as one of: test_code_defect, application_regression, environment_instability.",
            "    Provide your reasoning and list the affected test names.",
            "    Return your response as structured JSON.",
            "</instructions>",
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