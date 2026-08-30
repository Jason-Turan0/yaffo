import {afterEach, describe, expect, it, jest} from "@jest/globals";
import {mkdtempSync, mkdirSync, rmSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";

import {IsolatedEnvironment, TestRunResult} from "@lib/services/isolated_runner";
import {
    DEFAULT_SANDBOX_CONCURRENCY,
    discoverSandboxedSuites,
    parsePositiveInteger,
    runSandboxedSuites,
    runWorkerPool,
} from "@lib/services/run_sandboxed_suites";

const tempDirs: string[] = [];

const makeTestTree = (): string => {
    const root = mkdtempSync(join(tmpdir(), "sandboxed-suites-test-"));
    tempDirs.push(root);
    for (const file of [
        "generated_tests/albums/albums.spec.ts",
        "generated_tests/photo_details/a.spec.ts",
        "generated_tests/photo_details/b.spec.ts",
        "generated_tests/sharing/sharing.spec.ts",
    ]) {
        const path = join(root, file);
        mkdirSync(join(path, ".."), {recursive: true});
        writeFileSync(path, "test('placeholder', () => {});\n");
    }
    return root;
};

const passedResult = (): TestRunResult => ({
    success: true,
    exitCode: 0,
    output: "",
    summary: {total: 1, passed: 1, failed: 0, skipped: 0},
    tests: [],
});

const fakeEnvironment = (port: number, cleanup: () => Promise<void>): IsolatedEnvironment => ({
    tempDir: `/tmp/suite-${port}`,
    port,
    baseUrl: `http://127.0.0.1:${port}`,
    flaskProcess: null,
    taskqProcess: null,
    cleanup,
});

afterEach(() => {
    for (const dir of tempDirs.splice(0)) rmSync(dir, {recursive: true, force: true});
});

describe("discoverSandboxedSuites", () => {
    it("groups spec files by directory and marks the sharing directory for a peer", () => {
        const root = makeTestTree();
        const suites = discoverSandboxedSuites(root);

        expect(suites.map(({id, specs, withPeer}) => ({id, count: specs.length, withPeer}))).toEqual([
            {id: "albums", count: 1, withPeer: false},
            {id: "photo__details", count: 2, withPeer: false},
            {id: "sharing", count: 1, withPeer: true},
        ]);
    });

    it("accepts directory/spec selectors and de-duplicates overlapping selections", () => {
        const root = makeTestTree();
        const suites = discoverSandboxedSuites(root, [
            "generated_tests/photo_details",
            "generated_tests/photo_details/a.spec.ts",
        ]);

        expect(suites).toHaveLength(1);
        expect(suites[0].specs).toEqual([
            "generated_tests/photo_details/a.spec.ts",
            "generated_tests/photo_details/b.spec.ts",
        ]);
    });
});

describe("parsePositiveInteger", () => {
    it("uses the fallback when unset and accepts a positive integer", () => {
        expect(parsePositiveInteger("COUNT", undefined, DEFAULT_SANDBOX_CONCURRENCY)).toBe(5);
        expect(parsePositiveInteger("COUNT", " 3 ", 5)).toBe(3);
    });

    it.each(["0", "-1", "2.5", "many"])("rejects %s", (value) => {
        expect(() => parsePositiveInteger("COUNT", value, 5)).toThrow(/positive integer/);
    });
});

describe("runWorkerPool", () => {
    it("caps active work and preserves result order", async () => {
        let active = 0;
        let maximum = 0;
        const results = await runWorkerPool([30, 5, 15, 1], 2, async (delay) => {
            active++;
            maximum = Math.max(maximum, active);
            await new Promise((resolve) => setTimeout(resolve, delay));
            active--;
            return delay;
        });

        expect(maximum).toBe(2);
        expect(results).toEqual([30, 5, 15, 1]);
    });
});

describe("runSandboxedSuites", () => {
    it("uses a separate copied sandbox per directory and always cleans it up", async () => {
        const root = makeTestTree();
        const starts: Array<{port: number; options: unknown}> = [];
        const cleanups: number[] = [];
        const runs: Array<{baseUrl: string; specs: string[]; options: unknown}> = [];

        const results = await runSandboxedSuites(
            {uiTestsDir: root, concurrency: 2, basePort: 6100},
            {
                log: () => undefined,
                startEnvironment: async (port, options) => {
                    starts.push({port, options});
                    return fakeEnvironment(port, async () => { cleanups.push(port); });
                },
                runTests: async (baseUrl, specs, options) => {
                    runs.push({baseUrl, specs: specs ?? [], options});
                    return passedResult();
                },
            },
        );

        expect(results.every((result) => result.success)).toBe(true);
        expect(starts).toHaveLength(3);
        expect(starts.map(({port}) => port).sort()).toEqual([6100, 6100, 6102]);
        expect(starts.map(({options}) => options)).toEqual(expect.arrayContaining([
            {withPeer: false, preseeded: true, copyPreseeded: true},
            {withPeer: true, preseeded: true, copyPreseeded: true},
        ]));
        expect(cleanups.sort()).toEqual([6100, 6100, 6102]);
        expect(runs.find(({specs}) => specs[0].includes("sharing"))?.options).toEqual(
            expect.objectContaining({environment: expect.objectContaining({PEER_URL: ""})}),
        );
    });

    it("continues other directories after a failure and cleans failed runs", async () => {
        const root = makeTestTree();
        const cleanups = jest.fn<() => Promise<void>>(async () => undefined);
        let runCount = 0;

        const results = await runSandboxedSuites(
            {uiTestsDir: root, concurrency: 2},
            {
                log: () => undefined,
                startEnvironment: async (port) => fakeEnvironment(port, cleanups),
                runTests: async () => {
                    runCount++;
                    if (runCount === 1) throw new Error("browser crashed");
                    return passedResult();
                },
            },
        );

        expect(results).toHaveLength(3);
        expect(results.filter((result) => !result.success)).toHaveLength(1);
        expect(results.find((result) => !result.success)?.error?.message).toBe("browser crashed");
        expect(cleanups).toHaveBeenCalledTimes(3);
    });
});
