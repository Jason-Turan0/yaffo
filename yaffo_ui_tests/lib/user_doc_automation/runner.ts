import {chromium} from "@playwright/test";
import type {Browser} from "@playwright/test";
import {randomUUID} from "crypto";
import {copyFileSync, existsSync, mkdirSync, readdirSync, rmSync, writeFileSync} from "fs";
import {dirname, join} from "path";
import {compareShots} from "./compare";
import type {CompareResult} from "./compare";
import type {Box} from "./framing";
import {resolveClip, resolveIgnoreRegions} from "./framing";
import {toWebp} from "./encode";
import {createObserver, PAGE_HEADER, RUN_HEADER, takeServerObservation} from "./observe";
import type {Observation} from "./observe";
import {mediaIdByFilename} from "./media_lookup";
import {blockOsSideEffects} from "./side_effects";
import {settle} from "./settle";
import type {Walkthrough} from "./types";

/**
 * Retina. The docs site renders these at roughly half their pixel width, so a 1x
 * capture looks soft.
 */
export const DEVICE_SCALE_FACTOR = 2;

export type ShotStatus = "new" | "changed" | "unchanged";

/**
 * A shot as it comes off the browser: a PNG and the geometry needed to judge it.
 *
 * This is the half that can run in a container. Everything downstream of it — WebP
 * encoding, pixel comparison, promotion into the guide — needs the project virtualenv
 * for Pillow and NumPy, which the Playwright image does not have and should not grow.
 * So the boundary sits exactly here, and this struct is what crosses it.
 *
 * The PNG's path is deliberately *not* a field. It is a pure function of the staging
 * directory and the target, so the host recomputes it against its own paths rather
 * than translating a container path back — the file is the same file either way,
 * reached through a bind mount.
 */
export interface RawShot {
    /** Path relative to docs/guide — the shot's identity. */
    target: string;
    width: number;
    height: number;
    ignore: Box[];
}

export interface RawResult {
    page: string;
    shots: RawShot[];
    observation: Observation;
    error?: string;
}

export interface ShotResult extends RawShot {
    staged: string;
    status: ShotStatus;
    /** Pixel comparison against the committed image. Absent for a new shot. */
    diff?: CompareResult;
}

export interface WalkthroughResult {
    page: string;
    shots: ShotResult[];
    observation: Observation;
    error?: string;
}

export interface CaptureOptions {
    baseUrl: string;
    /** Where captures land before anything is compared. Never docs/. */
    stagingDir: string;
}

export interface ProcessOptions {
    /** docs/guide */
    guideDir: string;
    stagingDir: string;
    /** Copy changed and new shots into the guide. */
    promote?: boolean;
}

export type RunOptions = CaptureOptions & ProcessOptions;

/** Where `capture` leaves its raw output for `process` to pick up. */
export const RAW_FILENAME = "raw.json";

/**
 * A page's assets always live at docs/guide/{area}/assets/{page}/. There are no
 * shared images, so a shot's destination is a pure function of the page id.
 */
const targetFor = (pageId: string, filename: string): string => {
    const [area, name] = [pageId.slice(0, pageId.indexOf("/")), pageId.slice(pageId.indexOf("/") + 1)];
    return join(area, "assets", name, filename);
};

/** The PNG a capture writes for a target. Recomputed on each side of the boundary. */
const stagedPngFor = (stagingDir: string, target: string): string =>
    join(stagingDir, target).replace(/\.webp$/, ".png");

const captureOne = async (
    browser: Browser,
    walkthrough: Walkthrough,
    options: CaptureOptions
): Promise<RawResult> => {
    const {baseUrl, stagingDir} = options;
    // A fresh id per run, so this walkthrough's server-side records are its own and
    // collecting them cannot disturb anything else running against the same app.
    const runId = randomUUID();
    const observer = createObserver(walkthrough.page, baseUrl);
    const context = await browser.newContext({
        // Relative URLs resolve against this, the same as `playwright.config.ts` does
        // for generated specs. Without it `page.waitForURL("/")` never matches — the
        // page really is at "/", but the pattern has nothing to resolve against, and
        // under a containerized capture the address is `host.docker.internal` anyway.
        // A walkthrough written in the idiom of the Playwright specs should just work.
        baseURL: baseUrl,
        // Fixed so a shot never changes because the machine's settings differ.
        deviceScaleFactor: DEVICE_SCALE_FACTOR,
        locale: "en-US",
        timezoneId: "America/Chicago",
        colorScheme: "light",
        reducedMotion: "reduce",
        // Lets the server-side observer attribute every request to this run.
        extraHTTPHeaders: {[PAGE_HEADER]: walkthrough.page, [RUN_HEADER]: runId},
    });
    observer.attach(context);
    // Before any walkthrough code runs: clicking "Open File" would otherwise launch
    // Preview on the machine running the capture.
    await blockOsSideEffects(context);

    const shots: RawShot[] = [];
    let error: string | undefined;
    try {
        for (const [filename, shot] of Object.entries(walkthrough.shots)) {
            const page = await context.newPage();
            try {
                await page.setViewportSize(shot.viewport);
                const path = typeof shot.goto === "string"
                    ? shot.goto
                    : await shot.goto({
                        baseUrl,
                        mediaIdByFilename: (filename) => mediaIdByFilename(baseUrl, filename),
                    });
                await page.goto(`${baseUrl}${path}`, {waitUntil: "domcontentloaded"});
                await settle(page);
                if (shot.setup) {
                    await shot.setup(page);
                    await settle(page);
                }

                const clip = await resolveClip(page, shot);
                const ignore = await resolveIgnoreRegions(page, shot, clip, DEVICE_SCALE_FACTOR);

                const target = targetFor(walkthrough.page, filename);
                const stagedPng = stagedPngFor(stagingDir, target);
                mkdirSync(dirname(stagedPng), {recursive: true});
                await page.screenshot({path: stagedPng, clip, scale: "device"});

                shots.push({
                    target,
                    width: Math.round(clip?.width ?? shot.viewport.width),
                    height: Math.round(clip?.height ?? shot.viewport.height),
                    ignore,
                });
            } finally {
                await page.close();
            }
        }

        if (walkthrough.flows) {
            const page = await context.newPage();
            try {
                await page.setViewportSize({width: 1400, height: 1100});
                await walkthrough.flows({
                    page,
                    visit: async (path: string) => {
                        await page.goto(`${baseUrl}${path}`, {waitUntil: "domcontentloaded"});
                        await settle(page);
                    },
                    mediaIdByFilename: (filename) => mediaIdByFilename(baseUrl, filename),
                });
            } finally {
                await page.close();
            }
        }
    } catch (e) {
        // Playwright's message says only which primitive timed out. Its stack carries
        // the generated walkthrough file and line the repair agent actually needs.
        error = e instanceof Error ? (e.stack ?? e.message) : String(e);
    } finally {
        await context.close();
    }

    const server = await takeServerObservation(baseUrl, runId);
    return {page: walkthrough.page, shots, observation: observer.result(server), error};
};

/**
 * Drive the browser and leave PNGs in staging. Safe to run in a container; needs
 * nothing but Node, Chromium, and a route to the app.
 */
export const captureWalkthroughs = async (
    walkthroughs: Walkthrough[],
    options: CaptureOptions
): Promise<RawResult[]> => {
    // Emptied rather than removed: under a containerized capture this directory is a
    // bind mount whose parent is mounted read-only, and a mount point cannot be
    // unlinked no matter how writable its contents are.
    mkdirSync(options.stagingDir, {recursive: true});
    for (const entry of readdirSync(options.stagingDir)) {
        rmSync(join(options.stagingDir, entry), {recursive: true, force: true});
    }

    const browser = await chromium.launch();
    const results: RawResult[] = [];
    try {
        for (const walkthrough of walkthroughs) {
            results.push(await captureOne(browser, walkthrough, options));
        }
    } finally {
        await browser.close();
    }

    writeFileSync(
        join(options.stagingDir, RAW_FILENAME),
        JSON.stringify({capturedAt: new Date().toISOString(), results}, null, 2)
    );
    return results;
};

/**
 * Encode, compare against what is committed, and optionally promote. Host-side: this
 * is the half that needs the virtualenv.
 */
export const processResults = (
    raw: RawResult[],
    options: ProcessOptions
): WalkthroughResult[] => {
    const {guideDir, stagingDir} = options;
    const results = raw.map((result) => ({
        ...result,
        shots: result.shots.map((shot): ShotResult => {
            const staged = toWebp(stagedPngFor(stagingDir, shot.target));
            const committed = join(guideDir, shot.target);

            let status: ShotStatus = "new";
            let diff: CompareResult | undefined;
            if (existsSync(committed)) {
                // The diff overlay sits beside the staged shot so a reviewer can see
                // *where* it moved, not just that it did. When an overlay is written,
                // preserve the committed image beside it before promotion can replace
                // that file. The capture artifact then contains the candidate, baseline,
                // and overlay as one self-contained review set.
                const diffOut = staged.replace(/\.webp$/, ".diff.png");
                diff = compareShots(committed, staged, shot.ignore, diffOut);
                status = diff.status;
                if (diff.diffImage && existsSync(diff.diffImage)) {
                    copyFileSync(committed, staged.replace(/\.webp$/, ".baseline.webp"));
                }
            }

            if (options.promote && status !== "unchanged") {
                mkdirSync(dirname(committed), {recursive: true});
                copyFileSync(staged, committed);
            }
            return {...shot, staged, status, diff};
        }),
    }));

    writeFileSync(
        join(stagingDir, "report.json"),
        JSON.stringify({generatedAt: new Date().toISOString(), results}, null, 2)
    );
    return results;
};

/** Capture and process in one process. The default when not containerized. */
export const runWalkthroughs = async (
    walkthroughs: Walkthrough[],
    options: RunOptions
): Promise<WalkthroughResult[]> =>
    processResults(await captureWalkthroughs(walkthroughs, options), options);
