/**
 * MCP Client - Manages connection to Playwright MCP server
 *
 * Provides Claude with tools to automate browser interactions
 * for running and validating generated tests.
 */

import {Client} from "@modelcontextprotocol/sdk/client/index.js";
import {StdioClientTransport} from "@modelcontextprotocol/sdk/client/stdio.js";
import type {Tool} from "@anthropic-ai/sdk/resources/messages.js";
import {ToolProvider} from "@lib/test_generator/toolprovider.types";

export interface PlaywrightMcpClientOptions {
    headless?: boolean;
    browser?: "chromium" | "firefox" | "webkit";
    baseUrl?: string;
}

export interface McpTool {
    name: string;
    description: string;
    inputSchema: Record<string, unknown>;
}

type CallToolReturn = ReturnType<Client["callTool"]>;

const MAX_TOOL_RESULT_CHARS = 30000;

const EXCLUDED_TOOLS = [
    "browser_install",
    "browser_pdf_save",
];

export const truncateToolResult = (result: string): string => {
    if (result.length <= MAX_TOOL_RESULT_CHARS) {
        return result;
    }
    const truncated = result.slice(0, MAX_TOOL_RESULT_CHARS);
    const truncatedMsg = `\n\n[TRUNCATED: Result was ${result.length} chars, showing first ${MAX_TOOL_RESULT_CHARS}]`;
    return truncated + truncatedMsg;
};


export class PlaywrightMcpClient implements ToolProvider {
    private client: Client | null = null;
    private transport: StdioClientTransport | null = null;
    private tools: McpTool[] = [];
    private readonly options: PlaywrightMcpClientOptions;

    constructor(options: PlaywrightMcpClientOptions = {}) {
        this.options = {
            headless: true,
            browser: "chromium",
            ...options,
        };
    }

    async connect(): Promise<void> {
        const args = ["@playwright/mcp@latest"];

        if (this.options.headless) {
            args.push("--headless");
        }

        if (this.options.browser) {
            args.push(`--browser=${this.options.browser}`);
        }

        this.transport = new StdioClientTransport({
            command: "npx",
            args,
        });

        this.client = new Client(
            {
                name: "yaffo-playwright-runner",
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

        console.log(`🎭 Playwright MCP connected. Available tools: ${this.tools.length}`);
    }

    async disconnect(): Promise<void> {
        if (this.client) {
            await this.client.close();
            this.client = null;
            this.transport = null;
            console.log("🎭 Playwright MCP disconnected");
        }
    }

    getToolsForClaude(): Tool[] {
        return this.tools
            .filter((tool) => !EXCLUDED_TOOLS.includes(tool.name))
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
            throw new Error("Playwright MCP client not connected");
        }

        const result = await this.client.callTool({name, arguments: args});
        const contentText: string | undefined = (result?.content as any)?.text;

        if (contentText != null && contentText.length > MAX_TOOL_RESULT_CHARS) {
            console.warn(`   ⚠️  Result truncated: ${contentText.length} → ${MAX_TOOL_RESULT_CHARS} chars`);
            const content = result?.content as any;
            if (content == null) {
                throw new Error("No content");
            }
            content._meta = {
                ...(content?._meta || {}),
                truncated: true,
            };
            content.text = truncateToolResult(contentText);
        }
        return result;
    }

    async navigate(url: string): Promise<CallToolReturn> {
        return this.callTool("browser_navigate", {url});
    }

    async snapshot(): Promise<CallToolReturn> {
        return this.callTool("browser_snapshot", {});
    }

    async click(element: string): Promise<CallToolReturn> {
        return this.callTool("browser_click", {element});
    }

    async type(element: string, text: string): Promise<CallToolReturn> {
        return this.callTool("browser_type", {element, text});
    }

    async takeScreenshot(options?: {fullPage?: boolean}): Promise<CallToolReturn> {
        return this.callTool("browser_take_screenshot", options || {});
    }

    async close(): Promise<CallToolReturn> {
        return this.callTool("browser_close", {});
    }

    getOptions(): PlaywrightMcpClientOptions {
        return {...this.options};
    }
}

export async function createPlaywrightClient(
    options: PlaywrightMcpClientOptions = {}
): Promise<ToolProvider> {
    const client = new PlaywrightMcpClient(options);
    await client.connect();
    return client;
}

export async function createStubPlaywrightClient(options: PlaywrightMcpClientOptions = {}):Promise<ToolProvider> {
    return {
        getToolsForClaude(): [] {
            return []
        },

        callTool(name: string, args: Record<string, unknown>): Promise<CallToolReturn> {
            throw new Error("Playwright MCP client not connected");
        },

        async disconnect(): Promise<void> {

        }
    }
}