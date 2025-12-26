import Anthropic from "@anthropic-ai/sdk";
import {join, resolve, basename} from "path";
import {writeFileSync, existsSync, readFileSync, unlinkSync} from "fs";
import {ApiLogEntry, ToolCall} from "@lib/test_generator/model_client.types";
import {GenerationResult, GeneratorOptions} from "@lib/test_generator/index.types";
import {Spec} from "@lib/test_generator/spec_parser.types";
import {createFilesystemClient, FilesystemMcpClient} from "@lib/test_generator/mcp_filesystem_client";
import {SYSTEM_PROMPT, buildUserPrompt, TEST_GENERATOR_OUTPUT_FORMAT} from "@lib/test_generator/context_generator";

import {
    Message,
    MessageParam,
    TextBlockParam,
    ToolResultBlockParam
} from "@anthropic-ai/sdk/resources/messages/messages";
import {GeneratedTestResponse} from "@lib/test_generator/model_client.response.types";
import {parseJsonResponse} from "@lib/test_generator/json_parser";
import {typeCheckFile, formatTypeErrorsForModel} from "@lib/test_generator/typescript_validator";
import {anthropicModelClientFactory, AnthropicModelClient} from "@lib/test_generator/anthropic_model_client";

const YAFFO_ROOT = resolve(join(process.cwd(), "../yaffo"));
const DEFAULT_OUTPUT_DIR = resolve(join(process.cwd(), "generated_tests"));

export class TestGeneratorOrchestrator {

    private iterationCount = 0;
    private maxIterations = 20;
    private maxRetries = 3;

    constructor(
        private spec: Spec,
        private runLogDir: string,
        private outputDir: string,
        private anthropic: AnthropicModelClient,
        private mcpClient: FilesystemMcpClient,
    ) {
        this.spec = spec;
        this.runLogDir = runLogDir;
        this.outputDir = outputDir ?? DEFAULT_OUTPUT_DIR;
    }

    generate = async (specPath: string, baseUrl: string, tempDir?: string): Promise<GenerationResult> => {
        try {
            const generatedJson = await this.runToolExplorationLoop(specPath, baseUrl);

            if (!generatedJson) {
                return {
                    success: false,
                    error: "No JSON response generated after tool exploration",
                    logPath: this.runLogDir
                };
            }

            return await this.parseAndValidateLoop(generatedJson);
        } finally {
            await this.mcpClient?.disconnect();
        }
    };


    private runToolExplorationLoop = async (specPath: string, baseUrl: string): Promise<string | null> => {
        if (this.mcpClient == null || this.anthropic == null) {
            throw new Error("Orchestrator not initialized");
        }
        this.iterationCount = 0;

        const tools = this.mcpClient.getToolsForClaude();
        const allowedDirs = this.mcpClient.getAllowedDirectories();
        const userPrompt = buildUserPrompt(this.spec, specPath, baseUrl, allowedDirs);
        this.anthropic.addMessage({role: "user", content: userPrompt})
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
    };

    private parseAndValidateLoop = async (generatedJson: string): Promise<GenerationResult> => {
        let retryCount = 0;
        let currentJson = generatedJson;
        let lastResponse: GeneratedTestResponse | null = null;

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
                const fixResult = await this.handleSchemaErrors(schemaErrors, currentJson, retryCount);
                if (fixResult.shouldBreak) {
                    lastResponse = parsedResponse;
                    break;
                }
                if (fixResult.newJson) {
                    currentJson = fixResult.newJson;
                    retryCount++;
                    continue;
                }
                break;
            }

            lastResponse = parsedResponse;
            const writtenPaths = this.writeGeneratedFiles(parsedResponse);
            this.logExplanation(parsedResponse);

            const typeErrors = this.typeCheckFiles(writtenPaths);

            if (typeErrors.length === 0) {
                console.log(`\n✅ All files compile successfully!`);
                return {success: true, logPath: this.runLogDir};
            }

            const fixResult = await this.handleTypeErrors(typeErrors, parsedResponse, currentJson, retryCount);
            if (fixResult.shouldBreak) {
                return {success: true, logPath: this.runLogDir};
            }
            if (fixResult.newJson) {
                currentJson = fixResult.newJson;
                retryCount++;
            } else {
                break;
            }
        }

        return {
            success: lastResponse !== null,
            logPath: this.runLogDir
        };
    };

    private handleSchemaErrors = async (
        schemaErrors: string[],
        currentJson: string,
        retryCount: number
    ): Promise<{ shouldBreak: boolean; newJson?: string }> => {
        console.log(`\n⚠️  Schema validation errors:`);
        schemaErrors.forEach(err => console.log(`   - ${err}`));

        if (retryCount + 1 > this.maxRetries) {
            console.log(`\n⚠️  Max retries (${this.maxRetries}) exceeded. Schema errors remain.`);
            return {shouldBreak: true};
        }

        console.log(`\n🔄 Schema fix attempt ${retryCount + 1}/${this.maxRetries}...`);

        const schemaFixRequest = `The JSON response has schema validation errors:

${schemaErrors.map(e => `- ${e}`).join("\n")}

Expected Schema:
\`\`\`typescript
${TEST_GENERATOR_OUTPUT_FORMAT}
\`\`\`
Please fix the schema errors and provide the corrected JSON.`;

        this.anthropic?.addMessages([
            {role: "assistant", content: currentJson},
            {role: "user", content: schemaFixRequest}
        ]);
        const fixResponse = await this.anthropic?.callModelApi();
        if (!fixResponse) {
            console.log(`   ❌ Failed to get fix response from model`);
            return {shouldBreak: true};
        }

        const fixTextContent = this.extractTextContent(fixResponse);
        if (fixTextContent) {
            return {shouldBreak: false, newJson: fixTextContent};
        }

        console.log(`   ❌ No text content in fix response`);
        return {shouldBreak: true};
    };

    private handleTypeErrors = async (
        typeErrors: string[],
        parsedResponse: GeneratedTestResponse,
        currentJson: string,
        retryCount: number
    ): Promise<{ shouldBreak: boolean; newJson?: string }> => {
        if (retryCount + 1 > this.maxRetries) {
            console.log(`\n⚠️  Max retries (${this.maxRetries}) exceeded. Returning with type errors.`);
            return {shouldBreak: true};
        }

        console.log(`\n🔄 Type error fix attempt ${retryCount + 1}/${this.maxRetries}...`);

        const typeFixRequest = `The generated TypeScript code has compilation errors:

${typeErrors.join("\n\n")}

Here is the current code that needs to be fixed:
\`\`\`typescript
${parsedResponse.files[0]?.code || ""}
\`\`\`

Please fix these errors and provide the corrected code in the same JSON format as before.`;

        this.anthropic?.addMessages(
            [{role: "assistant", content: currentJson},
            {role: "user", content: typeFixRequest}]
        )

        const fixResponse = await this.anthropic.callModelApi( );

        if (!fixResponse) {
            console.log(`   ❌ Failed to get fix response from model`);
            return {shouldBreak: true};
        }

        const fixTextContent = this.extractTextContent(fixResponse);
        if (fixTextContent) {
            return {shouldBreak: false, newJson: fixTextContent};
        }

        console.log(`   ❌ No text content in fix response`);
        return {shouldBreak: true};
    };



    private determineNextAction = async (response: Message): Promise<{
        success: boolean;
        continue: boolean;
        generatedJson?: string;
        toolUsages?: MessageParam[];
    }> => {
        console.log(`   Stop reason: ${response.stop_reason}`);

        if (response.stop_reason === "end_turn") {
            const textContent = this.extractTextContent(response);
            return {success: true, continue: false, generatedJson: textContent};
        }

        const toolCalls = this.extractToolCalls(response);
        if (response.stop_reason === "tool_use" && toolCalls.length > 0) {
            const toolUsages: MessageParam[] = [];
            toolUsages.push({role: "assistant", content: response.content});

            const toolResults: ToolResultBlockParam[] = [];
            for (const call of toolCalls) {
                console.log(`   🔧 Tool: ${call.name}(${JSON.stringify(call.input).slice(0, 100)}...)`);
                try {
                    const result = await this.mcpClient!.callTool(call.name, call.input);
                    toolResults.push({
                        type: "tool_result",
                        tool_use_id: call.id,
                        content: result?.content as TextBlockParam[],
                    });
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

    private extractToolCalls = (response: Message): ToolCall[] => {
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

    private extractTextContent = (response: Message): string => {
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
                const typeResult = typeCheckFile(filePath);
                if (!typeResult.success) {
                    console.log(`   ❌ Type errors in ${basename(filePath)}: ${typeResult.errorCount} error(s)`);
                    errors.push(formatTypeErrorsForModel(filePath, typeResult));
                } else {
                    console.log(`   ✅ ${basename(filePath)} compiles successfully`);
                }
            }
        }

        return errors;
    };

    private logExplanation = (response: GeneratedTestResponse): void => {
        if (response.explanation) {
            const confidenceStr = response.confidence != null
                ? `. Confidence: ${response.confidence * 100}%`
                : "";
            console.log(`📝 Explanation: ${response.explanation}${confidenceStr}`);
        }
    };
}

export const testGeneratorOrchestratorFactory = async(spec:Spec, runLogDir: string, tempDir?: string) => {
    const fileMcpClient = await createFilesystemClient(YAFFO_ROOT, DEFAULT_OUTPUT_DIR, tempDir);
    const anthropicModel = anthropicModelClientFactory(runLogDir, SYSTEM_PROMPT, fileMcpClient.getToolsForClaude());
    return new TestGeneratorOrchestrator(
        spec,
        runLogDir,
        DEFAULT_OUTPUT_DIR,
        anthropicModel,
        fileMcpClient
    )
}