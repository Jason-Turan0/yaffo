import {afterEach, beforeEach, describe, expect, it} from "@jest/globals";
import {mkdirSync, mkdtempSync, rmSync, writeFileSync} from "fs";
import {tmpdir} from "os";
import {join} from "path";
import {loadWalkthroughs} from "../load";

let contentDir: string;

beforeEach(() => {
    contentDir = mkdtempSync(join(tmpdir(), "yaffo-walkthroughs-"));
});

afterEach(() => {
    rmSync(contentDir, {recursive: true, force: true});
});

const walkthrough = (area: string, page: string, source?: string): void => {
    const dir = join(contentDir, area, page);
    mkdirSync(dir, {recursive: true});
    writeFileSync(
        join(dir, `${page}.ts`),
        source ?? `export default {page: ${JSON.stringify(`${area}/${page}`)}, shots: {}};`,
        "utf8"
    );
};

describe("loadWalkthroughs", () => {
    it("discovers page modules while ignoring support and hidden areas", async () => {
        walkthrough("library", "browsing");
        walkthrough("settings", "labels");
        walkthrough("_support", "helper");
        walkthrough(".private", "secret");
        mkdirSync(join(contentDir, "library", "notes"), {recursive: true});
        writeFileSync(join(contentDir, "library", "README.md"), "not a page directory", "utf8");

        const loaded = await loadWalkthroughs(contentDir);
        expect(loaded.map(({page}) => page).sort()).toEqual([
            "library/browsing",
            "settings/labels",
        ]);
    });

    it("filters by the walkthrough's declared page id", async () => {
        walkthrough("library", "browsing");
        walkthrough("settings", "labels");
        const loaded = await loadWalkthroughs(contentDir, ["settings/labels"]);
        expect(loaded.map(({page}) => page)).toEqual(["settings/labels"]);
    });

    it("rejects a module without a default walkthrough export", async () => {
        walkthrough("library", "broken", "export const value = 1;");
        await expect(loadWalkthroughs(contentDir)).rejects.toThrow(
            /broken\.ts has no default walkthrough export/
        );
    });
});
