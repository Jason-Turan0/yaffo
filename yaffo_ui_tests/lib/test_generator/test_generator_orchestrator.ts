import {join, resolve, basename} from "path";
import {writeFileSync, existsSync, readFileSync, unlinkSync} from "fs";
import {ToolCall} from "@lib/test_generator/model_client.types";
import {GenerationResult} from "@lib/test_generator/index.types";
import {Spec} from "@lib/test_generator/spec_parser.types";
import {createFilesystemClient, FilesystemMcpClient} from "@lib/test_generator/mcp_filesystem_client";
import {promptGeneratorFactory, PromptGenerator} from "@lib/test_generator/prompt_generator";

import {GeneratedTestResponse} from "@lib/test_generator/model_client.response.types";
import {parseJsonResponse} from "@lib/test_generator/json_parser";
import {TypeScriptValidator, DefaultTypeScriptValidator} from "@lib/test_generator/typescript_validator";
import {
    anthropicModelClientFactory,
    AnthropicModelClient,
    AnthropicModelAlias
} from "@lib/test_generator/anthropic_model_client";
import {
    createPlaywrightClient,
    createStubPlaywrightClient,
} from "@lib/test_generator/mcp_playwright_client";
import {ToolProvider} from "@lib/test_generator/toolprovider.types";
import {IsolatedEnvironment, runPlaywrightTests, startIsolatedEnvironment} from "@lib/test_generator/isolated_runner";
import {
    BetaMessage,
    BetaMessageParam,
    BetaTool,
    BetaToolResultBlockParam
} from "@anthropic-ai/sdk/resources/beta";
import {localFilesystemMemoryToolFactory} from "@lib/test_generator/local_filesystem_memory_tool";

const YAFFO_ROOT = resolve(join(process.cwd(), "../yaffo"));

export class TestGeneratorOrchestrator {
    private iterationCount = 0;
    private maxIterations = 20;
    private maxRetries = 3;
    private toolProviderMap: Map<string, { tool: BetaTool, toolProvider: ToolProvider }> = new Map<string, {
        tool: BetaTool;
        toolProvider: ToolProvider
    }>()

    constructor(
        private spec: Spec,
        private runLogDir: string,
        private outputDir: string,
        private baseUrl: string,
        private anthropic: AnthropicModelClient,
        private promptGenerator: PromptGenerator,
        private allowedDirectories: string[],
        private isolatedEnvironment: IsolatedEnvironment | null,
        private toolProviders: ToolProvider[],
        private typeScriptValidator: TypeScriptValidator = new DefaultTypeScriptValidator(),
    ) {
        this.spec = spec;
        this.runLogDir = runLogDir;
        this.outputDir = outputDir;
        const tools = toolProviders.flatMap(toolProvider => toolProvider.getToolsForClaude().map((tool) => ({
            tool,
            toolProvider
        })));

        for (const tool of tools) {
            if (this.toolProviderMap.has(tool.tool.name)) {
                throw new Error(`Duplicate tool names ${tool.tool.name}`)
            }
            this.toolProviderMap.set(tool.tool.name, tool);
        }
    }

    generate = async (specPath: string, baseUrl: string, tempDir?: string): Promise<GenerationResult> => {
        try {
            const userPrompt = this.promptGenerator.buildUserPrompt(this.spec, specPath, baseUrl, this.allowedDirectories);
            this.anthropic.addMessage({
                role: "user", content: [
                    {type: "text", text: userPrompt}
                ]
            });
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
                await toolProvider.disconnect()
            }
            if (this.isolatedEnvironment != null) {
                await this.isolatedEnvironment.cleanup()
            }
        }
    }
    ;

    private generateTestCode = async () => {
        let generatedJson: string | null = null;
        while (this.iterationCount < this.maxIterations) {
            this.iterationCount++;
            console.log(`\n🔄 Iteration ${this.iterationCount}...`);

            const response = await this.anthropic.callModelApi();

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

            if (nextAction.toolUsages?.length) {
                this.anthropic.addMessages(nextAction.toolUsages);
            }
        }
        return generatedJson;
    }

    private validateTestCode = async (originalJson: string): Promise<GenerationResult> => {
        let retryCount = 0;
        let currentJson = originalJson;

        while (retryCount <= this.maxRetries) {
            const {response: parsedResponse, schemaErrors} = parseJsonResponse(currentJson);

            if (!parsedResponse) {
                const rawPath = join(this.outputDir, `${this.spec.feature}.txt`);
                writeFileSync(rawPath, currentJson);
                return {
                    success: false,
                    error: `Failed to parse JSON response. Raw response saved to ${rawPath}`,
                    logPath: this.runLogDir
                };
            }

            if (schemaErrors.length > 0) {
                this.addSchemaErrorMessage(schemaErrors, currentJson);
                const correctedJson = await this.generateTestCode();
                if (!correctedJson) {
                    return {
                        success: false,
                        error: `Failed to json schema errors in response.`,
                        logPath: this.runLogDir
                    };
                } else {
                    currentJson = correctedJson;
                    continue;
                }
            }

            const writtenPaths = this.writeGeneratedFiles(parsedResponse);
            const typeErrors = this.typeCheckFiles(writtenPaths);

            if (typeErrors.length === 0) {
                console.log(`\n✅ All files compile successfully!`);
            } else {
                this.addCompileErrorMessage(typeErrors, parsedResponse, currentJson);
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
                const testFailures = await this.runPlaywrightTests(writtenPaths);
                if (testFailures.length === 0) {
                    console.log(`\n✅ Playwright tests passed!`);
                    return {
                        success: true,
                        logPath: this.runLogDir
                    }
                } else {
                    this.addPlaywrightTestErrorMessage(testFailures, parsedResponse, currentJson);
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
                }
            } else {
                console.log(`\n✅ Playwright tests disabled`);
                return {
                    success: true,
                    logPath: this.runLogDir
                }
            }
        }

        return {
            success: true,
            logPath: this.runLogDir
        };
    };

    private addSchemaErrorMessage = (
        schemaErrors: string[],
        currentJson: string
    ): void => {
        schemaErrors.forEach(err => console.log(`   - ${err}`));
        const schemaFixPrompt = this.promptGenerator.buildSchemaFixPrompt(schemaErrors);
        this.anthropic.addMessages([
            {role: "assistant", content: currentJson},
            {role: "user", content: schemaFixPrompt}
        ]);
    };

    private addCompileErrorMessage = (
        typeErrors: string[],
        parsedResponse: GeneratedTestResponse,
        currentJson: string,
    ): void => {
        const currentCode = parsedResponse.files[0]?.code || "";
        const typeFixPrompt = this.promptGenerator.buildTypeErrorFixPrompt(typeErrors, currentCode);
        this.anthropic.addMessages([
            {role: "assistant", content: currentJson},
            {role: "user", content: typeFixPrompt}
        ]);
    };

    private addPlaywrightTestErrorMessage = (
        testFailures: string[],
        parsedResponse: GeneratedTestResponse,
        currentJson: string,
    ): void => {
        const currentCode = parsedResponse.files[0]?.code || "";
        const typeFixPrompt = this.promptGenerator.buildTestFailurePrompt(testFailures, currentCode);
        this.anthropic.addMessages([
            {role: "assistant", content: currentJson},
            {role: "user", content: typeFixPrompt}
        ]);
    }


    private determineNextAction = async (response: BetaMessage): Promise<{
        success: boolean;
        continue: boolean;
        generatedJson?: string;
        toolUsages?: BetaMessageParam[];
    }> => {
        console.log(`   Stop reason: ${response.stop_reason}`);

        if (response.stop_reason === "end_turn") {
            const textContent = this.extractTextContent(response);
            return {success: true, continue: false, generatedJson: textContent};
        }

        const toolCalls = this.extractToolCalls(response);
        if (response.stop_reason === "tool_use" && toolCalls.length > 0) {
            const toolUsages: BetaMessageParam[] = [];
            toolUsages.push({role: "assistant", content: response.content});

            const toolResults: BetaToolResultBlockParam[] = [];
            for (const call of toolCalls) {
                console.log(`   🔧 Tool: ${call.name}(${JSON.stringify(call.input).slice(0, 100)}...)`);
                try {
                    const toolTuple = this.toolProviderMap.get(call.name);
                    if (!toolTuple) {
                        toolResults.push({
                            type: "tool_result",
                            tool_use_id: call.id,
                            content: `Error: No implementation for tool ${call.name}`,
                            is_error: true,
                        });
                    } else {
                        const result = await toolTuple.toolProvider.callTool(call.name, call.input);
                        toolResults.push({
                            type: "tool_result",
                            tool_use_id: call.id,
                            content: (typeof result === 'string') ? result : [result]
                        });
                        console.log(`Tool ${call.name} Used. ID: ${call.id}`)
                    }
                } catch (e) {
                    const errorMsg = e instanceof Error ? e.message : String(e);
                    console.log(`   ❌ Tool error: ${errorMsg}`);
                    toolResults.push({
                        type: "tool_result",
                        tool_use_id: call.id,
                        content: `Error: ${errorMsg}`,
                        is_error: true,
                    });
                }
            }

            toolUsages.push({role: "user", content: toolResults});
            return {success: true, continue: true, toolUsages};
        }

        throw new Error(`Unknown stop reason ${response.stop_reason}`);
    };

    private extractToolCalls = (response: BetaMessage): ToolCall[] => {
        const toolCalls: ToolCall[] = [];
        for (const block of response.content) {
            if (block.type === "tool_use") {
                toolCalls.push({
                    id: block.id,
                    name: block.name,
                    input: block.input as Record<string, unknown>,
                });
            }
        }
        return toolCalls;
    };

    private extractTextContent = (response: BetaMessage): string => {
        let textContent = "";
        for (const block of response.content) {
            if (block.type === "text") {
                textContent += block.text;
            }
        }
        return textContent;
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

    private runPlaywrightTests = async (filePaths: string[]): Promise<string[]> => {
        if (this.isolatedEnvironment == null) return [];

        console.log(`\n🔍 Running playwright tests...`);
        const errors: string[] = [];

        const toRun = filePaths.filter(path => path.endsWith(".ts"));
        const result = await runPlaywrightTests(this.baseUrl, toRun);

        if (result.success) {
            return []
        }
        return [result.output]
    };

}

export const
    testGeneratorOrchestratorFactory = async (
        spec: Spec,
        runLogDir: string,
        outputDir: string,
        model: AnthropicModelAlias,
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

        const fileMcpClient = await createFilesystemClient(allowedDirectories);
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
        const tools = toolProviders.flatMap(provider => provider.getToolsForClaude());
        const anthropicModel = anthropicModelClientFactory(
            runLogDir,
            model,
            promptGenerator.getSystemPrompt(),
            tools,
        );

        return new TestGeneratorOrchestrator(
            spec,
            runLogDir,
            outputDir,
            baseUrl,
            anthropicModel,
            promptGenerator,
            allowedDirectories,
            isolatedEnvironment,
            toolProviders
        );
    };