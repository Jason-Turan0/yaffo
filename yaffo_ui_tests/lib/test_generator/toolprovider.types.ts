import {BetaTool} from "@anthropic-ai/sdk/resources/beta";

export type ContentBlock = {
    type: "text",
    text: string;
};

export type CallToolReturn = string | ContentBlock;

export interface ToolProvider {
    getToolsForClaude(): BetaTool[];
    callTool(name: string, args: Record<string, unknown>): Promise<CallToolReturn>;
    disconnect(): Promise<void>;
}