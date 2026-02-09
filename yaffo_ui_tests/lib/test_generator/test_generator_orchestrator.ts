import {join, resolve, basename} from "path";
import {writeFileSync, existsSync, readFileSync, unlinkSync, mkdirSync} from "fs";
import {GenerationResult} from "@lib/test_generator/index.types";
import {Spec} from "@lib/test_generator/prompt/spec_parser.types";
import {createFilesystemClient} from "@lib/tool_providers/mcp_filesystem_client";
import {promptGeneratorFactory, PromptGenerator} from "@lib/test_generator/prompt/prompt_generator";
import {GeneratedTestResponse} from "@lib/model_clients/model_client.response.types";
import {parseJsonResponse, GeneratedTestResponseSchema} from "@lib/test_generator/prompt/json_parser";
import {TypeScriptValidator, DefaultTypeScriptValidator} from "@lib/services/typescript_validator";
import {createModelClient, supportsNativeStructuredOutput} from "@lib/model_clients/model_client_factory";
import {zodToJsonSchema} from "zod-to-json-schema";
import {
    createPlaywrightClient,
    createStubPlaywrightClient,
} from "@lib/tool_providers/mcp_playwright_client";
import {RawToolDefinition, ToolProvider} from "@lib/tool_providers/toolprovider.types";
import {IsolatedEnvironment, startIsolatedEnvironment, TestRunResult} from "@lib/services/isolated_runner";
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
import {recordTestResult} from "@lib/test_generator/test_result_history";
import {buildTestFailurePrompt} from "@lib/test_generator/prompt/formatters";

const YAFFO_ROOT = resolve(join(process.cwd(), "../yaffo"));

export class TestGeneratorOrchestrator {
    private iterationCount = 0;
    private maxIterations = 100;
    private maxRetries = 5;
    private toolProviderMap: Map<string, { tool: RawToolDefinition; toolProvider: ToolProvider }> = new Map();

    constructor(
        private spec: Spec,
        private runLogDir: string,
        private outputDir: string,
        private baseUrl: string,
        private modelClient: ModelClient,
        private promptGenerator: PromptGenerator,
        private allowedDirectories: string[],
        private isolatedEnvironment: IsolatedEnvironment | null,
        private toolProviders: ToolProvider[],
        private typeScriptValidator: TypeScriptValidator,
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

    private specPath: string = "";

    generate = async (specPath: string, baseUrl: string): Promise<GenerationResult> => {
        this.specPath = specPath;
        try {
            const userPrompt = this.promptGenerator.buildUserPrompt(this.spec, specPath, baseUrl, this.allowedDirectories);
            this.modelClient.addUserMessage([toTextPart(userPrompt)]);
            const generatedJson = await this.generateTestCode();
            if (!generatedJson) {
                return {
                    success: false,
                    error: "Code Generation failed.",
                    logPath: this.runLogDir
                };
            }
            return await this.validateTestCode(generatedJson);
        } finally {
            for (const toolProvider of this.toolProviders) {
                await toolProvider.disconnect();
            }
            if (this.isolatedEnvironment != null) {
                await this.isolatedEnvironment.cleanup();
            }
        }
    };

    private generateTestCode = async (): Promise<string | null> => {
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

    private validateTestCode = async (originalJson: string): Promise<GenerationResult> => {
        let retryCount = 0;
        let currentJson = originalJson;

        while (retryCount <= this.maxRetries) {
            const {response: parsedResponse, schemaErrors} = parseJsonResponse<GeneratedTestResponse>(currentJson);

            if (schemaErrors.length > 0) {
                retryCount++;
                this.addSchemaErrorMessage(schemaErrors);
                const correctedJson = await this.generateTestCode();
                if (!correctedJson) {
                    return {
                        success: false,
                        error: `JSON schema errors in response.`,
                        logPath: this.runLogDir
                    };
                }
                currentJson = correctedJson;
                continue;
            }

            if (!parsedResponse) {
                const rawPath = join(this.outputDir, `${this.spec.feature}.txt`);
                writeFileSync(rawPath, currentJson);
                return {
                    success: false,
                    error: `Failed to parse JSON response. Raw response saved to ${rawPath}`,
                    logPath: this.runLogDir
                };
            }

            const writtenPaths = this.writeGeneratedFiles(parsedResponse);
            const typeErrors = this.typeCheckFiles(writtenPaths);

            if (typeErrors.length === 0) {
                console.log(`\n✅ All files compile successfully!`);
            } else {
                this.addCompileErrorMessage(typeErrors, parsedResponse);
                const correctedJson = await this.generateTestCode();
                if (!correctedJson) {
                    return {
                        success: false,
                        error: `Failed to fix typescript compilation error.`,
                        logPath: this.runLogDir
                    };
                } else {
                    currentJson = correctedJson;
                    continue;
                }
            }

            if (this.isolatedEnvironment != null) {
                const runResult = await this.runPlaywrightTests(writtenPaths);
                if (runResult == null || runResult.success) {
                    console.log(`\n✅ Playwright tests passed!`);
                    if (runResult) {
                        recordTestResult(this.outputDir, this.spec.feature, runResult);
                    }
                    return {
                        success: true,
                        logPath: this.runLogDir
                    };
                } else {
                    recordTestResult(this.outputDir, this.spec.feature, runResult);
                    this.addPlaywrightTestErrorMessage(runResult);
                    const correctedJson = await this.generateTestCode();
                    if (!correctedJson) {
                        return {
                            success: false,
                            error: `Failed to correct playwright test failures.`,
                            logPath: this.runLogDir
                        };
                    } else {
                        currentJson = correctedJson;
                    }
                    continue;
                }
            } else {
                console.log(`\n✅ Playwright tests disabled`);
                return {
                    success: true,
                    logPath: this.runLogDir
                };
            }
        }

        return {
            success: true,
            logPath: this.runLogDir
        };
    };

    private addSchemaErrorMessage = (schemaErrors: string[]): void => {
        schemaErrors.forEach(err => console.log(`   - ${err}`));
        const schemaFixPrompt = this.promptGenerator.buildSchemaFixPrompt(schemaErrors);
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
        testFailures: TestRunResult
    ): void => {
        const playwrightFailurePrompt = buildTestFailurePrompt(testFailures);
        this.modelClient.addUserMessage([toTextPart(playwrightFailurePrompt)]);
    }

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
                            toolCallId: call.toolCallId,
                            toolName: call.toolName,
                            result: `Error: No implementation for tool ${call.toolName}`,
                            isError: true,
                        });
                    } else {
                        const result = await toolTuple.toolProvider.callTool(call.toolName, call.input);
                        const resultText = typeof result === "string" ? result : result.text;
                        toolResults.push({
                            type: "tool_result",
                            toolCallId: call.toolCallId,
                            toolName: call.toolName,
                            result: resultText,
                        });
                    }
                } catch (e) {
                    const errorMsg = e instanceof Error ? e.message : String(e);
                    console.log(`   ❌ Tool error: ${errorMsg}`);
                    toolResults.push({
                        type: "tool_result",
                        toolCallId: call.toolCallId,
                        result: `Error: ${errorMsg}`,
                        toolName: call.toolName,
                        isError: true,
                    });
                }
            }
            this.modelClient.addToolResultMessage(toolResults.map(result => toToolResultPart(result)));
            return {success: true, continue: true};
        }

        throw new Error(`Unknown finish reason ${response.finishReason}`);
    };

    private writeGeneratedFiles = (response: GeneratedTestResponse): string[] => {
        this.cleanupOldGeneratedFiles();

        const writtenPaths: string[] = [];
        for (const file of response.files) {
            const outputPath = join(this.outputDir, basename(file.filename));
            writeFileSync(outputPath, file.code);
            console.log(`   📄 Written: ${outputPath}`);
            writtenPaths.push(outputPath);
        }

        writeFileSync(
            join(this.outputDir, `${this.spec.feature}.json`),
            JSON.stringify(response, null, 2)
        );

        return writtenPaths;
    };

    private cleanupOldGeneratedFiles = (): void => {
        const jsonPath = join(this.outputDir, `${this.spec.feature}.json`);
        if (!existsSync(jsonPath)) {
            return;
        }

        try {
            const existingJson = readFileSync(jsonPath, "utf-8");
            const existingResponse = JSON.parse(existingJson) as GeneratedTestResponse;

            for (const file of existingResponse.files) {
                const filePath = join(this.outputDir, basename(file.filename));
                if (existsSync(filePath)) {
                    unlinkSync(filePath);
                    console.log(`   🗑️  Deleted old file: ${filePath}`);
                }
            }
        } catch (e) {
            console.log(`   ⚠️  Could not parse existing JSON for cleanup: ${e instanceof Error ? e.message : String(e)}`);
        }
    };

    private typeCheckFiles = (filePaths: string[]): string[] => {
        console.log(`\n🔍 Type checking generated files...`);
        const errors: string[] = [];

        for (const filePath of filePaths) {
            if (filePath.endsWith(".ts")) {
                const typeResult = this.typeScriptValidator.typeCheckFile(filePath);
                if (!typeResult.success) {
                    console.log(`   ❌ Type errors in ${basename(filePath)}: ${typeResult.errorCount} error(s)`);
                    errors.push(this.typeScriptValidator.formatTypeErrorsForModel(filePath, typeResult));
                } else {
                    console.log(`   ✅ ${basename(filePath)} compiles successfully`);
                }
            }
        }

        return errors;
    };

    private runPlaywrightTests = async (filePaths: string[]): Promise<TestRunResult | null> => {
        if (this.isolatedEnvironment == null) return null;
        console.log(`\n🔍 Running playwright tests...`);
        const toRun = filePaths.filter(path => path.endsWith(".ts"));
        return await this.playwrightTestRunner(this.baseUrl, toRun);
    };
}

export const testGeneratorOrchestratorFactory = async (
    spec: Spec,
    runLogDir: string,
    outputDir: string,
    model: ModelAlias,
    baseUrl: string,
    runTestEnvironment: boolean,
    port: number,
) => {
    let isolatedEnvironment: IsolatedEnvironment | null = null;
    const allowedDirectories = [YAFFO_ROOT, outputDir];
    if (runTestEnvironment) {
        isolatedEnvironment = await startIsolatedEnvironment(port);
        allowedDirectories.push(isolatedEnvironment.tempDir);
    }

    const fileMcpClient = await createFilesystemClient(allowedDirectories, {useDocker: true});
    const mcpPlaywrightClient = runTestEnvironment ? await createPlaywrightClient({
        headless: true,
        baseUrl,
        browser: "chromium",
        artifacts: {
            outputDir: runLogDir,
            saveVideo: true,
            saveSession: true
        }
    }) : await createStubPlaywrightClient();
    const memoryTool = localFilesystemMemoryToolFactory(outputDir);

    const toolProviders: ToolProvider[] = [fileMcpClient, mcpPlaywrightClient, memoryTool];

    const promptGenerator = promptGeneratorFactory(runTestEnvironment, baseUrl, YAFFO_ROOT, outputDir, spec);
    const outputSchemaStr = supportsNativeStructuredOutput(model)
        ? undefined
        : JSON.stringify(zodToJsonSchema(GeneratedTestResponseSchema), null, 2);
    const rawTools = toolProviders.flatMap(provider => provider.getTools());
    const modelClient = createModelClient(
        runLogDir,
        model,
        await promptGenerator.getSystemPrompt(outputSchemaStr),
        rawTools,
        GeneratedTestResponseSchema,
    );

    return new TestGeneratorOrchestrator(
        spec,
        runLogDir,
        outputDir,
        baseUrl,
        modelClient,
        promptGenerator,
        allowedDirectories,
        isolatedEnvironment,
        toolProviders,
        new DefaultTypeScriptValidator(),
    );
};