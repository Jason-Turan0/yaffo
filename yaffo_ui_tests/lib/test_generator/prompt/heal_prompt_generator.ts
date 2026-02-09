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

    buildSystemPrompt(): string {
        return [
            "<role>",
            "    You are an expert Playwright test analyst and debugger with READ-ONLY access to filesystem tools",
            "    and a live sandboxed website via Playwright MCP tools.",
            "    You work in two phases:",
            "    Phase 1 (Triage): Investigate the failure and classify its root cause.",
            "    Phase 2 (Fix): If the failure is a test code defect, produce corrected TypeScript test code.",
            "    Your current phase will be specified in user messages.",
            "</role>",
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
            "<guidelines>",
            "    1. INVESTIGATE before concluding — use tools to verify your hypothesis.",
            "    2. Use ACTUAL selectors from templates — verify them before using.",
            "    3. Look at error messages carefully — timeout/network errors suggest environment instability.",
            "    4. If the same tests fail repeatedly with the same error, it's likely a defect or regression, not flakiness.",
            "    5. Use Playwright to check if the page renders correctly.",
            "    6. Consider the test run history trends when making decisions.",
            "    7. Focus on fixing specific failures — don't rewrite tests unnecessarily.",
            "    8. Add appropriate waits for dynamic content if timing is the issue.",
            "    9. Preserve test structure and naming where possible.",
            "</guidelines>",
            "",
            "<tool_policy>",
            "    <use_parallel_tool_calls>",
            "        Maximize speed by making independent tool calls in parallel.",
            "    </use_parallel_tool_calls>",
            "</tool_policy>",
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

    buildTransitionToHealPrompt(analysisReasoning: string, outputSchema?: string): string {
        const blocks = [
            "<phase>Phase 2: Fix</phase>",
            "",
            "<triage_result>",
            "    <classification>test_code_defect</classification>",
            `    <reasoning>${analysisReasoning}</reasoning>`,
            "</triage_result>",
            "",
            "<instructions>",
            "    Based on your investigation above, produce the corrected test code.",
            "    You already have context from your triage analysis — use it.",
            "    If you need to verify additional details, use the available tools.",
            "    Return your response as structured JSON with the fixed code.",
            "</instructions>",
        ];

        if (outputSchema) {
            blocks.push(
                "",
                "<output_format>",
                "    Respond with ONLY valid JSON matching this schema (no markdown, no commentary):",
                "    <schema>",
                `    ${outputSchema}`,
                "    </schema>",
                "</output_format>",
            );
        }

        return blocks.join("\n");
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

    buildAnalysisPrompt(context: HealContext, allowedDirs: string[], outputSchema?: string): string {
        const failuresXml = formatTestResultsAsXml(context.testFailures);
        const specSection = this.specPromptGenerator.formatSpec(context.spec);
        const historySection = formatHistoryForPrompt(context.testRunHistory);
        const allowDirectoriesSection = this.specPromptGenerator.generateAllowedDirectories(allowedDirs);

        const blocks = [
            "<phase>Phase 1: Triage</phase>",
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
            "<context>",
            `   <test_context>${context.testContext}</test_context>`,
            `   <test_description>${context.testDescription}</test_description>`,
            `   <explanation>${context.explanation}</explanation>`,
            "</context>",
            "",
            historySection,
            "",
            "<task_objective>",
            "    Investigate the failing test using the available tools, then classify the failure",
            "    into one of three categories:",
            "    - test_code_defect: The test code itself is broken (wrong selectors, logic errors, missing waits).",
            "    - application_regression: The test is correct but the application has a real bug.",
            "    - environment_instability: Flaky infrastructure, network timeouts, or timing issues.",
            "</task_objective>",
            "",
            "<workflow>",
            "    1. Review the test failures, test code, and spec provided above.",
            "    2. Use filesystem tools to inspect relevant application source code (templates, routes, models).",
            "    3. Use Playwright tools to interact with the live site and verify whether the application behavior matches expectations.",
            "    4. Compare what the test expects vs what the application actually does.",
            "    5. Check the test run history for patterns (intermittent vs consistent failures).",
            "    6. Classify the failure and return structured JSON.",
            "</workflow>",
            "",
            "<instructions>",
            "    Investigate the failure using the available tools. Read relevant source code,",
            "    check the live application with Playwright, and review test history.",
            "    Then classify the failure as one of: test_code_defect, application_regression, environment_instability.",
            "    Provide your reasoning and list the affected test names.",
            "    Return your response as structured JSON.",
            "</instructions>",
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