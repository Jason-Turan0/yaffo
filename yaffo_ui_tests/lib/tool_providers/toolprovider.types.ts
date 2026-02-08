import type {JSONSchema7} from "ai";

export interface RawToolDefinition {
    name: string;
    description: string;
    inputSchema: JSONSchema7;
}

export type ContentBlock = {
    type: "text";
    text: string;
};

export type CallToolReturn = string | ContentBlock;

export interface ToolProvider {
    getTools(): RawToolDefinition[];
    callTool(name: string, args: Record<string, unknown>): Promise<CallToolReturn>;
    disconnect(): Promise<void>;
}