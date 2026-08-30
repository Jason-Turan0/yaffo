import {afterEach, describe, expect, it, jest} from "@jest/globals";
import type {BrowserContext, Request} from "@playwright/test";
import {
    createObserver,
    PAGE_HEADER,
    RUN_HEADER,
    takeServerObservation,
} from "../observe";

const realFetch = global.fetch;

afterEach(() => {
    global.fetch = realFetch;
});

describe("takeServerObservation", () => {
    it("requests the encoded run bucket and records the server dependencies", async () => {
        const fetchMock = jest.fn<typeof fetch>(async () => ({
            ok: true,
            json: async () => ({routes: ["yaffo/routes/home.py"], templates: ["yaffo/templates/home.html"]}),
        } as Response));
        global.fetch = fetchMock as typeof fetch;

        await expect(takeServerObservation("http://app.test", "run/id"))
            .resolves.toEqual({
                routes: ["yaffo/routes/home.py"],
                templates: ["yaffo/templates/home.html"],
                serverObserver: "recorded",
            });
        expect(fetchMock).toHaveBeenCalledWith("http://app.test/__doc_observer__/run%2Fid");
    });

    it("defaults omitted dependency arrays", async () => {
        global.fetch = jest.fn(async () => ({
            ok: true,
            json: async () => ({}),
        } as Response)) as typeof fetch;
        await expect(takeServerObservation("http://app.test", "run"))
            .resolves.toEqual({routes: [], templates: [], serverObserver: "recorded"});
    });

    it.each([
        ["an unavailable endpoint", async () => ({ok: false} as Response)],
        ["a network failure", async () => { throw new Error("ECONNREFUSED"); }],
    ])("reports %s without failing capture", async (_label, implementation) => {
        global.fetch = jest.fn(implementation) as typeof fetch;
        await expect(takeServerObservation("http://app.test", "run"))
            .resolves.toEqual({routes: [], templates: [], serverObserver: "unavailable"});
    });
});

describe("createObserver", () => {
    it("collects, normalizes, deduplicates, and sorts same-origin dependencies", () => {
        let record: ((request: Request) => void) | undefined;
        const context = {
            on: jest.fn((_event: string, handler: (request: Request) => void) => { record = handler; }),
        } as unknown as BrowserContext;
        const observer = createObserver("library/browsing", "http://app.test:5002/root");
        observer.attach(context);

        const request = (url: string): Request => ({url: () => url} as Request);
        for (const url of [
            "http://app.test:5002/media/view/42",
            "http://app.test:5002/media/view/7",
            "http://app.test:5002/settings/labels",
            "http://app.test:5002/static/js/gallery.js?v=1",
            "http://app.test:5002/static/css/app.css",
            "https://cdn.example.com/library.js",
            "not a URL",
        ]) record?.(request(url));

        expect(observer.result({
            routes: ["yaffo/routes/home.py"],
            templates: ["yaffo/templates/home.html"],
            serverObserver: "recorded",
        })).toEqual({
            page: "library/browsing",
            urls: ["/media/view/:id", "/settings/labels"],
            static: ["yaffo/static/css/app.css", "yaffo/static/js/gallery.js"],
            routes: ["yaffo/routes/home.py"],
            templates: ["yaffo/templates/home.html"],
            serverObserver: "recorded",
        });
    });

    it("uses explicit request headers for page and run isolation", () => {
        expect(PAGE_HEADER).toBe("X-Yaffo-Doc-Page");
        expect(RUN_HEADER).toBe("X-Yaffo-Doc-Run");
    });

    it("reports unavailable server data by default", () => {
        expect(createObserver("page", "http://app.test").result()).toMatchObject({
            routes: [], templates: [], serverObserver: "unavailable",
        });
    });
});
