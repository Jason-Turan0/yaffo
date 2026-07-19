import {join, resolve, basename} from "path";
import {writeFileSync, readFileSync, existsSync} from "fs";
import {
    HealPromptGenerator,
    HealContext,
    healPromptGeneratorFactory
} from "@lib/test_generator/prompt/heal_prompt_generator";
import {parseSpecFile} from "@lib/test_generator/prompt/spec_parser";
import {
    GeneratedTestResponse,
    FailureClassification,
    HealAnalysisResponse
} from "@lib/model_clients/model_client.response.types";
import {parseJsonResponse, GeneratedTestResponseSchema} from "@lib/test_generator/prompt/json_parser";
import {TypeScriptValidator, DefaultTypeScriptValidator} from "@lib/services/typescript_validator";
import {createModelClient, supportsNativeStructuredOutput} from "@lib/model_clients/model_client_factory";
import {zodToJsonSchema} from "zod-to-json-schema";
import {createPlaywrightClient} from "@lib/tool_providers/mcp_playwright_client";
import {RawToolDefinition, ToolProvider} from "@lib/tool_providers/toolprovider.types";
import {TestRunResult} from "@lib/services/isolated_runner";
import {
    ModelAlias,
    ModelClient,
    ModelResponse,
    ToolCallResult,
    toTextPart,
    toToolResultPart
} from "@lib/model_clients/model_client.interface";
import {localFilesystemMemoryToolFactory} from "@lib/tool_providers/local_filesystem_memory_tool";
import {runPlaywrightTests, PlaywrightTestRunner} from "@lib/services/run_playwright_tests";
import {loadTestResultHistory, recordTestResult} from "@lib/test_generator/test_result_history";
import {parseHealAnalysisResponse, HealAnalysisResponseSchema} from "@lib/test_generator/heal_analysis";
import * as util from "node:util";
import {buildTestFailurePrompt} from "@lib/test_generator/prompt/formatters";

export interface HealResult {
    success: boolean;
    testFilePath: string;
    logPath: string;
    error?: string;
    iterations: number;
    classification?: FailureClassification;
    /** Estimated USD spent on model API calls for this heal. */
    costUsd?: number;
    /** Number of model API calls made. */
    apiCalls?: number;
}

export class AutoHealTestOrchestrator {
    private iterationCount = 0;
    private maxIterations = 50;
    private maxRetries = 3;
    private maxEnvironmentRetries = 3;
    private toolProviderMap: Map<string, { tool: RawToolDefinition; toolProvider: ToolProvider }> = new Map();
    private featureName: string = "";

    constructor(
        private absoluteTestFilePath: string,
        private runLogDir: string,
        private outputDir: string,
        private baseUrl: string,
        private model: ModelAlias,
        private modelClient: ModelClient,
        private promptGenerator: HealPromptGenerator,
        private allowedDirectories: string[],
        private toolProviders: ToolProvider[],
        private typeScriptValidator: TypeScriptValidator = new DefaultTypeScriptValidator(),
        private playwrightTestRunner: PlaywrightTestRunner = runPlaywrightTests,
    ) {
        const tools = toolProviders.flatMap(toolProvider =>
            toolProvider.getTools().map((tool) => ({tool, toolProvider}))
        );

        for (const tool of tools) {
            if (this.toolProviderMap.has(tool.tool.name)) {
                throw new Error(`Duplicate tool names ${tool.tool.name}`);
            }
            this.toolProviderMap.set(tool.tool.name, tool);
        }
    }

    /** Estimated USD spent on model API calls so far. */
    getCost = (): number => this.modelClient.getSessionCost();

    /** Number of model API calls made so far. */
    getApiCallCount = (): number => this.modelClient.getApiCallCount();

    healTest = async (testFailures: TestRunResult, specPath: string): Promise<HealResult> => {
        try {
            const testCode = readFileSync(this.absoluteTestFilePath, "utf-8");
            const spec = parseSpecFile(specPath);
            this.featureName = spec.feature;
            const initialTestResponse = this.loadJsonFile();
            const testRunHistory = loadTestResultHistory(this.outputDir, this.featureName);

            const healContext: HealContext = {
                absoluteTestFilePath: this.absoluteTestFilePath,
                testCode,
                testFailures,
                specPath,
                spec,
                testContext: initialTestResponse?.testContext || '',
                explanation: initialTestResponse?.explanation || '',
                testDescription: initialTestResponse?.files?.find(f => resolve(join(this.outputDir, f.filename)) === this.absoluteTestFilePath)?.description || '',
                testRunHistory,
            };

            const analysisResult = await this.triageFailure(healContext);

            if (analysisResult) {
                console.log(`\n🔍 Triage classification: ${analysisResult.classification}`);
                console.log(`   Reasoning: ${analysisResult.reasoning}`);
                this.recordAnalysisResult(analysisResult);

                if (analysisResult.classification === "application_regression" || analysisResult.classification === "environment_instability") {
                    recordTestResult(this.outputDir, this.featureName, testFailures);
                    return {
                        success: false,
                        testFilePath: this.absoluteTestFilePath,
                        error: `Not attempting to auto fix tests: ${analysisResult.reasoning}`,
                        logPath: this.runLogDir,
                        iterations: this.iterationCount,
                        classification: analysisResult.classification,
                    };
                }
            }

            this.transitionToHealPhase(analysisResult);

            const generatedJson = await this.generateHealedCode();
            if (!generatedJson) {
                recordTestResult(this.outputDir, this.featureName, testFailures);
                return {
                    success: false,
                    testFilePath: this.absoluteTestFilePath,
                    error: this.modelClient.lastError
                        ? `Heal code generation failed: ${this.modelClient.lastError}`
                        : "Heal code generation failed.",
                    logPath: this.runLogDir,
                    iterations: this.iterationCount,
                    classification: "test_code_defect",
                };
            }

            const result = await this.validateHealedCode(generatedJson);
            recordTestResult(this.outputDir, this.featureName, testFailures);
            return {...result, classification: analysisResult?.classification ?? "test_code_defect"};
        } finally {
            for (const toolProvider of this.toolProviders) {
                await toolProvider.disconnect();
            }
        }
    };

    private triageFailure = async (context: HealContext): Promise<HealAnalysisResponse | null> => {
        console.log(`\n🔍 Triaging test failure...`);
        try {
            this.modelClient.setOutputSchema(HealAnalysisResponseSchema);

            const outputSchemaStr = supportsNativeStructuredOutput(this.model)
                ? undefined
                : JSON.stringify(zodToJsonSchema(HealAnalysisResponseSchema), null, 2);

            const analysisPrompt = this.promptGenerator.buildAnalysisPrompt(context, this.allowedDirectories, outputSchemaStr);
            this.modelClient.addUserMessage([toTextPart(analysisPrompt)]);

            let finalText: string | null = null;

            while (this.iterationCount < this.maxIterations) {
                this.iterationCount++;
                const response = await this.modelClient.callModelApi();
                if (!response) {
                    console.warn("   ⚠️ Triage returned no response, defaulting to heal");
                    return null;
                }

                console.log(`   Triage iteration ${this.iterationCount}/${this.maxIterations} — stop reason: ${response.finishReason}`);

                if (response.finishReason === "stop" || response.finishReason === "length") {
                    finalText = response.text;
                    break;
                }

                if (response.finishReason === "tool-calls" && response.toolCalls.length > 0) {
                    const toolResults = await this.executeToolCalls(response.toolCalls);
                    this.modelClient.addToolResultMessage(toolResults.map(toToolResultPart));
                    continue;
                }

                console.warn(`   ⚠️ Unexpected triage stop reason: ${response.finishReason}`);
                return null;
            }

            if (!finalText) {
                console.warn("   ⚠️ Triage did not produce a final response, defaulting to heal");
                return null;
            }

            const {response: analysisResponse, schemaErrors} = parseHealAnalysisResponse(finalText);
            if (schemaErrors.length > 0) {
                console.warn(`   ⚠️ Triage response invalid: ${schemaErrors.join(", ")}`);
                return null;
            }

            return analysisResponse;
        } catch (e) {
            console.warn(`   ⚠️ Triage failed: ${e instanceof Error ? e.message : String(e)}, defaulting to heal`);
            return null;
        }
    };

    private transitionToHealPhase = (analysisResult: HealAnalysisResponse | null): void => {
        console.log(`\n🔧 Transitioning to heal phase (iteration ${this.iterationCount}/${this.maxIterations})...`);
        this.modelClient.setOutputSchema(GeneratedTestResponseSchema);

        const outputSchemaStr = supportsNativeStructuredOutput(this.model)
            ? undefined
            : JSON.stringify(zodToJsonSchema(GeneratedTestResponseSchema), null, 2);

        const reasoning = analysisResult?.reasoning ?? "Triage inconclusive, proceeding with heal.";
        const transitionPrompt = this.promptGenerator.buildTransitionToHealPrompt(reasoning, outputSchemaStr);
        this.modelClient.addUserMessage([toTextPart(transitionPrompt)]);
    };

    private executeToolCalls = async (toolCalls: ModelResponse["toolCalls"]): Promise<ToolCallResult[]> => {
        const toolResults: ToolCallResult[] = [];
        for (const call of toolCalls) {
            console.log(`   🔧 Tool: ${call.toolName} Id: ${call.toolCallId} (${JSON.stringify(call.input).slice(0, 50)}...)`);
            try {
                const toolTuple = this.toolProviderMap.get(call.toolName);
                if (!toolTuple) {
                    toolResults.push({
                        type: "tool_result",
                        toolName: call.toolName,
                        toolCallId: call.toolCallId,
                        result: `Error: No implementation for tool ${call.toolName}`,
                        isError: true,
                    });
                } else {
                    const result = await toolTuple.toolProvider.callTool(call.toolName, call.input);
                    const resultText = typeof result === "string" ? result : result.text;
                    toolResults.push({
                        type: "tool_result",
                        toolName: call.toolName,
                        toolCallId: call.toolCallId,
                        result: resultText,
                    });
                }
            } catch (e) {
                const errorMsg = e instanceof Error ? e.message : String(e);
                console.log(`   ❌ Tool error: ${errorMsg}`);
                toolResults.push({
                    type: "tool_result",
                    toolCallId: call.toolCallId,
                    toolName: call.toolName,
                    result: `Error: ${errorMsg}`,
                    isError: true,
                });
            }
        }
        return toolResults;
    };

    private generateHealedCode = async (): Promise<string | null> => {
        let generatedJson: string | null = null;
        while (this.iterationCount < this.maxIterations) {
            this.iterationCount++;
            const response = await this.modelClient.callModelApi();

            if (!response) {
                break;
            }

            const nextAction = await this.determineNextAction(response);

            if (nextAction.generatedJson) {
                generatedJson = nextAction.generatedJson;
            }

            if (!nextAction.continue) {
                break;
            }
        }
        return generatedJson;
    };

    private validateHealedCode = async (originalJson: string): Promise<HealResult> => {
        let retryCount = 0;
        let currentJson = originalJson;

        while (retryCount <= this.maxRetries) {
            const {response: parsedResponse, schemaErrors} = parseJsonResponse<GeneratedTestResponse>(currentJson);

            if (!parsedResponse) {
                const rawPath = join(this.outputDir, `${basename(this.absoluteTestFilePath)}.heal.txt`);
                writeFileSync(rawPath, currentJson);
                return {
                    success: false,
                    testFilePath: this.absoluteTestFilePath,
                    error: `Failed to parse JSON response. Raw response saved to ${rawPath}`,
                    logPath: this.runLogDir,
                    iterations: this.iterationCount,
                };
            }

            if (schemaErrors.length > 0) {
                retryCount++;
                this.addSchemaErrorMessage(schemaErrors, currentJson);
                const correctedJson = await this.generateHealedCode();
                if (!correctedJson) {
                    return {
                        success: false,
                        testFilePath: this.absoluteTestFilePath,
                        error: `JSON schema errors in response.`,
                        logPath: this.runLogDir,
                        iterations: this.iterationCount,
                    };
                }
                currentJson = correctedJson;
                continue;
            }

            const writtenPath = this.writeHealedFile(parsedResponse);
            const typeErrors = this.typeCheckFile(writtenPath);

            if (typeErrors.length > 0) {
                retryCount++;
                this.addCompileErrorMessage(typeErrors, parsedResponse);
                const correctedJson = await this.generateHealedCode();
                if (!correctedJson) {
                    return {
                        success: false,
                        testFilePath: this.absoluteTestFilePath,
                        error: `Failed to fix typescript compilation error.`,
                        logPath: this.runLogDir,
                        iterations: this.iterationCount,
                    };
                }
                currentJson = correctedJson;
                continue;
            }

            console.log(`\n✅ Healed file compiles successfully!`);

            const runResult = await this.runPlaywrightTest(writtenPath);
            if (runResult.success) {
                console.log(`\n✅ Healed test passes!`);
                return {
                    success: true,
                    testFilePath: this.absoluteTestFilePath,
                    logPath: this.runLogDir,
                    iterations: this.iterationCount,
                };
            }

            retryCount++;
            this.addPlaywrightTestErrorMessage(runResult);
            const correctedJson = await this.generateHealedCode();
            if (!correctedJson) {
                return {
                    success: false,
                    testFilePath: this.absoluteTestFilePath,
                    error: `Failed to correct playwright test failures after healing.`,
                    logPath: this.runLogDir,
                    iterations: this.iterationCount,
                };
            }
            currentJson = correctedJson;
        }

        return {
            success: false,
            testFilePath: this.absoluteTestFilePath,
            error: `Max retries (${this.maxRetries}) exceeded.`,
            logPath: this.runLogDir,
            iterations: this.iterationCount,
        };
    };

    private addSchemaErrorMessage = (schemaErrors: string[], currentJson: string): void => {
        schemaErrors.forEach(err => console.log(`   - ${err}`));
        const schemaFixPrompt = [
            "<schema_validation>",
            "    <status>failed</status>",
            "    <errors>",
            ...schemaErrors.map(e => `        <error>${e}</error>`),
            "    </errors>",
            "</schema_validation>",
            "",
            "<instructions>Fix the schema errors and provide the corrected JSON.</instructions>",
        ].join("\n");
        this.modelClient.addUserMessage([toTextPart(schemaFixPrompt)]);
    };

    private addCompileErrorMessage = (
        typeErrors: string[],
        parsedResponse: GeneratedTestResponse,
    ): void => {
        const currentCode = parsedResponse.files[0]?.code || "";
        const typeFixPrompt = this.promptGenerator.buildTypeErrorFixPrompt(typeErrors, currentCode);

        this.modelClient.addUserMessage([toTextPart(typeFixPrompt)]);
    };

    private addPlaywrightTestErrorMessage = (
        testFailures: TestRunResult,
    ): void => {
        const playwrightFailurePrompt = buildTestFailurePrompt(testFailures);
        this.modelClient.addUserMessage([toTextPart(playwrightFailurePrompt)]);
    };

    private determineNextAction = async (response: ModelResponse): Promise<{
        success: boolean;
        continue: boolean;
        generatedJson?: string;
    }> => {
        console.log(`   Stop reason: ${response.finishReason}`);

        if (response.finishReason === "stop" || response.finishReason === "length") {
            return {success: true, continue: false, generatedJson: response.text};
        }

        if (response.finishReason === "tool-calls" && response.toolCalls.length > 0) {
            const toolResults = await this.executeToolCalls(response.toolCalls);
            this.modelClient.addToolResultMessage(toolResults.map(toToolResultPart));
            return {success: true, continue: true};
        }

        throw new Error(`Unknown stop reason ${response.finishReason}`);
    };

    private writeHealedFile = (response: GeneratedTestResponse): string => {
        const file = (response.files || [])[0];
        if (!file) {
            throw new Error("No file in healed response");
        }
        writeFileSync(this.absoluteTestFilePath, file.code);
        console.log(`   📄 Written healed file: ${this.absoluteTestFilePath}`);

        this.updateJsonFile(file.code);

        return this.absoluteTestFilePath;
    };

    private loadJsonFile = (): GeneratedTestResponse | null => {
        const jsonPath = join(this.outputDir, `${this.featureName}.json`);
        if (!existsSync(jsonPath)) {
            console.warn(`File not found: ${jsonPath}`);
            return null;
        }
        const existingJson = readFileSync(jsonPath, "utf-8");
        return JSON.parse(existingJson) as GeneratedTestResponse;
    };

    private updateJsonFile = (healedCode: string): void => {
        const jsonPath = join(this.outputDir, `${this.featureName}.json`);
        if (!existsSync(jsonPath)) {
            console.log(`   ⚠️  No JSON file found at ${jsonPath}, skipping update`);
            return;
        }

        try {
            const existingJson = readFileSync(jsonPath, "utf-8");
            const existingResponse = JSON.parse(existingJson) as GeneratedTestResponse;
            const testFileName = basename(this.absoluteTestFilePath);

            const fileIndex = existingResponse.files.findIndex(
                f => basename(f.filename) === testFileName
            );

            if (fileIndex === -1) {
                console.log(`   ⚠️  Test file ${testFileName} not found in JSON, skipping update`);
                return;
            }

            existingResponse.files[fileIndex].code = healedCode;
            writeFileSync(jsonPath, JSON.stringify(existingResponse, null, 2));
            console.log(`   📄 Updated JSON file: ${jsonPath}`);
        } catch (e) {
            console.log(`   ⚠️  Failed to update JSON file: ${e instanceof Error ? e.message : String(e)}`);
        }
    };

    private typeCheckFile = (filePath: string): string[] => {
        console.log(`\n🔍 Type checking healed file...`);
        const errors: string[] = [];

        if (filePath.endsWith(".ts")) {
            const typeResult = this.typeScriptValidator.typeCheckFile(filePath);
            if (!typeResult.success) {
                console.log(`   ❌ Type errors in ${basename(filePath)}: ${typeResult.errorCount} error(s)`);
                errors.push(this.typeScriptValidator.formatTypeErrorsForModel(filePath, typeResult));
            } else {
                console.log(`   ✅ ${basename(filePath)} compiles successfully`);
            }
        }

        return errors;
    };

    private runPlaywrightTest = async (filePath: string): Promise<TestRunResult> => {
        console.log(`\n🔍 Running healed playwright test...`);
        return await this.playwrightTestRunner(this.baseUrl, [filePath]);
    };

    private recordAnalysisResult = (analysisResult: HealAnalysisResponse) => {
        const savePath = join(this.outputDir, `${this.featureName}.triage_analysis.json`);
        try {
            writeFileSync(savePath, JSON.stringify(analysisResult, null, 2));
        } catch (e) {
            console.error(`Failed to save analysis to ${savePath}. ${util.inspect(e)}`)
        }
    }
}

export const autoHealTestOrchestratorFactory = async (
    testFilePath: string,
    runLogDir: string,
    outputDir: string,
    model: ModelAlias,
    baseUrl: string,
    allowedDirectories: string[],
    fileMcpClient: ToolProvider,
    mcpPlaywrightClient: ToolProvider | undefined
): Promise<AutoHealTestOrchestrator> => {
    const playwrightClient = mcpPlaywrightClient == null ? await createPlaywrightClient({
        headless: true,
        baseUrl,
        browser: "chromium",
        artifacts: {
            outputDir: runLogDir,
            saveVideo: true,
            saveSession: true
        }
    }) : mcpPlaywrightClient;
    const memoryTool = localFilesystemMemoryToolFactory(outputDir);

    const toolProviders: ToolProvider[] = [fileMcpClient, playwrightClient, memoryTool];

    const promptGenerator = healPromptGeneratorFactory(baseUrl);
    const rawTools = toolProviders.flatMap(provider => provider.getTools());
    const modelClient = createModelClient(
        runLogDir,
        model,
        promptGenerator.buildSystemPrompt(),
        rawTools,
        HealAnalysisResponseSchema,
    );

    return new AutoHealTestOrchestrator(
        testFilePath,
        runLogDir,
        outputDir,
        baseUrl,
        model,
        modelClient,
        promptGenerator,
        allowedDirectories,
        toolProviders
    );
};

export type AutoHealTestOrchestratorFactory = typeof autoHealTestOrchestratorFactory;