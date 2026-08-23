import type {z} from "zod";
import {toToolResultPart} from "@lib/model_clients/model_client.interface";
import {extractJson} from "@lib/test_generator/prompt/json_parser";
import type {ModelClient, ToolCallResult} from "@lib/model_clients/model_client.interface";
import type {ToolProvider} from "@lib/tool_providers/toolprovider.types";

/** Model turns to allow before giving up, so a confused session cannot loop forever. */
export const MAX_TOOL_ROUNDS = 20;

/**
 * Drive a turn until the model stops calling tools and answers.
 *
 * Every turn needs this once tools are in play, triage included: given a browser and
 * a filesystem the model will reasonably look something up before classifying, and a
 * caller that expects JSON on the first response gets an empty string instead.
 */
export const runToolLoop = async (
    client: ModelClient,
    providers: ToolProvider[],
    maxRounds = MAX_TOOL_ROUNDS
): Promise<string> => {
    for (let round = 0; round < maxRounds; round++) {
        const response = await client.callModelApi();
        if (!response) throw new Error(client.lastError ?? "the model returned no response");
        if (!response.toolCalls.length) return response.text;

        const results: ToolCallResult[] = [];
        for (const call of response.toolCalls) {
            try {
                // Tool names are unique across providers, so the first that advertises
                // the name owns the call.
                const provider = providers.find((candidate) =>
                    candidate.getTools().some((tool) => tool.name === call.toolName));
                if (!provider) throw new Error(`no provider implements ${call.toolName}`);
                const result = await provider.callTool(
                    call.toolName, call.input as Record<string, unknown>);
                results.push({
                    type: "tool_result",
                    toolCallId: call.toolCallId,
                    toolName: call.toolName,
                    result: typeof result === "string" ? result : result.text,
                });
            } catch (e) {
                results.push({
                    type: "tool_result",
                    toolCallId: call.toolCallId,
                    toolName: call.toolName,
                    result: `Error: ${e instanceof Error ? e.message : String(e)}`,
                    isError: true,
                });
            }
        }
        client.addToolResultMessage(results.map(toToolResultPart));
    }
    throw new Error(`gave up after ${maxRounds} tool rounds without an answer`);
};

export interface ParsedAnswer<T> {
    value?: T;
    /** Empty when the answer parsed. */
    errors: string[];
}

/**
 * Parse a model's final answer against a schema.
 *
 * Returns errors rather than throwing, because the two failure modes are the same
 * kind of problem to the caller: `safeParse` guards the shape, but `JSON.parse` on a
 * model response that is prose, truncated, or empty throws — and a throw here would
 * skip the validation gates and leave a half-written tree behind.
 */
export const parseAnswer = <T>(schema: z.ZodType<T>, answer: string): ParsedAnswer<T> => {
    let json: unknown;
    try {
        // Tolerant extraction first: a non-native provider may wrap JSON in prose or
        // a fenced block.
        json = JSON.parse(extractJson(answer));
    } catch (e) {
        const detail = e instanceof Error ? e.message : String(e);
        return {errors: [`response was not JSON (${detail}): ${answer.slice(0, 200)}`]};
    }
    const parsed = schema.safeParse(json);
    return parsed.success
        ? {value: parsed.data, errors: []}
        : {errors: parsed.error.errors.map((e) => `${e.path.join(".") || "(root)"}: ${e.message}`)};
};
