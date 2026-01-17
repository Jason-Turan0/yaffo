/**
 * MCP Client - Manages connection to filesystem MCP server
 *
 * Provides Claude with tools to read source code, templates, and routes
 * for generating accurate Playwright tests.
 */

import {Client} from "@modelcontextprotocol/sdk/client/index.js";
import {StdioClientTransport} from "@modelcontextprotocol/sdk/client/stdio.js";
import type {Tool} from "@anthropic-ai/sdk/resources/messages.js";
import {CallToolReturn, ToolProvider} from "@lib/test_generator/toolprovider.types";

export interface McpClientOptions {
    allowedDirectories: string[];
}

export interface McpTool {
    name: string;
    description: string;
    inputSchema: Record<string, unknown>;
}

const MAX_TOOL_RESULT_CHARS = 20000;

const WRITE_TOOLS = [
    "write_file",
    "edit_file",
    "create_directory",
    "move_file",
    "delete_file",
];

export const truncateToolResult = (result: string): string => {
    if (result.length <= MAX_TOOL_RESULT_CHARS) {
        return result;
    }
    const truncated = result.slice(0, MAX_TOOL_RESULT_CHARS);
    const truncatedMsg = `\n\n[TRUNCATED: Result was ${result.length} chars, showing first ${MAX_TOOL_RESULT_CHARS}]`;
    return truncated + truncatedMsg;
};

export class FilesystemMcpClient {
    private client: Client | null = null;
    private transport: StdioClientTransport | null = null;
    private tools: McpTool[] = [];
    private readonly allowedDirectories: string[];

    constructor(options: McpClientOptions) {
        this.allowedDirectories = options.allowedDirectories;
    }

    async connect(): Promise<void> {
        this.transport = new StdioClientTransport({
            command: "npx",
            args: [
                "-y",
                "@modelcontextprotocol/server-filesystem",
                ...this.allowedDirectories,
            ],
        });

        this.client = new Client(
            {
                name: "yaffo-test-generator",
                version: "1.0.0",
            },
            {
                capabilities: {},
            }
        );

        await this.client.connect(this.transport);

        const toolsResult = await this.client.listTools();
        this.tools = toolsResult.tools.map((t) => ({
            name: t.name,
            description: t.description || "",
            inputSchema: t.inputSchema as Record<string, unknown>,
        }));

        console.log(`🔌 MCP connected. Available tools: ${this.tools.map((t) => t.name).join(", ")}`);
    }

    async disconnect(): Promise<void> {
        if (this.client) {
            await this.client.close();
            this.client = null;
            this.transport = null;
            console.log("🔌 MCP disconnected");
        }
    }

    getToolsForClaude(): Tool[] {
        return this.tools
            .filter((tool) => !WRITE_TOOLS.includes(tool.name))
            .map((tool) => ({
                name: tool.name,
                description: tool.description,
                input_schema: tool.inputSchema as Tool["input_schema"],
            }));
    }

    getToolNames(): string[] {
        return this.tools.map((t) => t.name);
    }

    async callTool(name: string, args: Record<string, unknown>): Promise<CallToolReturn> {
        if (!this.client) {
            throw new Error("MCP client not connected");
        }

        const result = await this.client.callTool({name, arguments: args});
        const contentArray = result?.content as Array<{ type: string; text?: string }> | undefined;
        let contentText: string = contentArray
            ?.filter(block => block.type === 'text')
            .map(block => block.text || '')
            .join('\n') || '';
        let truncated = false
        if (contentText != null && contentText.length > MAX_TOOL_RESULT_CHARS) {
            console.warn(`   ⚠️  Result truncated: ${contentText.length} → ${MAX_TOOL_RESULT_CHARS} chars`);
            truncated = true;
            contentText = truncateToolResult(contentText);
        }
        return {
            type: 'text',
            text: contentText,
            ...(truncated ? {_meta: {truncated: true}} : {})
        };
    }

    getAllowedDirectories(): string[] {
        return this.allowedDirectories;
    }
}

export async function createFilesystemClient(
    allowedDirectories: string[],
): Promise<ToolProvider> {
    const client = new FilesystemMcpClient({allowedDirectories});
    await client.connect();
    return client;
}