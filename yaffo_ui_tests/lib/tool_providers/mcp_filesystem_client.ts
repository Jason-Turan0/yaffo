/**
 * MCP Client - Manages connection to filesystem MCP server
 *
 * Provides Claude with tools to read source code, templates, and routes
 * for generating accurate Playwright tests.
 */

import {Client} from "@modelcontextprotocol/sdk/client/index.js";
import {StdioClientTransport} from "@modelcontextprotocol/sdk/client/stdio.js";
import type {JSONSchema7} from "ai";
import {CallToolReturn, RawToolDefinition, ToolProvider} from "@lib/tool_providers/toolprovider.types";
import {truncateToolResultIfNeeded} from "@lib/tool_providers/utils";

export interface McpClientOptions {
    allowedDirectories: string[];
    useDocker?: boolean;
    dockerImage?: string;
    readonly?: boolean;
}

export interface McpTool {
    name: string;
    description: string;
    inputSchema: Record<string, unknown>;
}

const DEFAULT_DOCKER_IMAGE = "yaffo-mcp-filesystem:latest";

const WRITE_TOOLS = [
    "write_file",
    "edit_file",
    "create_directory",
    "move_file",
    "delete_file",
];

export interface DockerTransportConfig {
    command: string;
    args: string[];
}

export function buildDockerTransportConfig(
    allowedDirectories: string[],
    dockerImage: string = DEFAULT_DOCKER_IMAGE,
    readonlyMode: boolean = true,
): DockerTransportConfig {
    const volumeArgs: string[] = [];
    const containerDirs: string[] = [];
    const mountSuffix = readonlyMode ? ":ro" : "";

    for (let i = 0; i < allowedDirectories.length; i++) {
        const hostDir = allowedDirectories[i];
        const containerDir = `/data/${i}`;
        volumeArgs.push("-v", `${hostDir}:${containerDir}${mountSuffix}`);
        containerDirs.push(containerDir);
    }

    return {
        command: "docker",
        args: [
            "run", "--rm", "-i",
            "--network", "none",
            ...volumeArgs,
            dockerImage,
            ...containerDirs,
        ],
    };
}

function buildPathMaps(allowedDirectories: string[]): {
    hostToContainerMap: Map<string, string>;
    containerToHostMap: Map<string, string>;
} {
    const hostToContainerMap = new Map<string, string>();
    const containerToHostMap = new Map<string, string>();

    for (let i = 0; i < allowedDirectories.length; i++) {
        const hostDir = allowedDirectories[i];
        const containerDir = `/data/${i}`;
        hostToContainerMap.set(hostDir, containerDir);
        containerToHostMap.set(containerDir, hostDir);
    }

    return {hostToContainerMap, containerToHostMap};
}

export function translateHostToContainer(
    filePath: string,
    hostToContainerMap: Map<string, string>,
): string {
    const sortedEntries = [...hostToContainerMap.entries()]
        .sort((a, b) => b[0].length - a[0].length);

    for (const [hostDir, containerDir] of sortedEntries) {
        if (filePath === hostDir || filePath.startsWith(hostDir + "/")) {
            return containerDir + filePath.slice(hostDir.length);
        }
    }
    return filePath;
}

export function translateContainerToHost(
    filePath: string,
    containerToHostMap: Map<string, string>,
): string {
    const sortedEntries = [...containerToHostMap.entries()]
        .sort((a, b) => b[0].length - a[0].length);

    for (const [containerDir, hostDir] of sortedEntries) {
        if (filePath === containerDir || filePath.startsWith(containerDir + "/")) {
            return hostDir + filePath.slice(containerDir.length);
        }
    }
    return filePath;
}

export function translateToolArgs(
    args: Record<string, unknown>,
    hostToContainerMap: Map<string, string>,
): Record<string, unknown> {
    const translated = {...args};

    if (typeof translated.path === "string") {
        translated.path = translateHostToContainer(translated.path, hostToContainerMap);
    }

    if (Array.isArray(translated.paths)) {
        translated.paths = translated.paths.map((p: unknown) =>
            typeof p === "string" ? translateHostToContainer(p, hostToContainerMap) : p
        );
    }

    return translated;
}

export function translateToolResult(
    text: string,
    containerToHostMap: Map<string, string>,
): string {
    const sortedEntries = [...containerToHostMap.entries()]
        .sort((a, b) => b[0].length - a[0].length);

    let result = text;
    for (const [containerDir, hostDir] of sortedEntries) {
        while (result.includes(containerDir)) {
            result = result.replace(containerDir, hostDir);
        }
    }
    return result;
}


export class FilesystemMcpClient {
    private client: Client | null = null;
    private transport: StdioClientTransport | null = null;
    private tools: McpTool[] = [];
    private readonly allowedDirectories: string[];
    private readonly useDocker: boolean;
    private readonly dockerImage: string;
    private readonly readonlyMode: boolean;
    private readonly hostToContainerMap: Map<string, string>;
    private readonly containerToHostMap: Map<string, string>;

    constructor(options: McpClientOptions) {
        this.allowedDirectories = options.allowedDirectories;
        this.useDocker = options.useDocker ?? false;
        this.dockerImage = options.dockerImage ?? DEFAULT_DOCKER_IMAGE;
        this.readonlyMode = options.readonly ?? true;

        if (this.useDocker) {
            const maps = buildPathMaps(this.allowedDirectories);
            this.hostToContainerMap = maps.hostToContainerMap;
            this.containerToHostMap = maps.containerToHostMap;
        } else {
            this.hostToContainerMap = new Map();
            this.containerToHostMap = new Map();
        }
    }

    async connect(): Promise<void> {
        let transportConfig: { command: string; args: string[] };

        if (this.useDocker) {
            transportConfig = buildDockerTransportConfig(
                this.allowedDirectories,
                this.dockerImage,
                this.readonlyMode,
            );
        } else {
            transportConfig = {
                command: "npx",
                args: [
                    "-y",
                    "@modelcontextprotocol/server-filesystem",
                    ...this.allowedDirectories,
                ],
            };
        }

        this.transport = new StdioClientTransport(transportConfig);

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

        console.log(`🔌 MCP connected${this.useDocker ? " (Docker)" : ""}. Available tools: ${this.tools.map((t) => t.name).join(", ")}`);
    }

    async disconnect(): Promise<void> {
        if (this.client) {
            await this.client.close();
            this.client = null;
            this.transport = null;
            console.log("🔌 MCP disconnected");
        }
    }

    getTools(): RawToolDefinition[] {
        return this.tools
            .filter((tool) => !WRITE_TOOLS.includes(tool.name))
            .map((tool) => ({
                name: tool.name,
                description: tool.description,
                inputSchema: tool.inputSchema as JSONSchema7,
            }));
    }

    getToolNames(): string[] {
        return this.tools.map((t) => t.name);
    }

    async callTool(name: string, args: Record<string, unknown>): Promise<CallToolReturn> {
        if (!this.client) {
            throw new Error("MCP client not connected");
        }

        if (this.readonlyMode && WRITE_TOOLS.includes(name)) {
            return {
                type: 'text',
                text: `Error: Tool "${name}" is not allowed in readonly mode`,
            };
        }

        const translatedArgs = this.useDocker
            ? translateToolArgs(args, this.hostToContainerMap)
            : args;

        const result = await this.client.callTool({name, arguments: translatedArgs});
        const contentArray = result?.content as Array<{ type: string; text?: string }> | undefined;
        let contentText: string = contentArray
            ?.filter(block => block.type === 'text')
            .map(block => block.text || '')
            .join('\n') || '';

        if (this.useDocker) {
            contentText = translateToolResult(contentText, this.containerToHostMap);
        }

        return {
            type: 'text',
            text: truncateToolResultIfNeeded(contentText),
        };
    }

    getAllowedDirectories(): string[] {
        return this.allowedDirectories;
    }
}

export async function createFilesystemClient(
    allowedDirectories: string[],
    options?: { useDocker?: boolean; dockerImage?: string; readonly?: boolean },
): Promise<ToolProvider> {
    const client = new FilesystemMcpClient({
        allowedDirectories,
        useDocker: options?.useDocker,
        dockerImage: options?.dockerImage,
        readonly: options?.readonly,
    });
    await client.connect();
    return client;
}
