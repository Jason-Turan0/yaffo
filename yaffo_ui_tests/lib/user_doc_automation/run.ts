/**
 * Run guide walkthroughs against a sandbox.
 *
 *   npm run isolatedEnvironment:start
 *   npm run docs:capture
 *   npm run docs:capture -- --promote library-basics/browsing-filtering
 *
 * Captures land in .staging/ and are compared against what is committed. Nothing
 * touches docs/ unless --promote is passed, so a plain run can answer "is anything
 * stale?" without dirtying the tree.
 */
import {readdirSync} from "fs";
import {join, resolve} from "path";
import {runWalkthroughs} from "./index";
import type {Walkthrough} from "./index";

// Resolved from the working directory, like isolated_runner.ts and the rest of the
// harness: every entry point here is run from yaffo_ui_tests/.
// Authored spec, generated walkthroughs, and transient staging live in the
// content tree; this module is infrastructure and lives under lib/.
const CONTENT_DIR = resolve(join(process.cwd(), "user_doc_automation"));
const BASE_URL = process.env.BASE_URL || "http://127.0.0.1:5002";
const GUIDE_DIR = resolve(process.env.GUIDE_DIR || join(CONTENT_DIR, "..", "..", "docs", "guide"));
const STAGING_DIR = join(CONTENT_DIR, ".staging");

const load = async (only: string[]): Promise<Walkthrough[]> => {
    const dir = join(CONTENT_DIR, "walkthroughs");
    const loaded: Walkthrough[] = [];
    for (const entry of readdirSync(dir).sort()) {
        if (!entry.endsWith(".ts")) continue;
        const module = await import(join(dir, entry));
        const walkthrough = module.default as Walkthrough | undefined;
        if (!walkthrough?.page) throw new Error(`${entry} has no default walkthrough export`);
        if (!only.length || only.includes(walkthrough.page)) loaded.push(walkthrough);
    }
    return loaded;
};

const main = async (): Promise<void> => {
    const args = process.argv.slice(2);
    const promote = args.includes("--promote");
    const only = args.filter((a) => !a.startsWith("-"));

    const walkthroughs = await load(only);
    if (!walkthroughs.length) {
        console.error(only.length ? `No walkthrough for: ${only.join(", ")}` : "No walkthroughs found");
        process.exit(1);
    }

    console.log(`Running ${walkthroughs.length} walkthrough(s) against ${BASE_URL}`);
    console.log(`  staging: ${STAGING_DIR}${promote ? "  (promoting changes)" : ""}\n`);

    const results = await runWalkthroughs(walkthroughs, {
        baseUrl: BASE_URL, guideDir: GUIDE_DIR, stagingDir: STAGING_DIR, promote,
    });

    let failed = 0;
    for (const result of results) {
        console.log(`${result.page}`);
        for (const shot of result.shots) {
            const mark = {new: "+", changed: "~", unchanged: "="}[shot.status];
            const detail = shot.diff?.reason === "size"
                ? "  reframed"
                : shot.diff?.diffPixels
                    ? `  ${shot.diff.diffPixels} px differ`
                    : "";
            console.log(`  ${mark} ${shot.target}  ${shot.width}x${shot.height}${detail}`);
        }
        const {urls, static: statics, templates, routes, serverObserver} = result.observation;
        console.log(`  deps: ${routes.length} route(s), ${templates.length} template(s), ` +
            `${statics.length} static file(s), ${urls.length} url(s)`);
        if (serverObserver === "unavailable") {
            console.log("        (server observer unavailable - start the app with YAFFO_DOC_OBSERVER=1)");
        }
        if (result.error) {
            failed++;
            console.error(`  ! ${result.error}`);
        }
    }

    const changed = results.flatMap((r) => r.shots).filter((s) => s.status !== "unchanged");
    console.log(`\n${changed.length} shot(s) new or changed${promote ? " and promoted" : ""}.`);
    if (failed) process.exit(1);
};

main().catch((e) => {
    console.error(e);
    process.exit(1);
});
