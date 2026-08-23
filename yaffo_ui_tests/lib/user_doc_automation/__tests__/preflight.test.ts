import {describe, expect, it, jest} from "@jest/globals";
import type {ToolProvider} from "@lib/tool_providers/toolprovider.types";
import {verifyBrowserTool} from "../preflight";

type ToolResult = string | {type: "text"; text: string};

const provider = (
    tools: string[],
    implementation: (name: string, args: Record<string, unknown>) => ToolResult,
): ToolProvider => ({
    getTools: () => tools.map((name) => ({name})),
    callTool: jest.fn<(name: string, args: Record<string, unknown>) => Promise<ToolResult>>(
        async (name, args) => implementation(name, args)),
} as unknown as ToolProvider);

describe("verifyBrowserTool result handling", () => {
    it("accepts a successful MCP content block and calls navigate with the exact URL", async () => {
        const browser = provider(["browser_navigate"], () => ({
            type: "text", text: "### Page\n- Page Title: Yaffo",
        }));

        await expect(verifyBrowserTool([browser], "http://sandbox.test:5002"))
            .resolves.toBeUndefined();

        expect(browser.callTool).toHaveBeenCalledWith(
            "browser_navigate", {url: "http://sandbox.test:5002"});
    });

    it("selects the provider that advertises browser_navigate", async () => {
        const filesystem = provider(["read_file"], () => "not used");
        const browser = provider(["browser_snapshot", "browser_navigate"], () => "### Page");

        await verifyBrowserTool([filesystem, browser], "http://sandbox.test");

        expect(filesystem.callTool).not.toHaveBeenCalled();
        expect(browser.callTool).toHaveBeenCalledTimes(1);
    });

    it("reports non-Error rejections without losing their detail", async () => {
        const browser = provider(["browser_navigate"], () => {
            throw "connection closed";
        });

        await expect(verifyBrowserTool([browser], "http://sandbox.test"))
            .rejects.toThrow(/could not reach http:\/\/sandbox\.test: connection closed/);
    });

    it("uses only the first line of a tool-level error in its diagnostic", async () => {
        const browser = provider(["browser_navigate"], () => ({
            type: "text",
            text: "### Error\nBrowser executable is missing\nA long internal stack trace",
        }));

        const failure = await verifyBrowserTool([browser], "http://sandbox.test")
            .then(() => "", (caught: Error) => caught.message);

        expect(failure).toContain("Browser executable is missing");
        expect(failure).not.toContain("A long internal stack trace");
    });
});
