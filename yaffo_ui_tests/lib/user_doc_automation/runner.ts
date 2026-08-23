import {chromium} from "@playwright/test";
import type {Browser} from "@playwright/test";
import {randomUUID} from "crypto";
import {copyFileSync, existsSync, mkdirSync, rmSync, writeFileSync} from "fs";
import {dirname, join} from "path";
import {compareShots} from "./compare";
import type {CompareResult} from "./compare";
import type {Box} from "./framing";
import {resolveClip, resolveIgnoreRegions} from "./framing";
import {toWebp} from "./encode";
import {createObserver, PAGE_HEADER, RUN_HEADER, takeServerObservation} from "./observe";
import type {Observation} from "./observe";
import {settle} from "./settle";
import type {Walkthrough} from "./types";

/**
 * Retina. The docs site renders these at roughly half their pixel width, so a 1x
 * capture looks soft.
 */
export const DEVICE_SCALE_FACTOR = 2;

export type ShotStatus = "new" | "changed" | "unchanged";

export interface ShotResult {
    /** Path relative to docs/guide — the shot's identity. */
    target: string;
    staged: string;
    status: ShotStatus;
    width: number;
    height: number;
    ignore: Box[];
    /** Pixel comparison against the committed image. Absent for a new shot. */
    diff?: CompareResult;
}

export interface WalkthroughResult {
    page: string;
    shots: ShotResult[];
    observation: Observation;
    error?: string;
}

export interface RunOptions {
    baseUrl: string;
    /** docs/guide */
    guideDir: string;
    /** Where captures land before anything is compared. Never docs/. */
    stagingDir: string;
    /** Copy changed and new shots into the guide. */
    promote?: boolean;
}

/**
 * A page's assets always live at docs/guide/{area}/assets/{page}/. There are no
 * shared images, so a shot's destination is a pure function of the page id.
 */
const targetFor = (pageId: string, filename: string): string => {
    const [area, name] = [pageId.slice(0, pageId.indexOf("/")), pageId.slice(pageId.indexOf("/") + 1)];
    return join(area, "assets", name, filename);
};

const runOne = async (
    browser: Browser,
    walkthrough: Walkthrough,
    options: RunOptions
): Promise<WalkthroughResult> => {
    const {baseUrl, guideDir, stagingDir} = options;
    // A fresh id per run, so this walkthrough's server-side records are its own and
    // collecting them cannot disturb anything else running against the same app.
    const runId = randomUUID();
    const observer = createObserver(walkthrough.page, baseUrl);
    const context = await browser.newContext({
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

    const shots: ShotResult[] = [];
    let error: string | undefined;
    try {
        for (const [filename, shot] of Object.entries(walkthrough.shots)) {
            const page = await context.newPage();
            try {
                await page.setViewportSize(shot.viewport);
                await page.goto(`${baseUrl}${shot.goto}`, {waitUntil: "domcontentloaded"});
                await settle(page);
                if (shot.setup) {
                    await shot.setup(page);
                    await settle(page);
                }

                const clip = await resolveClip(page, shot);
                const ignore = await resolveIgnoreRegions(page, shot, clip);

                const target = targetFor(walkthrough.page, filename);
                const stagedPng = join(stagingDir, target).replace(/\.webp$/, ".png");
                mkdirSync(dirname(stagedPng), {recursive: true});
                await page.screenshot({path: stagedPng, clip, scale: "device"});
                const staged = toWebp(stagedPng);

                const committed = join(guideDir, target);
                let status: ShotStatus = "new";
                let diff: CompareResult | undefined;
                if (existsSync(committed)) {
                    // The diff overlay sits beside the staged shot so a reviewer can
                    // see *where* it moved, not just that it did.
                    diff = compareShots(committed, staged, ignore,
                        staged.replace(/\.webp$/, ".diff.png"));
                    status = diff.status;
                }

                shots.push({
                    target,
                    staged,
                    status,
                    width: Math.round(clip?.width ?? shot.viewport.width),
                    height: Math.round(clip?.height ?? shot.viewport.height),
                    ignore,
                    diff,
                });

                if (options.promote && status !== "unchanged") {
                    mkdirSync(dirname(committed), {recursive: true});
                    copyFileSync(staged, committed);
                }
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
                });
            } finally {
                await page.close();
            }
        }
    } catch (e) {
        error = e instanceof Error ? e.message : String(e);
    } finally {
        await context.close();
    }

    const server = await takeServerObservation(baseUrl, runId);
    return {page: walkthrough.page, shots, observation: observer.result(server), error};
};

export const runWalkthroughs = async (
    walkthroughs: Walkthrough[],
    options: RunOptions
): Promise<WalkthroughResult[]> => {
    rmSync(options.stagingDir, {recursive: true, force: true});
    mkdirSync(options.stagingDir, {recursive: true});

    const browser = await chromium.launch();
    const results: WalkthroughResult[] = [];
    try {
        for (const walkthrough of walkthroughs) {
            results.push(await runOne(browser, walkthrough, options));
        }
    } finally {
        await browser.close();
    }

    writeFileSync(
        join(options.stagingDir, "report.json"),
        JSON.stringify({generatedAt: new Date().toISOString(), results}, null, 2)
    );
    return results;
};
