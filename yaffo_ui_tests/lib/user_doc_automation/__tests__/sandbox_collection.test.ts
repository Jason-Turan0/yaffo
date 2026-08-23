import {afterEach, describe, expect, it, jest} from "@jest/globals";
import {gatherSandboxFacts} from "../sandbox_facts";

const realFetch = global.fetch;

afterEach(() => {
    global.fetch = realFetch;
});

const response = (body: string, status = 200): Response => ({
    ok: status >= 200 && status < 300,
    status,
    text: async () => body,
} as Response);

describe("gatherSandboxFacts", () => {
    it("asks the app to classify media and keeps stable, deduplicated basenames", async () => {
        const photoHtml = [
            '<img title="/private/run/photos/newest.JPG">',
            '<img title="/private/run/photos/older-image.png">',
            '<img title="/another/root/newest.JPG">',
            '<img title="basename-only.jpg">',
        ].join("\n");
        const videoHtml = '<video title="/private/run/videos/clip-one.mp4"></video>';
        const fetchMock = jest.fn<typeof fetch>(async (input) =>
            response(String(input).includes("media-type=photo") ? photoHtml : videoHtml));
        global.fetch = fetchMock;

        await expect(gatherSandboxFacts("http://app.test")).resolves.toEqual({
            photos: ["newest.JPG", "older-image.png"],
            videos: ["clip-one.mp4"],
        });
        expect(fetchMock.mock.calls.map(([url]) => String(url)).sort()).toEqual([
            "http://app.test/?media-type=photo&page-size=250",
            "http://app.test/?media-type=video&page-size=250",
        ]);
    });

    it("distinguishes an empty gallery from a successful populated response", async () => {
        global.fetch = jest.fn<typeof fetch>(async () => response("<p>No media</p>"));
        await expect(gatherSandboxFacts("http://app.test")).resolves.toEqual({
            photos: [],
            videos: [],
            error: "the gallery returned no media items",
        });
    });

    it("returns an actionable HTTP error without rejecting the generation run", async () => {
        global.fetch = jest.fn<typeof fetch>(async (input) =>
            String(input).includes("media-type=video")
                ? response("unavailable", 503)
                : response('<img title="/fixture/photo.jpg">'));
        const facts = await gatherSandboxFacts("http://app.test");
        expect(facts).toEqual({
            photos: [],
            videos: [],
            error: "http://app.test/?media-type=video&page-size=250 -> HTTP 503",
        });
    });

    it("preserves a non-Error rejection as diagnostic text", async () => {
        global.fetch = jest.fn<typeof fetch>(async () => { throw "socket closed"; });
        await expect(gatherSandboxFacts("http://app.test")).resolves.toEqual({
            photos: [], videos: [], error: "socket closed",
        });
    });
});
