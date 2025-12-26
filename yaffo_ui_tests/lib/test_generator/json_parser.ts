import {GeneratedTestResponse} from "./model_client.response.types";

export const parseJsonResponse = (text: string): GeneratedTestResponse | null => {
    let jsonText = text.trim();

    // Remove markdown code blocks if present
    jsonText = jsonText
        .replace(/```json\n?/g, "")
        .replace(/```\n?/g, "")
        .trim();

    // Try direct parse first
    try {
        return JSON.parse(jsonText) as GeneratedTestResponse;
    } catch {
        // If direct parse fails, try to extract JSON object from text
        // (handles case where model adds explanation before/after JSON)
    }

    // Find the JSON object by locating `{"` which is more reliable than just `{`
    // This avoids matching things like `{photo_id}` in markdown backticks
    const jsonStartPatterns = ['{"files":', '{"'];
    let firstBrace = -1;

    for (const pattern of jsonStartPatterns) {
        const idx = jsonText.indexOf(pattern);
        if (idx !== -1) {
            firstBrace = idx;
            break;
        }
    }

    if (firstBrace === -1) {
        console.error("No JSON object found in response");
        return null;
    }

    // Find the matching closing brace by counting brace depth
    let depth = 0;
    let lastBrace = -1;
    let inString = false;
    let escapeNext = false;

    for (let i = firstBrace; i < jsonText.length; i++) {
        const char = jsonText[i];

        if (escapeNext) {
            escapeNext = false;
            continue;
        }

        if (char === '\\') {
            escapeNext = true;
            continue;
        }

        if (char === '"') {
            inString = !inString;
            continue;
        }

        if (!inString) {
            if (char === '{') {
                depth++;
            } else if (char === '}') {
                depth--;
                if (depth === 0) {
                    lastBrace = i;
                    break;
                }
            }
        }
    }

    if (lastBrace === -1) {
        console.error("No matching closing brace found in JSON");
        return null;
    }

    const extractedJson = jsonText.slice(firstBrace, lastBrace + 1);
    console.log(`📋 Extracted JSON from position ${firstBrace} to ${lastBrace} (${extractedJson.length} chars)`);

    try {
        return JSON.parse(extractedJson) as GeneratedTestResponse;
    } catch (e) {
        console.error("Failed to parse extracted JSON:", e);
        console.error("Extracted text (first 500 chars):", extractedJson.slice(0, 500));
        return null;
    }
};