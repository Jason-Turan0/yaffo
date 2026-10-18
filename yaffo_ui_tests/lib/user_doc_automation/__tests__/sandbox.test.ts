import {afterAll, beforeEach, describe, expect, it, jest} from "@jest/globals";

const appContainerRunning = jest.fn<() => boolean>();
const appContainerHasPeer = jest.fn<() => boolean>();
const runAppContainer = jest.fn<(...args: unknown[]) => number>();
const stopAppContainer = jest.fn<() => number>();
const prepareAppResources = jest.fn();
const spawnSync = jest.fn();
const originalFetch = globalThis.fetch;
const fetchMock = jest.fn<typeof fetch>();
afterAll(() => { globalThis.fetch = originalFetch; });

await jest.unstable_mockModule("../docker", () => ({
    appContainerRunning, appContainerHasPeer, runAppContainer, stopAppContainer, prepareAppResources,
    dockerAvailable: () => true,
    docsMounts: () => ({data: "data", assets: "assets"}),
    snapshotDockerEnv: () => ({}),
    DOCS_APP_CONTAINER: "yaffo-docs-app", DOCS_APP_PORT: 5002, DOCS_PEER_PORT: 5003,
}));
await jest.unstable_mockModule("child_process", () => ({spawnSync}));
const {main} = await import("../sandbox");

beforeEach(() => {
    jest.clearAllMocks();
    appContainerRunning.mockReturnValue(false);
    appContainerHasPeer.mockReturnValue(false);
    runAppContainer.mockReturnValue(0);
    stopAppContainer.mockReturnValue(0);
    fetchMock.mockReset();
    globalThis.fetch = fetchMock;
});

describe("docs sandbox peer", () => {
    it("forwards --peer during fixture build", async () => {
        expect(await main(["build", "--peer"], {})).toBe(0);
        expect(runAppContainer).toHaveBeenCalledWith(expect.objectContaining({mode: "fixture", withPeer: true}), {});
    });

    it("waits for both HTTP servers when starting detached", async () => {
        fetchMock.mockResolvedValue({ok: true} as Response);
        expect(await main(["start", "--peer", "--detach"], {})).toBe(0);
        expect(runAppContainer).toHaveBeenCalledWith(expect.objectContaining({mode: "serve", withPeer: true, detach: true}), {});
        expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(["http://127.0.0.1:5002", "http://127.0.0.1:5003"]);
    });

    it("does not report ready when only the primary started", async () => {
        fetchMock.mockImplementation(async url => ({ok: String(url).endsWith(":5002")} as Response));
        expect(await main(["start", "--peer", "--detach"], {})).toBe(1);
        expect(spawnSync).toHaveBeenCalled();
    });

    it("requires restarting an existing single-instance sandbox", async () => {
        appContainerRunning.mockReturnValue(true);
        expect(await main(["start", "--peer"], {})).toBe(1);
        expect(runAppContainer).not.toHaveBeenCalled();
        expect(stopAppContainer).not.toHaveBeenCalled();
    });

    it("accepts an existing peer sandbox only after both servers respond", async () => {
        appContainerRunning.mockReturnValue(true);
        appContainerHasPeer.mockReturnValue(true);
        fetchMock.mockResolvedValue({ok: true} as Response);
        expect(await main(["start", "--peer"], {})).toBe(0);
        expect(fetchMock).toHaveBeenCalledTimes(2);
        expect(runAppContainer).not.toHaveBeenCalled();
    });
});
