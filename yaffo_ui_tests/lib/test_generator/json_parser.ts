import {z} from "zod";
import {GeneratedTestResponse} from "../model_clients/model_client.response.types";
import * as util from "node:util";

const GeneratedTestFileSchema = z.object({
    filename: z.string().describe("The filename of the generated playwright test. Only include the file name - the system will organize the folder structure."),
    code: z.string().describe("The content of the complete, valid TypeScript test file"),
    description: z.string().optional().describe("A description of what the test file covers"),
}).strict();

export const GeneratedTestResponseSchema = z.object({
    files: z.array(GeneratedTestFileSchema).min(1).describe("The list of generated test files"),
    testContext: z.string().optional().describe("Context that would be helpful for troubleshooting failed tests"),
    explanation: z.string().optional().describe("Any additional explanation regarding the process of writing the testing code"),
    confidence: z.number().optional().describe("Confidence rating of the generated test, from 0 (low) to 1 (high)"),
}).strict();

export interface ParseResult<T> {
    response: T | null;
    schemaErrors: string[];
}

const formatZodErrors = (error: z.ZodError): string[] => {
    return error.errors.map(err => {
        const path = err.path.join(".");
        if (err.code === "unrecognized_keys") {
            const keys = (err as z.ZodUnrecognizedKeysIssue).keys;
            return `${path ? path + ": " : ""}Unexpected field(s) [${keys.join(", ")}] - these may belong at a different level in the schema`;
        }
        return `${path ? path + ": " : ""}${err.message}`;
    });
};

const extractJson = (text: string): string => {
    const trimmed = text.trim();
    try {
        JSON.parse(trimmed);
        return trimmed;
    } catch {
        // not pure JSON — try to extract from ```json ``` fenced blocks
    }

    const match = trimmed.match(/```json\s*([\s\S]*?)```/);
    if (match) {
        return match[1].trim();
    }

    return trimmed;
};

export const parseJsonResponse = <T = GeneratedTestResponse>(jsonText: string): ParseResult<T> => {
    const extracted = extractJson(jsonText);
    let parsed: unknown;
    try {
        parsed = JSON.parse(extracted);
    } catch (e) {
        return {response: null, schemaErrors: ["No valid JSON found in response", util.inspect(e)]};
    }

    const result = GeneratedTestResponseSchema.safeParse(parsed);
    if (!result.success) {
        const schemaErrors = formatZodErrors(result.error);
        console.error("Schema validation errors:", schemaErrors);
        return {response: null, schemaErrors};
    }

    return {response: result.data as T, schemaErrors: []};
};