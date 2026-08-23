import {describe, expect, it} from "@jest/globals";
import {verifyBrowserTool} from "../user_doc_automation/preflight";
import type {ToolProvider} from "@lib/tool_providers/toolprovider.types";

const provider = (result: string | Error): ToolProvider => ({
    getTools: () => [{name: "browser_navigate"}],
    callTool: async () => {
        if (result instanceof Error) throw result;
        return result;
    },
} as unknown as ToolProvider);

/** The exact body the MCP server returned when chrome-for-testing was missing. */
const REAL_FAILURE = `### Error
Error: Browser "chrome-for-testing" is not installed; expected executable at /Users/j/Library/Caches/ms-playwright/chromium-1237/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing. Run \`npx @playwright/mcp install-browser chrome-for-testing\``;

const OK = `### Ran Playwright code
\`\`\`js
await page.goto('http://127.0.0.1:5002/');
\`\`\`
### Page
- Page Title: Home - Yaffo`;

describe("verifyBrowserTool", () => {
    // The server connects and advertises 24 tools either way; the failure is only in
    // the result body. A model handed this does not stop, it improvises — one run spent
    // ~40 rounds deriving a media id from source instead.
    it("catches a connected-but-broken browser", async () => {
        await expect(verifyBrowserTool([provider(REAL_FAILURE)], "http://x"))
            .rejects.toThrow(/cannot open a page/);
    });

    it("surfaces the install command from the server's message", async () => {
        await expect(verifyBrowserTool([provider(REAL_FAILURE)], "http://x"))
            .rejects.toThrow(/install-browser chrome-for-testing/);
    });

    it("passes a working browser", async () => {
        await expect(verifyBrowserTool([provider(OK)], "http://x")).resolves.toBeUndefined();
    });

    it("reports an unreachable app rather than swallowing it", async () => {
        await expect(verifyBrowserTool([provider(new Error("ECONNREFUSED"))], "http://x"))
            .rejects.toThrow(/could not reach http:\/\/x.*ECONNREFUSED/);
    });

    it("is a no-op when no browser tool is offered at all", async () => {
        const noBrowser = {getTools: () => [{name: "read_file"}]} as unknown as ToolProvider;
        await expect(verifyBrowserTool([noBrowser], "http://x")).resolves.toBeUndefined();
    });

    // "### Error" appearing inside a page snapshot must not trip the check.
    it("only treats a leading ### Error as failure", async () => {
        await expect(verifyBrowserTool([provider(`${OK}\n- text: "### Error handling"`)], "http://x"))
            .resolves.toBeUndefined();
    });
});
