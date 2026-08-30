import {existsSync, readdirSync} from "fs";
import {join} from "path";
import type {Walkthrough} from "./types";

/**
 * Walkthroughs live one folder per page, mirroring the guide:
 * `user_doc_automation/{area}/{page}/{page}.ts` — alongside that page's catalog,
 * lockfile, and memories, the way `generated_tests/{feature}/` is laid out.
 *
 * Shared by the host entry point and the in-container worker so a containerized run
 * and a local one cannot disagree about which pages exist.
 */
export const loadWalkthroughs = async (
    contentDir: string,
    only: string[] = []
): Promise<Walkthrough[]> => {
    const loaded: Walkthrough[] = [];
    for (const area of readdirSync(contentDir, {withFileTypes: true})) {
        if (!area.isDirectory() || area.name.startsWith("_") || area.name.startsWith(".")) continue;
        const areaDir = join(contentDir, area.name);
        for (const page of readdirSync(areaDir, {withFileTypes: true})) {
            if (!page.isDirectory()) continue;
            const module = join(areaDir, page.name, `${page.name}.ts`);
            if (!existsSync(module)) continue;
            const walkthrough = (await import(module)).default as Walkthrough | undefined;
            if (!walkthrough?.page) throw new Error(`${module} has no default walkthrough export`);
            if (!only.length || only.includes(walkthrough.page)) loaded.push(walkthrough);
        }
    }
    return loaded;
};
