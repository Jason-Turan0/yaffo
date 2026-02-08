import {join, resolve, basename} from "path";
import {writeFileSync, readFileSync, existsSync} from "fs";
import {createFilesystemClient} from "@lib/test_generator/mcp_filesystem_client";
import {HealPromptGenerator, HealContext, healPromptGeneratorFactory} from "@lib/test_generator/heal_prompt_generator";
import {parseSpecFile} from "@lib/test_generator/spec_parser";
import {GeneratedTestResponse} from "@lib/model_clients/model_client.response.types";
import {parseJsonResponse, GeneratedTestResponseSchema} from "@lib/test_generator/json_parser";
import {TypeScriptValidator, DefaultTypeScriptValidator} from "@lib/test_generator/typescript_validator";
import {createModelClient, supportsNativeStructuredOutput} from "@lib/model_clients/model_client_factory";
import {zodToJsonSchema} from "zod-to-json-schema";
import {createPlaywrightClient} from "@lib/test_generator/mcp_playwright_client";
import {RawToolDefinition, ToolProvider} from "@lib/test_generator/toolprovider.types";
import {TestRunResult} from "@lib/test_generator/isolated_runner";
import {
    ModelAlias,
    ModelClient,
    ModelResponse,
    ToolCallResult,
    toTextPart,
    toToolResultPart
} from "@lib/model_clients/model_client.interface";
import {localFilesystemMemoryToolFactory} from "@lib/test_generator/local_filesystem_memory_tool";
import {runPlaywrightTests, PlaywrightTestRunner} from "@lib/test_generator/run_playwright_tests";

const YAFFO_ROOT = resolve(join(process.cwd(), "../yaffo"));

export interface HealResult {
    success: boolean;
    testFilePath: string;
    logPath: string;
    error?: string;
    iterations: number;
}

export class AutoHealTestOrchestrator {
    private iterationCount = 0;
    private maxIterations = 50;
    private maxRetries = 3;
    private toolProviderMap: Map<string, { tool: RawToolDefinition; toolProvider: ToolProvider }> = new Map();
    private featureName: string = "";

    constructor(
        private absoluteTestFilePath: string,
        private runLogDir: string,
        private outputDir: string,
        private baseUrl: string,
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

    healTest = async (testFailures: TestRunResult, specPath: string): Promise<HealResult> => {
        try {
            const testCode = readFileSync(this.absoluteTestFilePath, "utf-8");
            const spec = parseSpecFile(specPath);
            this.featureName = spec.feature;
            const initialTestResponse = this.loadJsonFile();

            const healContext: HealContext = {
                absoluteTestFilePath: this.absoluteTestFilePath,
                testCode,
                testFailures,
                specPath,
                spec,
                testContext: initialTestResponse?.testContext || '',
                explanation: initialTestResponse?.explanation || '',
                testDescription: initialTestResponse?.files?.find(f => resolve(join(this.outputDir, f.filename)) === this.absoluteTestFilePath)?.description || '',
            };

            const userPrompt = this.promptGenerator.buildHealPrompt(healContext, this.allowedDirectories);
            this.modelClient.addUserMessage([toTextPart(userPrompt)]);

            const generatedJson = await this.generateHealedCode();
            if (!generatedJson) {
                return {
                    success: false,
                    testFilePath: this.absoluteTestFilePath,
                    error: "Heal code generation failed.",
                    logPath: this.runLogDir,
                    iterations: this.iterationCount,
                };
            }

            return await this.validateHealedCode(generatedJson);
        } finally {
            for (const toolProvider of this.toolProviders) {
                await toolProvider.disconnect();
            }
        }
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
                this.addCompileErrorMessage(typeErrors, parsedResponse, currentJson);
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
            this.addPlaywrightTestErrorMessage(runResult, parsedResponse, currentJson);
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
        currentJson: string,
    ): void => {
        const currentCode = parsedResponse.files[0]?.code || "";
        const typeFixPrompt = this.promptGenerator.buildTypeErrorFixPrompt(typeErrors, currentCode);

        this.modelClient.addUserMessage([toTextPart(typeFixPrompt)]);
    };

    private addPlaywrightTestErrorMessage = (
        testFailures: TestRunResult,
        parsedResponse: GeneratedTestResponse,
        currentJson: string,
    ): void => {
        const currentCode = parsedResponse.files[0]?.code || "";
        const playwrightFailurePrompt = this.promptGenerator.buildTestFailurePrompt(testFailures, currentCode);
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
            const toolResults: ToolCallResult[] = [];
            for (const call of response.toolCalls) {
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
}

export const autoHealTestOrchestratorFactory = async (
    testFilePath: string,
    runLogDir: string,
    outputDir: string,
    model: ModelAlias,
    baseUrl: string,
    tempDir: string,
    fileMcpClient1: ToolProvider | undefined,
    mcpPlaywrightClient1: ToolProvider | undefined
): Promise<AutoHealTestOrchestrator> => {
    const allowedDirectories = [YAFFO_ROOT, outputDir, tempDir];

    const fileMcpClient = fileMcpClient1 == null ? await createFilesystemClient(allowedDirectories): fileMcpClient1;
    const mcpPlaywrightClient = mcpPlaywrightClient1 == null ? await createPlaywrightClient({
        headless: true,
        baseUrl,
        browser: "chromium",
        artifacts: {
            outputDir: runLogDir,
            saveVideo: true,
            saveSession: true
        }
    }): mcpPlaywrightClient1;
    const memoryTool = localFilesystemMemoryToolFactory(outputDir);

    const toolProviders: ToolProvider[] = [fileMcpClient, mcpPlaywrightClient, memoryTool];

    const promptGenerator = healPromptGeneratorFactory(baseUrl);
    const outputSchemaStr = supportsNativeStructuredOutput(model)
        ? undefined
        : JSON.stringify(zodToJsonSchema(GeneratedTestResponseSchema), null, 2);
    const rawTools = toolProviders.flatMap(provider => provider.getTools());
    const modelClient = createModelClient(
        runLogDir,
        model,
        await promptGenerator.buildSystemPrompt(outputSchemaStr),
        rawTools,
        GeneratedTestResponseSchema,
    );

    return new AutoHealTestOrchestrator(
        testFilePath,
        runLogDir,
        outputDir,
        baseUrl,
        modelClient,
        promptGenerator,
        allowedDirectories,
        toolProviders
    );
};

export type AutoHealTestOrchestratorFactory = typeof autoHealTestOrchestratorFactory;