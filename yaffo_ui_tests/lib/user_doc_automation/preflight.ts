import type {ToolProvider} from "@lib/tool_providers/toolprovider.types";

/**
 * Confirm the browser tool actually works before handing it to an agent.
 *
 * The Playwright MCP server connects and advertises its 24 tools whether or not it can
 * launch a browser; the failure only appears in the *result* of the first navigate:
 *
 *   Error: Browser "chrome-for-testing" is not installed; expected executable at
 *   .../ms-playwright/chromium-1237/... Run `npx @playwright/mcp install-browser`
 *
 * A model handed that does not stop — it improvises. One generate run spent roughly
 * forty rounds reading route modules, templates, lock files, fixture-seeding code, and
 * eventually its own API logs, trying to derive by inspection a media id it could have
 * read off the screen in one call. It then ran out of output budget mid-thought.
 *
 * A tool that is present but broken is worse than one that is absent, because the agent
 * cannot tell the difference between "this is broken" and "I am holding it wrong". So
 * check it here, once, and fail with the command that fixes it.
 */
export const verifyBrowserTool = async (
    providers: ToolProvider[],
    baseUrl: string
): Promise<void> => {
    const provider = providers.find((candidate) =>
        candidate.getTools().some((tool) => tool.name === "browser_navigate"));
    if (!provider) return;   // No browser offered at all: nothing to verify.

    let text: string;
    try {
        const result = await provider.callTool("browser_navigate", {url: baseUrl});
        text = typeof result === "string" ? result : result.text;
    } catch (e) {
        throw new Error(`the browser tool could not reach ${baseUrl}: ` +
            (e instanceof Error ? e.message : String(e)));
    }

    // The server reports tool-level failures in the result body, not as a throw.
    if (/^###\s*Error/m.test(text)) {
        const detail = text.replace(/^###\s*Error\s*/m, "").trim().split("\n")[0];
        throw new Error(
            `the browser tool is connected but cannot open a page:\n   ${detail}\n` +
            `   Agents given a broken browser improvise instead of stopping — fix this ` +
            `before running.`);
    }
};
