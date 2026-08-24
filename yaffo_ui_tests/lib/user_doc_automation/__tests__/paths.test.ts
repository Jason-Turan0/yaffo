import {afterEach, describe, expect, it} from "@jest/globals";
import {realpathSync} from "fs";
import {tmpdir} from "os";
import {join, resolve} from "path";

const ENV_KEYS = [
    "GUIDE_DIR", "DOCS_STAGING_DIR", "DOCS_CAPTURE_DIR", "DOCS_BASE_URL",
    "YAFFO_DOCS_DATA_DIR",
] as const;
const originalEnv = new Map(ENV_KEYS.map((key) => [key, process.env[key]]));

const freshPaths = async (tag: string) =>
    import(`../paths?paths-test=${tag}-${Math.random()}`);

afterEach(() => {
    for (const key of ENV_KEYS) {
        const original = originalEnv.get(key);
        if (original === undefined) delete process.env[key];
        else process.env[key] = original;
    }
});

describe("documentation automation paths", () => {
    it("derives repository, content, capture, and browser defaults from the working directory", async () => {
        for (const key of ENV_KEYS) delete process.env[key];

        const paths = await freshPaths("defaults");

        expect(paths.REPO).toBe(resolve(process.cwd(), ".."));
        expect(paths.CONTENT_DIR).toBe(resolve(process.cwd(), "user_doc_automation"));
        expect(paths.GUIDE_DIR).toBe(resolve(process.cwd(), "..", "docs", "guide"));
        expect(paths.STAGING_DIR).toBe(resolve(process.cwd(), ".doc-staging"));
        expect(paths.CAPTURE_DIR).toBe(join(paths.STAGING_DIR, "captures"));
        expect(paths.BASE_URL).toBe("http://127.0.0.1:5002");
        const tempRoot = realpathSync(process.platform === "win32" ? tmpdir() : "/tmp");
        expect(paths.DOCS_DATA_DIR).toBe(join(tempRoot, "yaffo-docs"));
    });

    it("honors every independent environment override without appending captures twice", async () => {
        process.env.GUIDE_DIR = "/tmp/custom-guide";
        process.env.DOCS_STAGING_DIR = "/tmp/custom-staging";
        process.env.DOCS_CAPTURE_DIR = "/tmp/container-captures";
        process.env.DOCS_BASE_URL = "http://sandbox.test:7000";
        process.env.YAFFO_DOCS_DATA_DIR = "/canonical/docs-fixture";

        const paths = await freshPaths("overrides");

        expect(paths.GUIDE_DIR).toBe("/tmp/custom-guide");
        expect(paths.STAGING_DIR).toBe("/tmp/custom-staging");
        expect(paths.CAPTURE_DIR).toBe("/tmp/container-captures");
        expect(paths.BASE_URL).toBe("http://sandbox.test:7000");
        expect(paths.DOCS_DATA_DIR).toBe("/canonical/docs-fixture");
    });

    it("derives captures from an overridden staging directory when capture has no override", async () => {
        process.env.DOCS_STAGING_DIR = "/tmp/custom-staging";
        delete process.env.DOCS_CAPTURE_DIR;

        const {CAPTURE_DIR} = await freshPaths("derived-capture");

        expect(CAPTURE_DIR).toBe("/tmp/custom-staging/captures");
    });

    it("canonicalizes an explicit docs fixture path through an existing temp parent", async () => {
        process.env.YAFFO_DOCS_DATA_DIR = "/tmp/custom-docs-fixture";

        const {DOCS_DATA_DIR} = await freshPaths("canonical-docs-root");

        expect(DOCS_DATA_DIR).toBe(join(realpathSync("/tmp"), "custom-docs-fixture"));
    });

    it("splits page identifiers and resolves their content directory", async () => {
        const {CONTENT_DIR, pageDir, splitPage} = await freshPaths("page");

        expect(splitPage("library/browsing-filtering")).toEqual([
            "library", "browsing-filtering",
        ]);
        expect(pageDir("library/browsing-filtering")).toBe(
            join(CONTENT_DIR, "library", "browsing-filtering"));
    });
});
