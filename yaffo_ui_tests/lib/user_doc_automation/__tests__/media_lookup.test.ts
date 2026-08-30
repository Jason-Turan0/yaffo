import {afterEach, describe, expect, it, jest} from "@jest/globals";
import {mediaIdByFilename} from "../media_lookup";

const realFetch = global.fetch;

afterEach(() => {
    global.fetch = realFetch;
});

const response = (body: string, status = 200): Response => ({
    ok: status >= 200 && status < 300,
    status,
    text: async () => body,
} as Response);

describe("mediaIdByFilename", () => {
    it("filters by an encoded filename and returns the first media id", async () => {
        const fetchMock = jest.fn<typeof fetch>(async () => response(
            '<a href="/media/view/73">matching item</a>'
        ));
        global.fetch = fetchMock as typeof fetch;

        await expect(mediaIdByFilename("http://app.test", "family trip #1.jpg"))
            .resolves.toBe(73);
        expect(fetchMock).toHaveBeenCalledWith(
            "http://app.test/?path=family%20trip%20%231.jpg&page-size=250"
        );
    });

    it("reports an HTTP failure with the requested URL", async () => {
        global.fetch = jest.fn<typeof fetch>(async () => response("unavailable", 503));
        await expect(mediaIdByFilename("http://app.test", "photo.jpg"))
            .rejects.toThrow("http://app.test/?path=photo.jpg&page-size=250 -> HTTP 503");
    });

    it("reports when the filtered gallery contains no media link", async () => {
        global.fetch = jest.fn<typeof fetch>(async () => response("<p>No media found</p>"));
        await expect(mediaIdByFilename("http://app.test", "missing.jpg"))
            .rejects.toThrow("the gallery has no media item named missing.jpg");
    });
});
