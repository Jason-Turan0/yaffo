import {join, resolve, basename} from "path";
import {writeFileSync, readFileSync, existsSync} from "fs";
import {ToolCall} from "@lib/test_generator/model_client.types";
import {createFilesystemClient} from "@lib/test_generator/mcp_filesystem_client";
import {HealPromptGenerator, HealContext, healPromptGeneratorFactory} from "@lib/test_generator/heal_prompt_generator";
import {parseSpecFile} from "@lib/test_generator/spec_parser";
import {GeneratedTestResponse} from "@lib/test_generator/model_client.response.types";
import {parseJsonResponse, GeneratedTestResponseSchema} from "@lib/test_generator/json_parser";
import {zodToJsonSchema} from "zod-to-json-schema";
import {TypeScriptValidator, DefaultTypeScriptValidator} from "@lib/test_generator/typescript_validator";
import {
    anthropicModelClientFactory,
    AnthropicModelClient,
    AnthropicModelAlias
} from "@lib/test_generator/anthropic_model_client";
import {createPlaywrightClient} from "@lib/test_generator/mcp_playwright_client";
import {ToolProvider} from "@lib/test_generator/toolprovider.types";
import {startIsolatedEnvironment, TestRunResult} from "@lib/test_generator/isolated_runner";
import {
    BetaMessage,
    BetaMessageParam,
    BetaTool,
    BetaToolResultBlockParam
} from "@anthropic-ai/sdk/resources/beta";
import {localFilesystemMemoryToolFactory} from "@lib/test_generator/local_filesystem_memory_tool";
import {runPlaywrightTests} from "@lib/test_generator/run_playwright_tests";

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
    private toolProviderMap: Map<string, { tool: BetaTool; toolProvider: ToolProvider }> = new Map();
    private featureName: string = "";

    constructor(
        private absoluteTestFilePath: string,
        private runLogDir: string,
        private outputDir: string,
        private baseUrl: string,
        private anthropic: AnthropicModelClient,
        private promptGenerator: HealPromptGenerator,
        private allowedDirectories: string[],
        private toolProviders: ToolProvider[],
        private typeScriptValidator: TypeScriptValidator = new DefaultTypeScriptValidator(),
    ) {
        const tools = toolProviders.flatMap(toolProvider =>
            toolProvider.getToolsForClaude().map((tool) => ({tool, toolProvider}))
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
            const initialTestResponse = this.loadJsonFile()

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
            this.anthropic.addMessage({role: "user", content: [{type: "text", text: userPrompt}]});

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
        this.anthropic.addMessages([
            {role: "assistant", content: currentJson},
            {role: "user", content: [{type: "text", text: schemaFixPrompt}]}
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
            {role: "assistant", content: [{type: "text", text: currentJson}]},
            {role: "user", content: [{type: "text", text: typeFixPrompt}]}
        ]);
    };

    private addPlaywrightTestErrorMessage = (
        testFailures: TestRunResult,
        parsedResponse: GeneratedTestResponse,
        currentJson: string,
    ): void => {
        const currentCode = parsedResponse.files[0]?.code || "";
        const playwrightFailurePrompt = this.promptGenerator.buildTestFailurePrompt(testFailures, currentCode);
        this.anthropic.addMessages([
            {role: "assistant", content: [{type: "text", text: currentJson}]},
            {role: "user", content: [{type: "text", text: playwrightFailurePrompt}]}
        ]);
    };

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
                console.log(`   🔧 Tool: ${call.name} Id: ${call.id} (${JSON.stringify(call.input).slice(0, 50)}...)`);
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
                            content: (typeof result === "string") ? [{type: "text", text: result}] : [result]
                        });
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
    }

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
        return await runPlaywrightTests(this.baseUrl, [filePath]);
    };
}

export const autoHealTestOrchestratorFactory = async (
    testFilePath: string,
    runLogDir: string,
    outputDir: string,
    model: AnthropicModelAlias,
    baseUrl: string,
    tempDir: string,
): Promise<AutoHealTestOrchestrator> => {
    const allowedDirectories = [YAFFO_ROOT, outputDir, tempDir];

    const fileMcpClient = await createFilesystemClient(allowedDirectories);
    const mcpPlaywrightClient = await createPlaywrightClient({
        headless: true,
        baseUrl,
        browser: "chromium",
        artifacts: {
            outputDir: runLogDir,
            saveVideo: true,
            saveSession: true
        }
    });
    const memoryTool = localFilesystemMemoryToolFactory(outputDir);

    const toolProviders: ToolProvider[] = [fileMcpClient, mcpPlaywrightClient, memoryTool];

    const promptGenerator = healPromptGeneratorFactory(baseUrl);
    const tools = toolProviders.flatMap(provider => provider.getToolsForClaude());
    const outputSchema = zodToJsonSchema(GeneratedTestResponseSchema);
    const anthropicModel = anthropicModelClientFactory(
        runLogDir,
        model,
        await promptGenerator.buildSystemPrompt(),
        tools,
        outputSchema,
    );

    return new AutoHealTestOrchestrator(
        testFilePath,
        runLogDir,
        outputDir,
        baseUrl,
        anthropicModel,
        promptGenerator,
        allowedDirectories,
        toolProviders
    );
};

export type AutoHealTestOrchestratorFactory = typeof autoHealTestOrchestratorFactory;