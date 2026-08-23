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
import {createHash} from "crypto";
import {existsSync, readFileSync, readdirSync, writeFileSync} from "fs";
import {join, resolve} from "path";
import {runWalkthroughs} from "./index";
import type {Walkthrough, WalkthroughResult} from "./index";

// Resolved from the working directory, like isolated_runner.ts and the rest of the
// harness: every entry point here is run from yaffo_ui_tests/.
// Authored spec, generated walkthroughs, and transient staging live in the
// content tree; this module is infrastructure and lives under lib/.
const CONTENT_DIR = resolve(join(process.cwd(), "user_doc_automation"));
// The sandbox to drive. Deliberately its own variable: BASE_URL is overloaded in
// this repo — .env points it at the dev app on :5000 for other tooling, and a docs
// run against the wrong instance fails in confusing ways.
const BASE_URL = process.env.DOCS_BASE_URL || "http://127.0.0.1:5002";
const GUIDE_DIR = resolve(process.env.GUIDE_DIR || join(CONTENT_DIR, "..", "..", "docs", "guide"));
const STAGING_DIR = join(CONTENT_DIR, ".staging");

/**
 * Walkthroughs live one folder per page, mirroring the guide:
 * `user_doc_automation/{area}/{page}/{page}.ts` — alongside that page's catalog,
 * lockfile, and memories, the way `generated_tests/{feature}/` is laid out.
 */
const load = async (only: string[]): Promise<Walkthrough[]> => {
    const loaded: Walkthrough[] = [];
    for (const area of readdirSync(CONTENT_DIR, {withFileTypes: true})) {
        if (!area.isDirectory() || area.name.startsWith("_") || area.name.startsWith(".")) continue;
        const areaDir = join(CONTENT_DIR, area.name);
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

/**
 * The page's fingerprint: what its walkthrough touched, and what its shots looked
 * like when they were last written. Committed beside the walkthrough so a diff shows
 * which page's dependencies moved.
 */
const writeLockfile = (result: WalkthroughResult): void => {
    const [area, name] = [result.page.slice(0, result.page.indexOf("/")),
                          result.page.slice(result.page.indexOf("/") + 1)];
    const dir = join(CONTENT_DIR, area, name);
    if (!existsSync(dir)) return;
    const lock = {
        page: result.page,
        // Set once the watermark lands; until then every diff is against HEAD.
        lastVerifiedSha: null as string | null,
        observed: result.observation,
        shots: Object.fromEntries(result.shots.map((shot) => [
            shot.target,
            {
                width: shot.width,
                height: shot.height,
                // Detects a committed screenshot being replaced outside this pipeline;
                // the staleness comparison itself is per-pixel, not by hash.
                sha256: existsSync(join(GUIDE_DIR, shot.target))
                    ? createHash("sha256").update(readFileSync(join(GUIDE_DIR, shot.target))).digest("hex")
                    : null,
            },
        ])),
    };
    writeFileSync(join(dir, `${name}.lock.json`), JSON.stringify(lock, null, 4) + "\n");
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
        if (promote) writeLockfile(result);
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
