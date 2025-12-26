import {Message, MessageParam} from "@anthropic-ai/sdk/resources/messages/messages";
import Anthropic from "@anthropic-ai/sdk";
import {Spec} from "@lib/test_generator/spec_parser.types";
import {FilesystemMcpClient} from "@lib/test_generator/mcp_filesystem_client";
import {writeFileSync, existsSync, readFileSync, unlinkSync} from "fs";
import {ApiLogEntry} from "@lib/test_generator/model_client.types";
import {join} from "path";
import {SYSTEM_PROMPT} from "@lib/test_generator/context_generator";

export class AnthropicModelClient {
    private messages: MessageParam[];
    private anthropic: Anthropic;
    private apiCallCount: number

    constructor(
        private runLogDir: string,
        private model: string,
        private systemPrompt: string,
        private tools:  Anthropic.Messages.Tool[],
        anthropicFactory: () => Anthropic,

    ) {
        this.runLogDir = runLogDir;
        this.model = model ?? "claude-sonnet-4-20250514";
        this.messages = [];
        this.anthropic = anthropicFactory()
        this.apiCallCount = 0;
    }

    public addMessage = (message: MessageParam) => {
        this.messages.push(message);
    }

    public addMessages = (message: MessageParam[]) => {
        this.messages.push(...message);
    }

    public callModelApi = async (): Promise<Message | undefined> => {
        let response: Anthropic.Message | undefined;
        const timestamp = new Date();
        const params = {
                model: this.model,
                max_tokens: 8192,
                system: this.systemPrompt,
                tools: this.tools,
                messages: this.messages,
        };

        try {
            response = await this?.anthropic?.messages?.create(params);
            return response;
        } catch {
            return undefined;
        } finally {
            const durationMs = Date.now() - timestamp.getTime();
            this.writeApiLog({
                timestamp: timestamp.toISOString(),
                durationMs,
                request: params,
                response,
                success: response != null,
            });
            this.apiCallCount += 1;
        }
    };

    private writeApiLog = (entry: ApiLogEntry): void => {
        const logPath = join(this.runLogDir, `${this.apiCallCount}_claude_api.json`);
        writeFileSync(logPath, JSON.stringify(entry, null, 2));
    };

}

export const anthropicModelClientFactory =  (
    runLogDir: string,
    systemPrompt: string,
    tools:  Anthropic.Messages.Tool[],
) => {
    return new AnthropicModelClient(runLogDir, "claude-sonnet-4-20250514", systemPrompt, tools, () => new Anthropic())
}