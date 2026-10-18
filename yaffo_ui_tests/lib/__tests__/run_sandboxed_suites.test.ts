import {afterEach, describe, expect, it, jest} from "@jest/globals";
import {mkdtempSync, mkdirSync, rmSync, writeFileSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {createServer} from "node:net";

import {IsolatedEnvironment, TestRunResult} from "@lib/services/isolated_runner";
import {
    DEFAULT_SANDBOX_CONCURRENCY,
    discoverSandboxedSuites,
    findSandboxPortSlots,
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
        // The fallback is returned as given. Asserting the constant's current
        // value instead made this fail when the default concurrency changed,
        // which is a property of the default, not of this function.
        expect(parsePositiveInteger("COUNT", undefined, DEFAULT_SANDBOX_CONCURRENCY))
            .toBe(DEFAULT_SANDBOX_CONCURRENCY);
        expect(parsePositiveInteger("COUNT", " 3 ", 5)).toBe(3);
    });

    it.each(["0", "-1", "2.5", "many"])("rejects %s", (value) => {
        expect(() => parsePositiveInteger("COUNT", value, 2)).toThrow(/positive integer/);
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

describe("findSandboxPortSlots", () => {
    it("skips occupied primary and peer web ports without overlapping workers", async () => {
        const occupied = new Set([6100, 6103]);
        const slots = await findSandboxPortSlots(6100, 2, false,
            async (port) => !occupied.has(port));
        expect(slots).toEqual([6104, 6106]);
    });

    it("skips occupied QUIC ports for either instance when sharing is needed", async () => {
        const occupied = new Set([16100, 16103]);
        const available = async (port: number, protocol: "tcp" | "udp") =>
            protocol !== "udp" || !occupied.has(port);
        expect(await findSandboxPortSlots(6100, 2, true, available)).toEqual([6104, 6106]);
        expect(await findSandboxPortSlots(6100, 2, false, available)).toEqual([6100, 6102]);
    });

    it("bounds the search by the valid web and QUIC port ranges", async () => {
        const available = async () => true;
        expect(await findSandboxPortSlots(65534, 1, false, available)).toEqual([65534]);
        expect(await findSandboxPortSlots(55534, 1, true, available)).toEqual([55534]);
        await expect(findSandboxPortSlots(55534, 2, true, available)).rejects.toThrow(/free sandbox port pairs/);
        await expect(findSandboxPortSlots(65534, 1, false, async () => false))
            .rejects.toThrow(/free sandbox port pairs/);
    });

    it("detects an actual listening server and leaves it running", async () => {
        const server = createServer();
        await new Promise<void>((resolveListen, reject) => {
            server.once("error", reject);
            server.listen(0, "127.0.0.1", resolveListen);
        });
        try {
            const address = server.address();
            if (!address || typeof address === "string") throw new Error("Missing listener address");
            const slots = await findSandboxPortSlots(address.port, 1, false);
            expect(slots[0]).toBeGreaterThan(address.port);
            expect(server.listening).toBe(true);
        } finally {
            await new Promise<void>((resolveClose) => server.close(() => resolveClose()));
        }
    });
});

describe("runSandboxedSuites", () => {
    it("uses free port pairs, passes their URLs to tests, and cleans each copied sandbox", async () => {
        const root = makeTestTree();
        const starts: Array<{port: number; options: unknown}> = [];
        const cleanups: number[] = [];
        const runs: Array<{baseUrl: string; specs: string[]; options: unknown}> = [];

        const results = await runSandboxedSuites(
            {uiTestsDir: root, concurrency: 2, basePort: 6100},
            {
                isPortAvailable: async (port) => ![6100, 6103].includes(port),
                log: () => undefined,
                startEnvironment: async (port, options) => {
                    starts.push({port, options});
                    return {
                        ...fakeEnvironment(port, async () => { cleanups.push(port); }),
                        peer: options.withPeer ? fakeEnvironment(port + 1, async () => undefined) : undefined,
                    };
                },
                runTests: async (baseUrl, specs, options) => {
                    runs.push({baseUrl, specs: specs ?? [], options});
                    return passedResult();
                },
            },
        );

        expect(results.every((result) => result.success)).toBe(true);
        expect(starts).toHaveLength(3);
        expect(starts.map(({port}) => port).sort()).toEqual([6104, 6104, 6106]);
        expect(starts.map(({options}) => options)).toEqual(expect.arrayContaining([
            {withPeer: false, preseeded: true, copyPreseeded: true, withFakeSyncServices: false},
            {withPeer: true, preseeded: true, copyPreseeded: true, withFakeSyncServices: false},
        ]));
        expect(cleanups.sort()).toEqual([6104, 6104, 6106]);
        expect(runs.map(({baseUrl}) => baseUrl).sort()).toEqual([
            "http://127.0.0.1:6104", "http://127.0.0.1:6104", "http://127.0.0.1:6106",
        ]);
        expect(runs.find(({specs}) => specs[0].includes("sharing"))?.options).toEqual(
            expect.objectContaining({environment: expect.objectContaining({PEER_URL: "http://127.0.0.1:6105"})}),
        );
    });

    it("starts the fake services for sync suites and hands their URLs and data dir to the specs", async () => {
        const root = makeTestTree();
        const spec = join(root, "generated_tests/sync_automations/sync_automations.spec.ts");
        mkdirSync(join(spec, ".."), {recursive: true});
        writeFileSync(spec, "test('placeholder', () => {});\n");
        const starts: Array<{options: unknown}> = [];
        const runs: Array<{specs: string[]; options: unknown}> = [];

        await runSandboxedSuites(
            {uiTestsDir: root, selectors: ["generated_tests/sync_automations"], basePort: 6200},
            {
                isPortAvailable: async () => true,
                log: () => undefined,
                startEnvironment: async (port, options) => {
                    starts.push({options});
                    return {...fakeEnvironment(port, async () => undefined), fakeImmichUrl: "http://127.0.0.1:7001",
                        fakeOneDriveUrl: "http://127.0.0.1:7002", fakeFlickrUrl: "http://127.0.0.1:7003"};
                },
                runTests: async (_baseUrl, specs, options) => {
                    runs.push({specs: specs ?? [], options});
                    return passedResult();
                },
            },
        );

        expect(starts).toEqual([{options: expect.objectContaining({withFakeSyncServices: true})}]);
        expect(runs[0].options).toEqual(expect.objectContaining({environment: expect.objectContaining({
            FAKE_IMMICH_URL: "http://127.0.0.1:7001",
            FAKE_ONEDRIVE_URL: "http://127.0.0.1:7002",
            FAKE_FLICKR_URL: "http://127.0.0.1:7003",
            YAFFO_DATA_DIR: "/tmp/suite-6200",
        })}));
    });

    it("continues other directories after a failure and cleans failed runs", async () => {
        const root = makeTestTree();
        const cleanups = jest.fn<() => Promise<void>>(async () => undefined);
        let runCount = 0;

        const results = await runSandboxedSuites(
            {uiTestsDir: root, concurrency: 2},
            {
                isPortAvailable: async () => true,
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
