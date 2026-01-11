import {Client} from "@modelcontextprotocol/sdk/client/index.js";
import {BetaTool} from "@anthropic-ai/sdk/resources/beta";
type CallToolReturn = ReturnType<Client["callTool"]>;

export interface ToolProvider {
    getToolsForClaude(): BetaTool[];
    callTool(name: string, args: Record<string, unknown>): Promise<CallToolReturn>;
    disconnect(): Promise<void>;
}