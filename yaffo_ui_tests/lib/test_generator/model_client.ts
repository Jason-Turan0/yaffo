import Anthropic from "@anthropic-ai/sdk";
import {join, resolve, basename} from "path";
import {writeFileSync} from "fs";
import {ApiLogEntry, ToolCall} from "@lib/test_generator/model_client.types";
import {GenerationResult, GeneratorOptions} from "@lib/test_generator/index.types";
import {Spec} from "@lib/test_generator/spec_parser.types";
import {createFilesystemClient, FilesystemMcpClient} from "@lib/test_generator/mcp_filesystem_client";
import {SYSTEM_PROMPT, buildUserPrompt} from "@lib/test_generator/context_generator";

import {
    Message,
    MessageCreateParamsNonStreaming,
    MessageParam, TextBlockParam,
    ToolResultBlockParam
} from "@anthropic-ai/sdk/resources/messages/messages";
import {GeneratedTestResponse} from "@lib/test_generator/model_client.response.types";
import {parseJsonResponse} from "@lib/test_generator/json_parser";
import {typeCheckFile, formatTypeErrorsForModel} from "@lib/test_generator/typescript_validator";

const YAFFO_ROOT = resolve(join(process.cwd(), "../yaffo"));
const PLAYWRIGHT_TEST_DIR = resolve(join(process.cwd(), "generated_tests"));

const writeApiLog = (
    runLogDir: string,
    callIndex: number,
    entry: ApiLogEntry) => {
    const logPath = join(runLogDir, `${callIndex}_claude_api.json`)
    writeFileSync(logPath, JSON.stringify(entry, null, 2));
};

const extractToolCallsFromResponse = (response: Anthropic.Messages.Message) => {
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
    return toolCalls
}

const callModelApi = async (
    anthropic: Anthropic,
    param: MessageCreateParamsNonStreaming,
    callIndex: number,
    runLogDir: string): Promise<Message | undefined> => {
    let response: Anthropic.Message | undefined;
    const timestamp = new Date();
    try {
        response = await anthropic.messages.create(param);
        return response;
    } catch (e) {
        return undefined;
    } finally {
        const durationMs = Date.now() - timestamp.getDate();
        writeApiLog(runLogDir, callIndex, {
            timestamp: timestamp.toISOString(),
            durationMs,
            request: param,
            response,
            success: response != null,
        })
    }
};

const extractTextContent = (response: Message): string => {
    let textContent = "";
    for (const block of response.content) {
        if (block.type === "text") {
            textContent += block.text;
        }
    }
    return textContent;
};

const writeGeneratedFiles = (
    spec: Spec,
    response: GeneratedTestResponse,
    outputDir: string): string[] => {
    const writtenPaths: string[] = [];
    for (const file of response.files) {
        const outputPath = join(outputDir, basename(file.filename));
        writeFileSync(outputPath, file.code);
        console.log(`   📄 Written: ${outputPath}`);
        writtenPaths.push(outputPath);
    }
    writeFileSync(join(outputDir, `${spec.feature}.json`), JSON.stringify(response, null, 2));
    return writtenPaths;
};

const determineNextAction = async (response: Message, mcpClient: FilesystemMcpClient) => {
    console.log(`   Stop reason: ${response.stop_reason}`);

    if (response.stop_reason === "end_turn") {
        const textContent = extractTextContent(response);
        return {success: true, continue: false, generatedJson: textContent, error: undefined, toolUsages: []}
    }

    const toolCalls: ToolCall[] = extractToolCallsFromResponse(response);
    if (response.stop_reason === "tool_use" && toolCalls.length > 0) {
        const toolUsages = [];
        toolUsages.push({role: "assistant" as const, content: response.content});
        const toolResults: ToolResultBlockParam[] = [];
        for (const call of toolCalls) {
            console.log(`   🔧 Tool: ${call.name}(${JSON.stringify(call.input).slice(0, 100)}...)`);
            try {
                const result = await mcpClient.callTool(call.name, call.input);
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
        toolUsages.push({role: "user" as const, content: toolResults});
        return {
            success: true,
            continue: true,
            generatedJson: undefined,
            error: undefined,
            toolUsages
        }
    }
    throw new Error(`Unknown stop reason ${response.stop_reason}`);
};

export const generateTestFromSpec = async (
    options: GeneratorOptions,
    spec: Spec,
    runLogDir: string
): Promise<GenerationResult> => {
    const {
        specPath,
        baseUrl = "http://127.0.0.1:5000",
        model = "claude-sonnet-4-20250514",
        tempDir,
    } = options;
    let mcpClient: FilesystemMcpClient | null = null;

    mcpClient = await createFilesystemClient(YAFFO_ROOT, PLAYWRIGHT_TEST_DIR, tempDir);
    const tools = mcpClient.getToolsForClaude();
    const allowedDirs = mcpClient.getAllowedDirectories();

    console.log(`🔧 Tools available (read-only): ${tools.map((t) => t.name).join(", ")}`);

    const anthropic = new Anthropic();
    const userPrompt = buildUserPrompt(spec, specPath, baseUrl, allowedDirs);

    const messages: MessageParam[] = [{role: "user", content: userPrompt}];

    let iterationCount = 0;
    const maxIterations = 20;
    let generatedJson: string | null = null;


    while (iterationCount < maxIterations) {
        iterationCount++;
        console.log(`\n🔄 Iteration ${iterationCount}...`);
        const response = await callModelApi(
            anthropic,
            {
                model,
                max_tokens: 8192,
                system: SYSTEM_PROMPT,
                tools,
                messages,
            },
            iterationCount,
            runLogDir
        );
        if (response == null) {
            break
        }
        const nextAction = await determineNextAction(response, mcpClient);
        if (nextAction.generatedJson != null && nextAction.generatedJson.length > 0) {
            generatedJson = nextAction.generatedJson;
        }
        if (!nextAction.continue) {
            break;
        }

        if (nextAction.continue && nextAction?.toolUsages?.length) {
            messages.push(...nextAction?.toolUsages);
        }
    }

    await mcpClient.disconnect();

    if (!generatedJson) {
        return {
            success: false,
            error: "No JSON response generated after tool exploration",
            logPath: runLogDir
        };
    }

    // Parse and validate loop
    const maxCompileRetries = 3;
    let compileRetryCount = 0;
    let currentJson = generatedJson;
    let lastResponse: GeneratedTestResponse | null = null;

    while (compileRetryCount <= maxCompileRetries) {
        // Parse the JSON response
        const parsedResponse = parseJsonResponse(currentJson);
        if (!parsedResponse) {
            const rawPath = join(PLAYWRIGHT_TEST_DIR, `${spec.feature}.txt`);
            writeFileSync(rawPath, currentJson);
            return {
                success: false,
                error: `Failed to parse JSON response. Raw response saved to ${rawPath}`,
                logPath: runLogDir
            };
        }

        lastResponse = parsedResponse;
        const writtenPaths = writeGeneratedFiles(spec, parsedResponse, PLAYWRIGHT_TEST_DIR);

        if (parsedResponse.explanation) {
            console.log(`📝 Explanation: ${parsedResponse.explanation}. Confidence: ${parsedResponse.confidence * 100}%`);
        }

        // Type check the generated files
        console.log(`\n🔍 Type checking generated files...`);
        const allErrors: string[] = [];

        for (const filePath of writtenPaths) {
            if (filePath.endsWith('.ts')) {
                const typeResult = typeCheckFile(filePath);
                if (!typeResult.success) {
                    console.log(`   ❌ Type errors in ${basename(filePath)}: ${typeResult.errorCount} error(s)`);
                    allErrors.push(formatTypeErrorsForModel(filePath, typeResult));
                } else {
                    console.log(`   ✅ ${basename(filePath)} compiles successfully`);
                }
            }
        }

        if (allErrors.length === 0) {
            console.log(`\n✅ All files compile successfully!`);
            return {
                success: true,
                logPath: runLogDir
            };
        }

        // If we have errors and haven't exceeded retries, ask Claude to fix
        compileRetryCount++;
        if (compileRetryCount > maxCompileRetries) {
            console.log(`\n⚠️  Max compile retries (${maxCompileRetries}) exceeded. Returning with type errors.`);
            return {
                success: true,
                logPath: runLogDir
            };
        }

        console.log(`\n🔄 Compile fix attempt ${compileRetryCount}/${maxCompileRetries}...`);

        // Build the fix request message
        const fixRequest = `The generated TypeScript code has compilation errors:

${allErrors.join("\n\n")}

Here is the current code that needs to be fixed:
\`\`\`typescript
${parsedResponse.files[0]?.code || ""}
\`\`\`

Please fix these errors and provide the corrected code in the same JSON format as before.`;

        // Add the assistant's previous response and the fix request
        messages.push({role: "assistant", content: currentJson});
        messages.push({role: "user", content: fixRequest});

        // Call the model for a fix
        iterationCount++;
        const fixResponse = await callModelApi(
            anthropic,
            {
                model,
                max_tokens: 8192,
                system: SYSTEM_PROMPT,
                messages,
            },
            iterationCount,
            runLogDir
        );

        if (!fixResponse) {
            console.log(`   ❌ Failed to get fix response from model`);
            break;
        }

        const fixTextContent = extractTextContent(fixResponse);
        if (fixTextContent) {
            currentJson = fixTextContent;
        } else {
            console.log(`   ❌ No text content in fix response`);
            break;
        }
    }

    return {
        success: lastResponse !== null,
        logPath: runLogDir
    };
};