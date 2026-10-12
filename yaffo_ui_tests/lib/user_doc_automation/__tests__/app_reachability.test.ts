import {describe, expect, it, jest} from "@jest/globals";
import {APP_UNREACHABLE, isAppUnreachable, waitForApp} from "../app_reachability";

describe("isAppUnreachable", () => {
    it("recognizes refused, reset and unresolved connections", () => {
        expect(isAppUnreachable("page.goto: net::ERR_CONNECTION_REFUSED at http://yaffo-docs-app:5002/")).toBe(true);
        expect(isAppUnreachable("fetch failed: connect ECONNREFUSED 127.0.0.1:5002")).toBe(true);
        expect(isAppUnreachable("page.goto: net::ERR_NAME_NOT_RESOLVED")).toBe(true);
        expect(isAppUnreachable(`${APP_UNREACHABLE}: the app didn't answer`)).toBe(true);
    });

    it("leaves real walkthrough failures alone", () => {
        expect(isAppUnreachable("locator.waitFor: Timeout 5000ms exceeded")).toBe(false);
        expect(isAppUnreachable(undefined)).toBe(false);
    });
});

describe("waitForApp", () => {
    it("returns once the app answers", async () => {
        const fetchImpl = jest.fn<typeof fetch>()
            .mockRejectedValueOnce(new Error("ECONNREFUSED"))
            .mockResolvedValue(new Response("ok"));
        await expect(waitForApp("http://app", {timeoutMs: 1_000, intervalMs: 1, fetchImpl})).resolves.toBe(true);
        expect(fetchImpl).toHaveBeenCalledTimes(2);
    });

    it("gives up after the timeout", async () => {
        const fetchImpl = jest.fn<typeof fetch>().mockRejectedValue(new Error("ECONNREFUSED"));
        await expect(waitForApp("http://app", {timeoutMs: 20, intervalMs: 5, fetchImpl})).resolves.toBe(false);
    });
});
