import {describe, expect, it} from "@jest/globals";
import {existsSync, mkdirSync, readdirSync, writeFileSync, rmSync} from "fs";
import {join} from "path";

/**
 * Per-run log directories.
 *
 * `apiCallCount` restarts at zero every run, so a shared directory meant each rerun
 * overwrote the previous run's `0_*.json`, `1_*.json`, and so on. Two readings of
 * "call 9" then referred to different runs through the same filename — which happened
 * mid-investigation and cost real time before anyone noticed the logs had been
 * swapped underneath them.
 */
describe("newRunLogDir", () => {
    const withStagingDir = async <T>(fn: (dir: string) => Promise<T> | T): Promise<T> => {
        const root = join(process.cwd(), ".doc-staging", `__test-${Date.now()}-${Math.random()}`);
        mkdirSync(root, {recursive: true});
        const previous = process.env.DOCS_STAGING_DIR;
        process.env.DOCS_STAGING_DIR = root;
        // paths.ts reads the env var at import time, so load it fresh per case.
        try {
            return await fn(root);
        } finally {
            if (previous === undefined) delete process.env.DOCS_STAGING_DIR;
            else process.env.DOCS_STAGING_DIR = previous;
            rmSync(root, {recursive: true, force: true});
        }
    };

    it("gives consecutive runs different directories", async () => {
        await withStagingDir(async (root) => {
            const {newRunLogDir} = await import(`../user_doc_automation/paths?a=${Math.random()}`);
            const first = newRunLogDir("generate-logs");
            await new Promise((r) => setTimeout(r, 5));
            const second = newRunLogDir("generate-logs");
            expect(first).not.toBe(second);
            expect(existsSync(first)).toBe(true);
            expect(existsSync(second)).toBe(true);
            expect(root).toBeTruthy();
        });
    });

    it("names directories so they sort chronologically", async () => {
        await withStagingDir(async () => {
            const {newRunLogDir} = await import(`../user_doc_automation/paths?b=${Math.random()}`);
            const made: string[] = [];
            for (let i = 0; i < 3; i++) {
                made.push(newRunLogDir("heal-logs"));
                await new Promise((r) => setTimeout(r, 5));
            }
            const names = made.map((d) => d.split("/").pop() as string);
            expect([...names].sort()).toEqual(names);
        });
    });

    it("uses no colons, which are not safe on every filesystem", async () => {
        await withStagingDir(async () => {
            const {newRunLogDir} = await import(`../user_doc_automation/paths?c=${Math.random()}`);
            expect(newRunLogDir("generate-logs").split("/").pop()).not.toContain(":");
        });
    });

    it("prunes old runs so full request logs cannot grow without bound", async () => {
        await withStagingDir(async (root) => {
            const {newRunLogDir} = await import(`../user_doc_automation/paths?d=${Math.random()}`);
            const kindRoot = join(root, "generate-logs");
            mkdirSync(kindRoot, {recursive: true});
            for (let i = 0; i < 40; i++) {
                const dir = join(kindRoot, `2020-01-01T00-00-${String(i).padStart(2, "0")}-000Z`);
                mkdirSync(dir, {recursive: true});
                writeFileSync(join(dir, "0_x_api.json"), "{}");
            }
            newRunLogDir("generate-logs");
            expect(readdirSync(kindRoot).length).toBeLessThanOrEqual(20);
        });
    });

    it("keeps the most recent runs, not arbitrary ones", async () => {
        await withStagingDir(async (root) => {
            const {newRunLogDir} = await import(`../user_doc_automation/paths?e=${Math.random()}`);
            const kindRoot = join(root, "generate-logs");
            mkdirSync(kindRoot, {recursive: true});
            for (let i = 0; i < 30; i++) {
                mkdirSync(join(kindRoot, `2020-01-01T00-00-${String(i).padStart(2, "0")}-000Z`),
                    {recursive: true});
            }
            newRunLogDir("generate-logs");
            const kept = readdirSync(kindRoot).sort();
            expect(kept).toContain("2020-01-01T00-00-29-000Z");
            expect(kept).not.toContain("2020-01-01T00-00-00-000Z");
        });
    });
});
