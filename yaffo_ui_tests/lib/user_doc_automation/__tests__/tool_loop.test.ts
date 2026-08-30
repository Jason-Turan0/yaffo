import {beforeEach, describe, expect, it, jest} from "@jest/globals";
import type {ModelClient, ModelResponse} from "@lib/model_clients/model_client.interface";
import type {ToolProvider} from "@lib/tool_providers/toolprovider.types";
import {runToolLoop} from "../tool_loop";

type ToolResult = string | {type: "text"; text: string};

const response = (
    text: string,
    toolCalls: Array<{toolCallId: string; toolName: string; input: unknown}> = [],
): ModelResponse => ({text, toolCalls} as unknown as ModelResponse);

const call = (toolCallId: string, toolName: string, input: unknown = {}) => ({
    toolCallId, toolName, input,
});

let addToolResultMessage: jest.Mock<(results: unknown[]) => void>;

const client = (responses: Array<ModelResponse | undefined>, lastError?: string): ModelClient => {
    let index = 0;
    return {
        callModelApi: jest.fn(async () => responses[index++]),
        addToolResultMessage,
        lastError,
    } as unknown as ModelClient;
};

const provider = (
    names: string[],
    implementation: (name: string, input: Record<string, unknown>) => ToolResult,
): ToolProvider => ({
    getTools: jest.fn(() => names.map((name) => ({name}))),
    callTool: jest.fn<(name: string, input: Record<string, unknown>) => Promise<ToolResult>>(
        async (name, input) => implementation(name, input)),
} as unknown as ToolProvider);

beforeEach(() => {
    addToolResultMessage = jest.fn();
});

describe("runToolLoop tool dispatch", () => {
    it("routes every call to its provider, batches results, and continues to the answer", async () => {
        const filesystem = provider(["read_file"], (_name, input) =>
            `contents of ${input.path}`);
        const browser = provider(["browser_snapshot"], () => ({
            type: "text", text: "page snapshot",
        }));
        const model = client([
            response("", [
                call("call-1", "read_file", {path: "guide.md"}),
                call("call-2", "browser_snapshot"),
            ]),
            response('{"files":[]}'),
        ]);

        await expect(runToolLoop(model, [filesystem, browser])).resolves.toBe('{"files":[]}');

        expect(filesystem.callTool).toHaveBeenCalledWith("read_file", {path: "guide.md"});
        expect(browser.callTool).toHaveBeenCalledWith("browser_snapshot", {});
        expect(addToolResultMessage).toHaveBeenCalledWith([
            {
                type: "tool-result",
                toolCallId: "call-1",
                toolName: "read_file",
                output: {type: "text", value: "contents of guide.md"},
            },
            {
                type: "tool-result",
                toolCallId: "call-2",
                toolName: "browser_snapshot",
                output: {type: "text", value: "page snapshot"},
            },
        ]);
    });

    it("returns missing-provider errors to the model and allows it to recover", async () => {
        const model = client([
            response("", [call("missing-1", "unknown_tool")]),
            response("recovered answer"),
        ]);

        await expect(runToolLoop(model, [])).resolves.toBe("recovered answer");

        expect(addToolResultMessage).toHaveBeenCalledWith([
            expect.objectContaining({
                toolCallId: "missing-1",
                output: {type: "text", value: "Error: no provider implements unknown_tool"},
            }),
        ]);
    });

    it("turns thrown Error and non-Error failures into tool results", async () => {
        const failing = provider(["throws_error", "throws_string"], (name) => {
            if (name === "throws_error") throw new Error("permission denied");
            throw "transport closed";
        });
        const model = client([
            response("", [
                call("error-1", "throws_error"),
                call("error-2", "throws_string"),
            ]),
            response("done"),
        ]);

        await expect(runToolLoop(model, [failing])).resolves.toBe("done");

        const results = addToolResultMessage.mock.calls[0][0] as Array<{
            toolCallId: string; output: {value: string};
        }>;
        expect(results.map(({toolCallId, output}) => [toolCallId, output.value])).toEqual([
            ["error-1", "Error: permission denied"],
            ["error-2", "Error: transport closed"],
        ]);
    });

    it("stops after the configured number of tool-only rounds", async () => {
        const looping = provider(["read_file"], () => "again");
        const model = client([
            response("", [call("round-1", "read_file")]),
            response("", [call("round-2", "read_file")]),
        ]);

        await expect(runToolLoop(model, [looping], 2)).rejects.toThrow(
            "gave up after 2 tool rounds without an answer");
        expect(addToolResultMessage).toHaveBeenCalledTimes(2);
    });

    it("surfaces the model client's recorded API error when no response arrives", async () => {
        await expect(runToolLoop(client([undefined], "provider rate limit"), []))
            .rejects.toThrow("provider rate limit");
    });
});
