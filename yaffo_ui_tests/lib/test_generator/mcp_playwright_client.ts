/**
 * MCP Client - Manages connection to Playwright MCP server
 *
 * Provides Claude with tools to automate browser interactions
 * for running and validating generated tests.
 */

import {Client} from "@modelcontextprotocol/sdk/client/index.js";
import {StdioClientTransport} from "@modelcontextprotocol/sdk/client/stdio.js";
import type {Tool} from "@anthropic-ai/sdk/resources/messages.js";
import {CallToolReturn, ToolProvider} from "@lib/test_generator/toolprovider.types";
import {writeFileSync} from "fs";

export interface McpClientLike {
    close(): Promise<void>;
    listTools(): Promise<{ tools: Array<{ name: string; description?: string; inputSchema: unknown }> }>;
    callTool(params: { name: string; arguments?: Record<string, unknown> }): Promise<Record<string, unknown>>;
}

export interface ArtifactOptions {
    /** Directory to save artifacts (videos, traces, session). Defaults to current directory */
    outputDir?: string;
    /** Save video of the session. Specify dimensions like "800x600" or true for default */
    saveVideo?: string | boolean;
    /** Save Playwright trace of the session */
    saveTrace?: boolean;
    /** Save Playwright MCP session data */
    saveSession?: boolean;
}

export interface PlaywrightMcpClientOptions {
    headless?: boolean;
    browser?: "chromium" | "firefox" | "webkit";
    baseUrl?: string;
    /** Artifact saving configuration */
    artifacts?: ArtifactOptions;
}

export interface McpTool {
    name: string;
    description: string;
    inputSchema: Record<string, unknown>;
}

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
    private tools: McpTool[] = [];
    private connected = false;

    constructor(private readonly client: McpClientLike) {}

    async connect(): Promise<void> {
        const toolsResult = await this.client.listTools();
        this.tools = toolsResult.tools.map((t) => ({
            name: t.name,
            description: t.description || "",
            inputSchema: t.inputSchema as Record<string, unknown>,
        }));
        this.connected = true;

        console.log(`🎭 Playwright MCP connected. Available tools: ${this.tools.length}`);
    }

    async disconnect(): Promise<void> {
        if (this.connected) {
            await this.client.close();
            this.connected = false;
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
        if (!this.connected) {
            throw new Error("Playwright MCP client not connected");
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

    async takeScreenshot(options?: { fullPage?: boolean }): Promise<CallToolReturn> {
        return this.callTool("browser_take_screenshot", options || {});
    }

    async close(): Promise<CallToolReturn> {
        return this.callTool("browser_close", {});
    }
}

function buildArgs(options: PlaywrightMcpClientOptions): string[] {
    const args = ["@playwright/mcp@latest"];

    if (options.headless !== false) {
        args.push("--headless");
    }

    if (options.browser) {
        args.push(`--browser=${options.browser}`);
    }

    const artifacts = options.artifacts;
    if (artifacts) {
        if (artifacts.outputDir) {
            args.push(`--output-dir=${artifacts.outputDir}`);
        }
        if (artifacts.saveVideo) {
            const videoSize = typeof artifacts.saveVideo === "string"
                ? artifacts.saveVideo
                : "1280x720";
            args.push(`--save-video=${videoSize}`);
        }
        if (artifacts.saveTrace) {
            args.push("--save-trace");
        }
        if (artifacts.saveSession) {
            args.push("--save-session");
        }
    }

    return args;
}

export async function createPlaywrightClient(
    options: PlaywrightMcpClientOptions = {}
): Promise<ToolProvider> {
    const args = buildArgs(options);

    const transport = new StdioClientTransport({
        command: "npx",
        args,
    });

    const mcpClient = new Client(
        {
            name: "yaffo-playwright-runner",
            version: "1.0.0",
        },
        {
            capabilities: {},
        }
    );

    await mcpClient.connect(transport);

    const client = new PlaywrightMcpClient(mcpClient);
    await client.connect();
    return client;
}

export async function createStubPlaywrightClient(): Promise<ToolProvider> {
    return {
        getToolsForClaude() {
            return [];
        },

        callTool(_name: string, _args: Record<string, unknown>): Promise<CallToolReturn> {
            return Promise.reject(new Error("Playwright MCP client not connected"));
        },

        async disconnect(): Promise<void> {}
    };
}