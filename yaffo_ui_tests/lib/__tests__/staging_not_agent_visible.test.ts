import {describe, expect, it} from "@jest/globals";
import {relative, sep} from "path";
import {CONTENT_DIR, GUIDE_DIR, STAGING_DIR} from "../user_doc_automation/paths";
import {YAFFO_APP_ROOT} from "@lib/types";

const isInside = (parent: string, child: string): boolean => {
    const rel = relative(parent, child);
    return rel !== "" && !rel.startsWith("..") && !rel.startsWith(sep);
};

/**
 * The generate agent's filesystem tool is granted exactly these three trees. A run's
 * own API logs — full prompts, reasoning, and responses — are written into staging, so
 * staging being inside any of them lets the agent read back its own transcript. It was
 * observed doing so: "the generate-logs filenames are 0_deepseek_api.json … Let me read
 * one to understand what they contain."
 */
describe("staging is out of the agent's reach", () => {
    it.each([
        ["the content tree", () => CONTENT_DIR],
        ["the guide", () => GUIDE_DIR],
        ["the app source", () => YAFFO_APP_ROOT],
    ])("is not inside %s", (_label, granted) => {
        expect(isInside(granted(), STAGING_DIR)).toBe(false);
    });

    it("is still under yaffo_ui_tests, so a run cleans up after itself", () => {
        expect(isInside(process.cwd(), STAGING_DIR)).toBe(true);
    });

    // Sanity-check the helper rather than trusting a bare `false` above.
    it("detects containment when it is real", () => {
        expect(isInside(CONTENT_DIR, `${CONTENT_DIR}${sep}start-here`)).toBe(true);
        expect(isInside(CONTENT_DIR, CONTENT_DIR)).toBe(false);
    });
});
