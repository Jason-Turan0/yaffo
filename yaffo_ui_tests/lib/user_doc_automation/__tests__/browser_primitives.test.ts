import {afterEach, describe, expect, it, jest} from "@jest/globals";
import type {BrowserContext, Page} from "@playwright/test";
import {
    PAD,
    paddedBox,
    resolveClip,
    resolveIgnoreRegions,
    rowCut,
} from "../framing";
import {blockOsSideEffects, OS_SIDE_EFFECT_ROUTES} from "../side_effects";
import {settle} from "../settle";

const originalDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");

afterEach(() => {
    if (originalDocument) Object.defineProperty(globalThis, "document", originalDocument);
    else delete (globalThis as {document?: unknown}).document;
    if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
    else delete (globalThis as {window?: unknown}).window;
});

const boxPage = (
    boxes: Record<string, {x: number; y: number; width: number; height: number} | null>,
    pageSize = {width: 500, height: 400}
): Page => ({
    locator: (selector: string) => ({
        first: () => ({boundingBox: async () => boxes[selector] ?? null}),
    }),
    evaluate: async () => pageSize,
} as unknown as Page);

describe("paddedBox", () => {
    it("pads an element while clamping the crop to the page", async () => {
        const page = boxPage({"#panel": {x: 4, y: 390, width: 480, height: 20}});
        await expect(paddedBox(page, "#panel")).resolves.toEqual({
            x: 0,
            y: 390 - PAD,
            width: 500,
            height: 26,
        });
    });

    it("fails clearly when the selected element has no box", async () => {
        await expect(paddedBox(boxPage({"#missing": null}), "#missing"))
            .rejects.toThrow("No bounding box for #missing");
    });
});

describe("rowCut", () => {
    const tile = (top: number, bottom: number) => ({
        getBoundingClientRect: () => ({top, bottom}),
    });

    const rowPage = (tiles: Array<ReturnType<typeof tile>> | null): Page => {
        Object.defineProperty(globalThis, "document", {
            configurable: true,
            value: {
                querySelector: () => tiles === null ? null : {querySelectorAll: () => tiles},
            },
        });
        Object.defineProperty(globalThis, "window", {
            configurable: true,
            value: {scrollY: 5},
        });
        return {
            evaluate: async (callback: (rule: unknown) => unknown, rule: unknown) => callback(rule),
        } as unknown as Page;
    };

    it("groups nearly aligned tiles and cuts midway before the next row", async () => {
        const page = rowPage([
            tile(10, 100), tile(13, 108),
            tile(150, 230), tile(152, 240),
        ]);
        await expect(rowCut(page, {grid: ".grid", item: ".tile", count: 1}))
            .resolves.toBe((113 + 155) / 2);
    });

    it("ends at the last row when no following row exists", async () => {
        const page = rowPage([tile(10, 100), tile(150, 230)]);
        await expect(rowCut(page, {grid: ".grid", item: ".tile", count: 9}))
            .resolves.toBe(235);
    });

    it("reports a missing grid", async () => {
        await expect(rowCut(rowPage(null), {grid: ".missing", item: ".tile", count: 1}))
            .rejects.toThrow("No grid .missing");
    });
});

describe("clip and ignore-region resolution", () => {
    it("uses the viewport when a shot has no clip selector", async () => {
        await expect(resolveClip(boxPage({}), {
            viewport: {width: 100, height: 100}, goto: "/",
        })).resolves.toBeUndefined();
    });

    it("trims a padded clip to the requested number of complete grid rows", async () => {
        const tiles = [
            {getBoundingClientRect: () => ({top: 40, bottom: 100})},
            {getBoundingClientRect: () => ({top: 150, bottom: 210})},
        ];
        Object.defineProperty(globalThis, "document", {
            configurable: true,
            value: {
                documentElement: {scrollWidth: 500, scrollHeight: 400},
                querySelector: () => ({querySelectorAll: () => tiles}),
            },
        });
        Object.defineProperty(globalThis, "window", {
            configurable: true,
            value: {scrollY: 0},
        });
        const page = {
            locator: () => ({first: () => ({
                boundingBox: async () => ({x: 100, y: 50, width: 300, height: 200}),
            })}),
            evaluate: async (callback: (value?: unknown) => unknown, value?: unknown) =>
                value === undefined ? callback() : callback(value),
        } as unknown as Page;

        await expect(resolveClip(page, {
            viewport: {width: 500, height: 400},
            goto: "/",
            clip: "#grid",
            rows: {grid: "#grid", item: ".tile", count: 1},
        })).resolves.toEqual({x: 84, y: 34, width: 332, height: 91});
    });

    it("converts ignored CSS boxes to device pixels relative to the captured crop", async () => {
        const page = boxPage({
            "#clock": {x: 120, y: 80, width: 40, height: 20},
            "#hidden": null,
        });
        const shot = {
            viewport: {width: 500, height: 400},
            goto: "/",
            ignoreRegions: ["#clock", "#hidden"],
        };
        await expect(resolveIgnoreRegions(
            page, shot, {x: 100, y: 50, width: 300, height: 200}, 2
        )).resolves.toEqual([{x: 40, y: 60, width: 80, height: 40}]);
    });
});

describe("blockOsSideEffects", () => {
    it("stubs every host-opening route with the endpoint's success response", async () => {
        const handlers: Array<(route: {fulfill: (options: unknown) => Promise<void>}) => Promise<void>> = [];
        const context = {
            route: jest.fn(async (_pattern: string, handler: typeof handlers[number]) => {
                handlers.push(handler);
            }),
        } as unknown as BrowserContext;

        await blockOsSideEffects(context);

        expect(context.route).toHaveBeenCalledTimes(OS_SIDE_EFFECT_ROUTES.length);
        expect((context.route as jest.Mock).mock.calls.map(([pattern]) => pattern))
            .toEqual(OS_SIDE_EFFECT_ROUTES);
        for (const handler of handlers) {
            const fulfill = jest.fn<(options: unknown) => Promise<void>>(async () => undefined);
            await handler({fulfill});
            expect(fulfill).toHaveBeenCalledWith({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify({success: true}),
            });
        }
    });
});

describe("settle", () => {
    it("continues after network-idle times out and stabilizes the page", async () => {
        const page = {
            waitForLoadState: jest.fn(async () => { throw new Error("stream never became idle"); }),
            addStyleTag: jest.fn(async () => undefined),
            evaluate: jest.fn(async () => undefined),
            waitForTimeout: jest.fn(async () => undefined),
        } as unknown as Page;

        await expect(settle(page)).resolves.toBeUndefined();

        expect(page.addStyleTag).toHaveBeenCalledWith(expect.objectContaining({
            content: expect.stringMatching(/animation: none.*\.notification/s),
        }));
        expect(page.evaluate).toHaveBeenCalledTimes(2);
        expect(page.waitForTimeout).toHaveBeenCalledWith(200);
    });

    it("waits for fonts and incomplete images, then removes focus", async () => {
        const blur = jest.fn();
        const addEventListener = jest.fn((
            _event: string,
            handler: () => void,
            _options: {once: boolean}
        ) => handler());
        Object.defineProperty(globalThis, "document", {
            configurable: true,
            value: {
                fonts: {ready: Promise.resolve()},
                images: [
                    {complete: true},
                    {complete: false, addEventListener},
                ],
                activeElement: {blur},
            },
        });
        const page = {
            waitForLoadState: jest.fn(async () => undefined),
            addStyleTag: jest.fn(async () => undefined),
            evaluate: jest.fn(async (callback: () => unknown) => callback()),
            waitForTimeout: jest.fn(async () => undefined),
        } as unknown as Page;

        await settle(page);

        expect(addEventListener).toHaveBeenCalledWith("load", expect.any(Function), {once: true});
        expect(addEventListener).toHaveBeenCalledWith("error", expect.any(Function), {once: true});
        expect(blur).toHaveBeenCalledTimes(1);
    });
});
