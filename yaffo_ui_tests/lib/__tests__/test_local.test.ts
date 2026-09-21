import {describe, expect, it, jest} from "@jest/globals";

import {runLocalSuite} from "../../scripts/test_local";
import {IsolatedEnvironment, startIsolatedEnvironment, TestRunResult} from "../services/isolated_runner";
import {runPlaywrightTests} from "../services/run_playwright_tests";

function setup(sharing: boolean) {
    const cleanup = jest.fn<() => Promise<void>>().mockResolvedValue(undefined);
    const environment: IsolatedEnvironment = {
        tempDir: "/tmp/local-test", port: 5502, baseUrl: "http://127.0.0.1:5502",
        flaskProcess: null, taskqProcess: null, cleanup,
        ...(sharing ? {peer: {
            tempDir: "/tmp/local-test-peer", port: 5503, baseUrl: "http://127.0.0.1:5503",
            flaskProcess: null, taskqProcess: null,
        }} : {}),
    };
    const result: TestRunResult = {
        success: false, exitCode: 1, output: "", tests: [],
        summary: {total: 1, passed: 0, failed: 1, skipped: 0},
    };
    const dependencies = {
        startEnvironment: jest.fn<typeof startIsolatedEnvironment>().mockResolvedValue(environment),
        runTests: jest.fn<typeof runPlaywrightTests>().mockResolvedValue(result),
    };
    const id = sharing ? "sharing" : "albums";
    const suite = {id, directory: `generated_tests/${id}`, specs: [`generated_tests/${id}/${id}.spec.ts`], withPeer: sharing};
    return {cleanup, dependencies, suite};
}

describe("local interactive runner lifecycle", () => {
    it.each([false, true])("starts the correct environment (sharing=%s), forwards mode and preserves test failure", async sharing => {
        const {suite, dependencies, cleanup} = setup(sharing);
        expect(await runLocalSuite(suite, {port: 5502, fresh: false, mode: "headed"}, dependencies)).toBe(1);
        expect(dependencies.startEnvironment).toHaveBeenCalledWith(5502, {
            withPeer: sharing, preseeded: true, copyPreseeded: true,
        });
        expect(dependencies.runTests).toHaveBeenCalledWith("http://127.0.0.1:5502", suite.specs, expect.objectContaining({
            mode: "headed",
            environment: expect.objectContaining({PEER_URL: sharing ? "http://127.0.0.1:5503" : ""}),
        }));
        expect(cleanup).toHaveBeenCalledTimes(1);
    });

    it("cleans up a fresh environment even when Playwright cannot start", async () => {
        const {suite, dependencies, cleanup} = setup(false);
        dependencies.runTests.mockRejectedValue(new Error("spawn failed"));
        await expect(runLocalSuite(suite, {port: 5502, fresh: true, mode: "ui"}, dependencies)).rejects.toThrow("spawn failed");
        expect(dependencies.startEnvironment).toHaveBeenCalledWith(5502, {
            withPeer: false, preseeded: false, copyPreseeded: false,
        });
        expect(cleanup).toHaveBeenCalledTimes(1);
    });

    it("cleans up without launching tests if interrupted during environment startup", async () => {
        const {suite, dependencies, cleanup} = setup(true);
        const controller = new AbortController();
        const environment = await dependencies.startEnvironment(5502);
        dependencies.startEnvironment.mockImplementation(async () => {
            controller.abort();
            return environment;
        });
        expect(await runLocalSuite(suite, {port: 5502, fresh: false, mode: "headless", signal: controller.signal}, dependencies)).toBe(130);
        expect(dependencies.runTests).not.toHaveBeenCalled();
        expect(cleanup).toHaveBeenCalledTimes(1);
    });
});
