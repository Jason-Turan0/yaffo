import {z} from "zod";
import {GeneratedTestResponse} from "./model_client.response.types";

const GeneratedTestFileSchema = z.object({
    filename: z.string(),
    code: z.string(),
    description: z.string().optional(),
}).strict();

const GeneratedTestResponseSchema = z.object({
    files: z.array(GeneratedTestFileSchema).min(1),
    testContext: z.string().optional(),
    explanation: z.string().optional(),
    confidence: z.number().min(0).max(1).optional(),
}).strict();

export interface ParseResult {
    response: GeneratedTestResponse | null;
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

const extractJsonFromText = (text: string): string | null => {
    const jsonStartRegex = /\{\s*"files":|^\{\s*"/gm;
    const match = jsonStartRegex.exec(text);
    if (!match) {
        return null;
    }

    const firstBrace = match.index;
    let depth = 0;
    let lastBrace = -1;
    let inString = false;
    let escapeNext = false;

    for (let i = firstBrace; i < text.length; i++) {
        const char = text[i];

        if (escapeNext) {
            escapeNext = false;
            continue;
        }

        if (char === "\\") {
            escapeNext = true;
            continue;
        }

        if (char === '"') {
            inString = !inString;
            continue;
        }

        if (!inString) {
            if (char === "{") {
                depth++;
            } else if (char === "}") {
                depth--;
                if (depth === 0) {
                    lastBrace = i;
                    break;
                }
            }
        }
    }

    if (lastBrace === -1) {
        return null;
    }

    console.log(`📋 Extracted JSON from position ${firstBrace} to ${lastBrace} (${lastBrace - firstBrace + 1} chars)`);
    return text.slice(firstBrace, lastBrace + 1);
};

export const parseJsonResponse = (text: string): ParseResult => {
    let jsonText = text.trim();

    jsonText = jsonText
        .replace(/```json\n?/g, "")
        .replace(/```\n?/g, "")
        .trim();

    let parsed: unknown;
    try {
        parsed = JSON.parse(jsonText);
    } catch {
        const extracted = extractJsonFromText(jsonText);
        if (!extracted) {
            console.error("No JSON object found in response");
            return {response: null, schemaErrors: ["No valid JSON found in response"]};
        }
        try {
            parsed = JSON.parse(extracted);
        } catch (e) {
            console.error("Failed to parse extracted JSON:", e);
            return {response: null, schemaErrors: ["Failed to parse JSON: " + String(e)]};
        }
    }

    const result = GeneratedTestResponseSchema.safeParse(parsed);
    if (!result.success) {
        const schemaErrors = formatZodErrors(result.error);
        console.error("Schema validation errors:", schemaErrors);
        return {response: parsed as GeneratedTestResponse, schemaErrors};
    }

    return {response: result.data as GeneratedTestResponse, schemaErrors: []};
};