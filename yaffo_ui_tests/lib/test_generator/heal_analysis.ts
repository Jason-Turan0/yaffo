import {z} from "zod";
import {extractJson} from "@lib/test_generator/prompt/json_parser";
import {ParseResult} from "@lib/test_generator/prompt/json_parser";
import {HealAnalysisResponse} from "@lib/model_clients/model_client.response.types";

export const FailureClassificationEnum = z.enum([
    "test_code_defect",
    "application_regression",
    "environment_instability",
]);

export const HealAnalysisResponseSchema = z.object({
    classification: FailureClassificationEnum,
    reasoning: z.string(),
    affectedTests: z.array(z.string()),
    suggestedAction: z.string(),
});

export const parseHealAnalysisResponse = (jsonText: string): ParseResult<HealAnalysisResponse> => {
    const extracted = extractJson(jsonText);
    let parsed: unknown;
    try {
        parsed = JSON.parse(extracted);
    } catch {
        return {response: null, schemaErrors: ["No valid JSON found in heal analysis response"]};
    }

    const result = HealAnalysisResponseSchema.safeParse(parsed);
    if (!result.success) {
        const schemaErrors = result.error.errors.map(err => {
            const path = err.path.join(".");
            return `${path ? path + ": " : ""}${err.message}`;
        });
        return {response: null, schemaErrors};
    }

    return {response: result.data, schemaErrors: []};
};
