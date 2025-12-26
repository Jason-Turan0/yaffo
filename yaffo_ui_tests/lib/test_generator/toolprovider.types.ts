import type {Tool} from "@anthropic-ai/sdk/resources/messages.js";
import {Client} from "@modelcontextprotocol/sdk/client/index.js";
type CallToolReturn = ReturnType<Client["callTool"]>;

export interface ToolProvider {
    getToolsForClaude(): Tool[];
    callTool(name: string, args: Record<string, unknown>): Promise<CallToolReturn>;
    disconnect(): Promise<void>;
}